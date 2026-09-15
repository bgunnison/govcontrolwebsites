from __future__ import annotations

import hashlib
import errno
import io
import importlib.util
import json
import posixpath
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any

try:
    import paramiko
    from scp import SCPClient
except ImportError as exc:  # pragma: no cover - exercised only on an unprepared machine
    raise RuntimeError(
        "SCP deployment requires Paramiko and scp. Run: python -m pip install -r requirements.txt"
    ) from exc


ROOT = Path(__file__).resolve().parent
MANIFEST_NAME = ".govcontrol-deploy-manifest.json"
SITE_PATH_FIELDS = {
    "governmentaicontrol.com": "AI_PATH",
    "governmentguncontrol.com": "GUN_PATH",
    "governmenthealthcarecontrol.com": "HEALTHCARE_PATH",
}


@dataclass(frozen=True)
class DeploymentResult:
    uploaded: int
    unchanged: int
    deleted: int
    initial_deploy: bool
    dry_run: bool


def load_private() -> ModuleType:
    private_path = ROOT / "private.py"
    if not private_path.is_file():
        raise RuntimeError(
            f"SSH credentials are missing. Copy {ROOT / 'private.example.py'} to {private_path} and fill it in."
        )
    spec = importlib.util.spec_from_file_location("govcontrol_private", private_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {private_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalized_remote_path(value: str) -> str:
    text = value.strip().replace("\\", "/").rstrip("/")
    path = PurePosixPath(text)
    parts = [part for part in path.parts if part != "/"]
    if not text.startswith("/") or not parts or ".." in parts or "\x00" in text:
        raise RuntimeError("remote_path must be a specific absolute SSH directory without '..'.")
    return "/" + "/".join(parts)


def parse_ssh_target(value: str) -> tuple[str, str]:
    target = value.strip()
    if target.lower().startswith("ssh "):
        target = target[4:].strip()
    if "@" not in target or any(character.isspace() for character in target):
        raise RuntimeError("SSH must use the form username@hostname.")
    username, hostname = target.rsplit("@", 1)
    if not username or not hostname:
        raise RuntimeError("SSH must use the form username@hostname.")
    return username, hostname


def deployment_settings(module: ModuleType, site_name: str) -> dict[str, Any]:
    defaults = getattr(module, "SSH_DEFAULTS", {})
    sites = getattr(module, "SSH_SITES", {})
    if defaults and not isinstance(defaults, dict):
        raise RuntimeError("SSH_DEFAULTS in private.py must be a dictionary.")
    if sites and not isinstance(sites, dict):
        raise RuntimeError("SSH_SITES in private.py must be a dictionary.")

    if isinstance(sites, dict) and site_name in sites:
        site_values = sites[site_name]
        if not isinstance(site_values, dict):
            raise RuntimeError(f"SSH_SITES[{site_name!r}] must be a dictionary.")
        settings = {**defaults, **site_values}
        target = str(settings.get("target") or "")
        username = str(settings.get("username") or "")
        hostname = str(settings.get("host") or "")
        if target:
            username, hostname = parse_ssh_target(target)
        enabled = bool(settings.get("enabled", False))
    else:
        path_field = SITE_PATH_FIELDS.get(site_name)
        if not path_field:
            raise RuntimeError(f"Unknown site: {site_name}")
        remote_path = str(getattr(module, path_field, "") or "")
        target = str(getattr(module, "SSH", "") or "")
        username, hostname = parse_ssh_target(target) if target else ("", "")
        settings = {
            "remote_path": remote_path,
            "password": str(getattr(module, "SSH_PASSWORD", "") or ""),
            "port": int(getattr(module, "SSH_PORT", 22)),
            "identity_file": str(getattr(module, "SSH_KEY_FILE", "") or ""),
            "known_hosts": str(getattr(module, "SSH_KNOWN_HOSTS", "") or ""),
            "allow_unknown_host": bool(getattr(module, "SSH_ALLOW_UNKNOWN_HOST", False)),
            "timeout": float(getattr(module, "SSH_TIMEOUT", 60)),
            "dry_run": bool(getattr(module, "SSH_DRY_RUN", False)),
            "initial_atomic": bool(getattr(module, "SSH_INITIAL_ATOMIC", True)),
        }
        enabled = bool(target and remote_path)

    settings["site_name"] = site_name
    settings["enabled"] = enabled
    settings["username"] = username
    settings["host"] = hostname
    settings["port"] = int(settings.get("port", 22))
    settings["password"] = str(settings.get("password", ""))
    settings["identity_file"] = str(settings.get("identity_file", ""))
    settings["known_hosts"] = str(settings.get("known_hosts", ""))
    settings["allow_unknown_host"] = bool(settings.get("allow_unknown_host", False))
    settings["timeout"] = float(settings.get("timeout", 60))
    settings["dry_run"] = bool(settings.get("dry_run", False))
    settings["initial_atomic"] = bool(settings.get("initial_atomic", True))

    if enabled:
        missing = [
            field
            for field in ("host", "username", "remote_path")
            if not str(settings.get(field, "")).strip()
        ]
        if missing:
            raise RuntimeError(f"SSH settings for {site_name} are missing: {', '.join(missing)}")
        settings["remote_path"] = normalized_remote_path(str(settings["remote_path"]))
    return settings


def connect(settings: dict[str, Any]) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    known_hosts = str(settings.get("known_hosts", "")).strip()
    if known_hosts:
        client.load_host_keys(str(Path(known_hosts).expanduser()))
    if settings.get("allow_unknown_host"):
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())

    identity_file = str(settings.get("identity_file", "")).strip()
    timeout = float(settings.get("timeout", 60))
    client.connect(
        hostname=str(settings["host"]),
        port=int(settings.get("port", 22)),
        username=str(settings["username"]),
        password=str(settings.get("password", "")) or None,
        key_filename=str(Path(identity_file).expanduser()) if identity_file else None,
        timeout=timeout,
        banner_timeout=timeout,
        auth_timeout=timeout,
        allow_agent=True,
        look_for_keys=True,
    )
    return client


def remote_exists(sftp: paramiko.SFTPClient, remote_path: str) -> bool:
    try:
        sftp.stat(remote_path)
        return True
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return False
        raise


def ensure_directory(sftp: paramiko.SFTPClient, remote_path: str) -> None:
    normalized = normalized_remote_path(remote_path)
    current = ""
    for part in PurePosixPath(normalized).parts:
        if part == "/":
            current = "/"
            continue
        current = posixpath.join(current, part)
        if not remote_exists(sftp, current):
            try:
                sftp.mkdir(current, mode=0o755)
            except OSError as exc:
                raise RuntimeError(
                    f"Cannot create SSH directory {current}: {exc}. "
                    "Check the absolute server path and the SSH account's directory permissions."
                ) from exc


def verify_remote_parent(sftp: paramiko.SFTPClient, remote_root: str) -> None:
    parent = posixpath.dirname(remote_root)
    try:
        info = sftp.stat(parent)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot access the website's SSH parent directory {parent}. "
            "Use the full absolute server path in private.py, not an FTP-relative path. "
            "No files were uploaded."
        ) from exc
    if not stat.S_ISDIR(info.st_mode):
        raise RuntimeError(f"The website's SSH parent path is not a directory: {parent}")


