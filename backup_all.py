from __future__ import annotations

import argparse
import json
import posixpath
import re
import stat
import sys
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from scp import SCPClient

from ssh_deploy import (
    SITE_PATH_FIELDS, connect, deployment_settings, load_private,
    local_manifest, normalized_remote_path,
)

ROOT = Path(__file__).resolve().parent
BACKUPS_DIR = ROOT / "backups"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_report(path: Path, payload: dict[str, Any]) -> None:
    # Each run owns a new directory. Never overwrite an earlier backup.
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def remote_inventory(sftp: Any, remote_root: str) -> dict[str, dict[str, Any]]:
    """Inventory hidden files too; do not follow links outside the selected website."""
    inventory = {}
    seen_names: set[str] = set()
    pending = [("", remote_root)]
    while pending:
        prefix, remote_dir = pending.pop()
        for item in sftp.listdir_attr(remote_dir):
            name = item.filename
            if (
                name in ("", ".", "..") or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
                or name.endswith((" ", "."))
                or name.split(".")[0].upper() in {
                    "CON", "PRN", "AUX", "NUL",
                    *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
                }
            ):
                raise RuntimeError(f"Server filename cannot be copied safely to Windows: {name!r}")
            relative = posixpath.join(prefix, name)
            if relative.casefold() in seen_names:
                raise RuntimeError(f"Server filenames collide on Windows: {relative}")
            seen_names.add(relative.casefold())
            mode = item.st_mode
            if stat.S_ISDIR(mode):
                kind = "directory"
                pending.append((relative, posixpath.join(remote_dir, name)))
            elif stat.S_ISREG(mode):
                kind = "file"
            else:
                raise RuntimeError(
                    f"Unsupported server entry: {relative} (symbolic link or special file). "
                    "Backup stopped instead of silently skipping it or following it outside the site."
                )
            inventory[relative] = {
                "kind": kind, "mode": stat.S_IMODE(mode),
                "size": item.st_size if kind == "file" else 0,
                "mtime": item.st_mtime if kind == "file" else None,
            }
    return inventory


def backup_site(settings: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    site = settings["site_name"]
    if site not in SITE_PATH_FIELDS:
        raise ValueError(f"Unknown website: {site}")
    remote_root = normalized_remote_path(str(settings["remote_path"]))
    destination = run_dir / site
    partial = run_dir / (site + ".partial")
    if destination.exists() or partial.exists():
        raise RuntimeError(f"Refusing to overwrite an existing backup for {site}")

    with ExitStack() as cleanup:
        client = connect(settings)
        cleanup.callback(client.close)
        sftp = client.open_sftp()
        cleanup.callback(sftp.close)
        if not stat.S_ISDIR(sftp.lstat(remote_root).st_mode):
            raise RuntimeError(f"Backup source must be a real directory, not a symbolic link: {remote_root}")
        expected = remote_inventory(sftp, remote_root)
        file_count = sum(item["kind"] == "file" for item in expected.values())
        print(f"[{site}] downloading {file_count} files with SCP", flush=True)
        completed = 0
        last_report = time.monotonic()

        def progress(filename, size, sent):
            nonlocal completed, last_report
            if sent == size:
                completed += 1
            now = time.monotonic()
            if now - last_report >= 10:
                print(f"[{site}] received {completed}/{file_count} files", flush=True)
                last_report = now

        scp = SCPClient(
            client.get_transport(), socket_timeout=float(settings.get("timeout", 60)),
            progress=progress,
        )
        cleanup.callback(scp.close)
        # Passing the directory itself (not a '*' glob) includes dotfiles.
        # The nonexistent destination renames the received root to .partial.
        scp.get(remote_root, local_path=str(partial), recursive=True, preserve_times=True)
        if not partial.is_dir():
            raise RuntimeError("SCP did not return a website directory.")
        files = local_manifest(partial)
        expected_files = {name: item for name, item in expected.items() if item["kind"] == "file"}
        actual_dirs = {path.relative_to(partial).as_posix() for path in partial.rglob("*") if path.is_dir()}
        expected_dirs = {name for name, item in expected.items() if item["kind"] == "directory"}
        if set(files) != set(expected_files) or actual_dirs != expected_dirs:
            raise RuntimeError("Downloaded file/directory list differs from the server; partial backup retained.")
        if any(files[name]["size"] != item["size"] for name, item in expected_files.items()):
            raise RuntimeError("Downloaded file sizes differ from the server; partial backup retained.")
        if remote_inventory(sftp, remote_root) != expected:
            raise RuntimeError("Server files changed during the download; partial backup retained. Try again.")

    manifest_name = f"{site}.manifest.json"
    write_report(run_dir / manifest_name, {
        "site": site, "remote_path": remote_root, "downloaded_at": utc_now(),
        "remote_entries": expected, "local_files": files,
    })
    # Both exact paths are new children of this run; no prior backup is moved/deleted.
    partial.rename(destination)
    return {
        "status": "complete", "directory": site, "manifest": manifest_name,
        "files": len(files), "bytes": sum(item["size"] for item in files.values()),
    }


def run_backups(selected: list[dict[str, Any]], backup_root: Path = BACKUPS_DIR) -> tuple[Path, bool]:
    backup_root.mkdir(parents=True, exist_ok=True)
    run_dir = backup_root / (datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f") + "-" + uuid4().hex[:8])
    run_dir.mkdir(exist_ok=False)
    report_path = run_dir / "backup.json"
    report: dict[str, Any] = {
        "started_at": utc_now(), "status": "running", "sites": {},
        "scope": "Website directory files only; no database export.",
    }
    write_report(report_path, report)
    print(f"Backup folder: {run_dir}", flush=True)
    try:
        for settings in selected:
            site = settings["site_name"]
            report["sites"][site] = {"status": "running"}
            write_report(report_path, report)
            try:
                result = backup_site(settings, run_dir)
                print(f"[{site}] saved {result['files']} files ({result['bytes']:,} bytes)", flush=True)
                report["sites"][site] = result
            except Exception as exc:
                # Keep completed sites and any .partial downloads; attempt remaining sites.
                report["sites"][site] = {"status": "failed", "error": str(exc)}
                print(f"[{site}] backup failed: {exc}", file=sys.stderr, flush=True)
            write_report(report_path, report)
        success = all(item["status"] == "complete" for item in report["sites"].values())
        report["status"] = "complete" if success else "incomplete"
        return run_dir, success
    finally:
        if report["status"] == "running":
            report["status"] = "interrupted"
        report["finished_at"] = utc_now()
        write_report(report_path, report)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download website files with SCP into timestamped local backups.")
    parser.add_argument("--site", choices=tuple(SITE_PATH_FIELDS), help="Back up only one configured website.")
    parser.add_argument("--no-pause", action="store_true", help="Tell backup.bat not to pause before closing.")
    args = parser.parse_args()
    private = load_private()
    selected = []
    for site in ((args.site,) if args.site else SITE_PATH_FIELDS):
        settings = deployment_settings(private, site)
        if settings["enabled"]:
            selected.append(settings)
        elif args.site:
            raise RuntimeError(f"SSH is not enabled/configured for {site} in private.py.")
        else:
            print(f"[{site}] SSH disabled or unconfigured; skipping.")
    if not selected:
        raise RuntimeError("No SSH sites are enabled/configured in private.py.")
    run_dir, success = run_backups(selected)
    print(f"Backup {'complete' if success else 'INCOMPLETE'}: {run_dir}", flush=True)
    print("Server files were not modified. WordPress databases are not included.")
    return 0 if success else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nBackup interrupted. Completed sites and partial downloads were kept.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
