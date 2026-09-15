"""Audit the Git index without printing credential values or opening private.py."""
from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRECTORIES = {
    ".ssh", ".venv", "venv", "__pycache__", "dist", "logs", "backups",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "posts", "imported_media",
}
EXCLUDED_FILES = (
    "private*.py", ".env", ".env.*", "id_rsa*", "id_ed25519*", "id_ecdsa*",
    "id_dsa*", "*.pem", "*.key", "*.p12", "*.pfx", "*.pyc",
    "WEEKLY_STATUS.txt", "*.log", "*.uploading", "*.tmp", "wordpress-import.json",
)
SECRET_PATTERNS = (
    r"(^|[^[:alnum:]_])sk-(proj-)?[[:alnum:]_-]{25,}",
    r"(^|[^[:alnum:]_])gh[pousr]_[[:alnum:]]{25,}",
    r"github_pat_[[:alnum:]_]{30,}",
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
)
MAX_FILE_BYTES = 50 * 1024 * 1024


def git(*args: str, input_data: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, input=input_data, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True,
    )
    return result.stdout


def forbidden_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if any(part.lower() in EXCLUDED_DIRECTORIES for part in parts[:-1]):
        return True
    name = parts[-1]
    if name in {"private.example.py", ".env.example"}:
        return False
    return any(fnmatch.fnmatchcase(name.lower(), pattern.lower()) for pattern in EXCLUDED_FILES)


def main() -> int:
    entries = git("ls-files", "--stage", "-z").split(b"\0")
    blobs: dict[str, list[str]] = {}
    errors = []
    file_count = 0
    for entry in filter(None, entries):
        metadata, raw_path = entry.split(b"\t", 1)
        mode, object_id, stage = metadata.decode("ascii").split()
        path = raw_path.decode("utf-8")
        file_count += 1
        if forbidden_path(path):
            errors.append(f"Private/generated file is staged: {path}")
        if mode == "160000":
            errors.append(f"Unexpected nested repository/submodule: {path}")
        if stage != "0":
            errors.append(f"Unresolved merge entry: {path}")
        blobs.setdefault(object_id, []).append(path)
    if not file_count:
        print("No staged/tracked files to audit. Stage the intended source files first.")
        return 1

    sizes = git(
        "cat-file", "--batch-check=%(objectname) %(objectsize)",
        input_data=("\n".join(blobs) + "\n").encode("ascii"),
    )
    total_bytes = 0
    for line in sizes.decode("ascii").splitlines():
        object_id, size = line.split()
        total_bytes += int(size) * len(blobs[object_id])
        if int(size) > MAX_FILE_BYTES:
            errors.extend(f"File exceeds the 50 MiB review threshold: {path}" for path in blobs[object_id])

    for pattern in SECRET_PATTERNS:
        result = subprocess.run(
            ["git", "grep", "--cached", "-I", "-l", "-z", "-E", "-e", pattern, "--", "."],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if result.returncode not in (0, 1):
            raise RuntimeError("The staged credential scan could not complete.")
        for raw_path in filter(None, result.stdout.split(b"\0")):
            errors.append(f"Possible credential signature (value withheld): {raw_path.decode('utf-8')}")

    if errors:
        print("\n".join(sorted(set(errors))))
        return 1
    print(
        f"Repository audit passed: {file_count} staged/tracked files, "
        f"{total_bytes / 1024 / 1024:.1f} MiB; no forbidden paths, common credential "
        "signatures, or oversized files detected."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Repository audit failed: {type(exc).__name__}. Check Git and the staged file list.", file=sys.stderr)
        raise SystemExit(1)