def remove_directory_tree(sftp: paramiko.SFTPClient, remote_path: str) -> None:
    if not remote_exists(sftp, remote_path):
        return
    attributes = sftp.listdir_attr(remote_path)
    for attribute in attributes:
        child = posixpath.join(remote_path, attribute.filename)
        if stat.S_ISDIR(attribute.st_mode):
            remove_directory_tree(sftp, child)
        else:
            sftp.remove(child)
    sftp.rmdir(remote_path)


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_manifest(local_root: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(local_root).as_posix(): {
            "sha256": file_digest(path),
            "size": path.stat().st_size,
        }
        for path in sorted(local_root.rglob("*"))
        if path.is_file()
    }


def read_remote_manifest(
    sftp: paramiko.SFTPClient, remote_root: str
) -> dict[str, dict[str, Any]] | None:
    manifest_path = posixpath.join(remote_root, MANIFEST_NAME)
    if not remote_exists(sftp, manifest_path):
        return None
    try:
        with sftp.open(manifest_path, "rb") as handle:
            payload = json.loads(handle.read().decode("utf-8"))
    except OSError as exc:
        raise RuntimeError(f"Could not read the remote deployment manifest: {manifest_path}") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"Remote deployment manifest is invalid: {manifest_path}") from exc
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict):
        raise RuntimeError(f"Remote deployment manifest has no file table: {manifest_path}")
    return files


def deployment_plan(
    current: dict[str, dict[str, Any]], previous: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str], list[str]]:
    changed = sorted(
        relative
        for relative, metadata in current.items()
        if previous.get(relative, {}).get("sha256") != metadata["sha256"]
    )
    unchanged = sorted(set(current) - set(changed))
    deleted = sorted(set(previous) - set(current))
    return changed, unchanged, deleted


def atomic_replace(sftp: paramiko.SFTPClient, temporary: str, destination: str) -> None:
    try:
        sftp.posix_rename(temporary, destination)
        return
    except (AttributeError, OSError):
        pass
    if remote_exists(sftp, destination):
        sftp.remove(destination)
    sftp.rename(temporary, destination)


def upload_file(
    sftp: paramiko.SFTPClient, scp: SCPClient, local_path: Path, remote_path: str
) -> None:
    ensure_directory(sftp, posixpath.dirname(remote_path))
    temporary = f"{remote_path}.uploading"
    if remote_exists(sftp, temporary):
        sftp.remove(temporary)
    # File contents travel over SCP; SFTP is only used for metadata/remote file operations.
    scp.put(str(local_path), remote_path=temporary)
    if sftp.stat(temporary).st_size != local_path.stat().st_size:
        raise RuntimeError(f"SCP upload size mismatch: {temporary}; live file was not replaced.")
    sftp.chmod(temporary, 0o644)
    atomic_replace(sftp, temporary, remote_path)


def write_remote_manifest(
    sftp: paramiko.SFTPClient,
    scp: SCPClient,
    remote_root: str,
    manifest: dict[str, dict[str, Any]],
) -> None:
    payload = json.dumps({"version": 1, "files": manifest}, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    destination = posixpath.join(remote_root, MANIFEST_NAME)
    temporary = f"{destination}.uploading"
    if remote_exists(sftp, temporary):
        sftp.remove(temporary)
    with io.BytesIO(payload) as source:
        scp.putfo(source, remote_path=temporary, mode="0600", size=len(payload))
    if sftp.stat(temporary).st_size != len(payload):
        raise RuntimeError(f"SCP manifest upload size mismatch: {temporary}")
    sftp.chmod(temporary, 0o600)
    atomic_replace(sftp, temporary, destination)


def remove_managed_file(sftp: paramiko.SFTPClient, remote_root: str, relative: str) -> None:
    remote_file = posixpath.join(remote_root, relative)
    if remote_exists(sftp, remote_file):
        sftp.remove(remote_file)
    parent = posixpath.dirname(remote_file)
    while parent != remote_root and parent.startswith(f"{remote_root}/"):
        try:
            sftp.rmdir(parent)
        except OSError:
            break
        parent = posixpath.dirname(parent)


def initial_atomic_deploy(
    sftp: paramiko.SFTPClient,
    scp: SCPClient,
    local_root: Path,
    remote_root: str,
    manifest: dict[str, dict[str, Any]],
) -> None:
    staging_path = f"{remote_root}.next"
    previous_path = f"{remote_root}.previous"
    remove_directory_tree(sftp, staging_path)
    ensure_directory(sftp, staging_path)
    for index, relative in enumerate(sorted(manifest), start=1):
        upload_file(sftp, scp, local_root / Path(relative), posixpath.join(staging_path, relative))
        if index % 50 == 0 or index == len(manifest):
            print(f"  uploaded {index}/{len(manifest)} files", flush=True)
    write_remote_manifest(sftp, scp, staging_path, manifest)
    remove_directory_tree(sftp, previous_path)
    moved_current = False
    if remote_exists(sftp, remote_root):
        sftp.rename(remote_root, previous_path)
        moved_current = True
    try:
        sftp.rename(staging_path, remote_root)
    except Exception:
        if moved_current and not remote_exists(sftp, remote_root):
            sftp.rename(previous_path, remote_root)
        raise


def deploy_directory(local_dist: Path, settings: dict[str, Any]) -> DeploymentResult:
    if not settings.get("enabled"):
        raise RuntimeError(f"SSH deployment is disabled for {settings.get('site_name', local_dist.parent.name)}.")
    if not (local_dist / "index.html").is_file():
        raise RuntimeError(f"Build output is missing or incomplete: {local_dist}")

    remote_root = normalized_remote_path(str(settings["remote_path"]))
    manifest = local_manifest(local_dist)
    with ExitStack() as cleanup:
        client = connect(settings)
        cleanup.callback(client.close)
        sftp = client.open_sftp()
        cleanup.callback(sftp.close)
        verify_remote_parent(sftp, remote_root)
        previous = read_remote_manifest(sftp, remote_root)
        initial = previous is None
        if not settings.get("dry_run"):
            scp = SCPClient(client.get_transport(), socket_timeout=float(settings.get("timeout", 60)))
            cleanup.callback(scp.close)
        if initial:
            if settings.get("dry_run"):
                return DeploymentResult(len(manifest), 0, 0, True, True)
            if settings.get("initial_atomic", True):
                initial_atomic_deploy(sftp, scp, local_dist, remote_root, manifest)
            else:
                ensure_directory(sftp, remote_root)
                for index, relative in enumerate(sorted(manifest), start=1):
                    upload_file(sftp, scp, local_dist / Path(relative), posixpath.join(remote_root, relative))
                    if index % 50 == 0 or index == len(manifest):
                        print(f"  uploaded {index}/{len(manifest)} files", flush=True)
                write_remote_manifest(sftp, scp, remote_root, manifest)
            return DeploymentResult(len(manifest), 0, 0, True, False)

        changed, unchanged, deleted = deployment_plan(manifest, previous)
        if settings.get("dry_run"):
            return DeploymentResult(len(changed), len(unchanged), len(deleted), False, True)

        for index, relative in enumerate(changed, start=1):
            upload_file(sftp, scp, local_dist / Path(relative), posixpath.join(remote_root, relative))
            if index % 50 == 0 or index == len(changed):
                print(f"  uploaded {index}/{len(changed)} changed files", flush=True)
        for relative in deleted:
            remove_managed_file(sftp, remote_root, relative)
        if changed or deleted:
            write_remote_manifest(sftp, scp, remote_root, manifest)
        return DeploymentResult(len(changed), len(unchanged), len(deleted), False, False)
