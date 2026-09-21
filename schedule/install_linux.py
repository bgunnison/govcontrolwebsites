"""Install a Sunday 22:00 cron entry on the server, preserving existing jobs."""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from weekly_run import alert_recipient

BEGIN = "# BEGIN GOVCONTROL WEEKLY"
END = "# END GOVCONTROL WEEKLY"
NATIVE_CRONTAB = Path("/usr/bin/crontab")


def read_crontab(command: str) -> str:
    result = subprocess.run(
        [command, "-l"], capture_output=True, text=True, check=False, timeout=30,
    )
    if result.returncode == 0:
        return result.stdout
    if result.returncode == 1 and result.stderr.strip().lower().startswith("no crontab for "):
        return ""
    raise RuntimeError(
        f"Cron access check failed for {command}: {result.stderr.strip() or 'unknown error'}. "
        "Ask the hosting provider to restore this account's cron access."
    )


def check_native_crontab(expected: str | None = None) -> None:
    # A jailed-shell wrapper can accept a file even when the real cron service
    # denies this account. Verify both permission and the installed entry there.
    selected = shutil.which("crontab")
    if not selected:
        raise RuntimeError("crontab is not installed.")
    if NATIVE_CRONTAB.is_file() and NATIVE_CRONTAB.resolve() != Path(selected).resolve():
        actual = read_crontab(str(NATIVE_CRONTAB))
        if expected is not None and actual.strip() != expected.strip():
            raise RuntimeError(
                "The native cron service did not retain the requested schedule. "
                "Ask the hosting provider to investigate before relying on automatic runs."
            )


def cron_text(existing: str, recipient: str, root: Path, python: Path) -> str:
    lines = existing.splitlines()
    starts, ends = lines.count(BEGIN), lines.count(END)
    if starts != ends or starts > 1:
        raise ValueError("The existing GovControl cron block needs manual review.")
    if starts:
        first, last = lines.index(BEGIN), lines.index(END)
        if first > last:
            raise ValueError("The existing GovControl cron block is malformed.")
        del lines[first:last + 1]
    while lines and not lines[-1].strip():
        lines.pop()
    for value in (str(root), str(python), recipient):
        if any(character in value for character in "\r\n"):
            raise ValueError("Cron settings must not contain line breaks.")
    command = (
        f"cd {shlex.quote(str(root))} && umask 077 && "
        f"PYTHONUTF8=1 PYTHONIOENCODING=utf-8 {shlex.quote(str(python))} "
        f"{shlex.quote(str(root / 'weekly_run.py'))} --scheduled"
    ).replace("%", r"\%")
    # Append last so the MAILTO setting does not change any existing jobs.
    block = [BEGIN, f'MAILTO="{recipient}"', f"0 22 * * 0 {command}", END]
    return "\n".join(lines + ([""] if lines else []) + block) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="Write the cron entry; otherwise only show it.")
    args = parser.parse_args()
    if os.name != "posix" or sys.version_info < (3, 11):
        raise RuntimeError("Run this installer on Linux with the private Python 3.11+ environment.")
    if not (shutil.which("mail") or shutil.which("mailx")):
        raise RuntimeError("mail or mailx is required for failure notifications.")
    for path in (ROOT, ROOT / "private.py"):
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise RuntimeError("The project directory and private.py must be restricted to the account owner.")
    recipient = alert_recipient()
    check_native_crontab()
    existing = read_crontab("crontab")
    # Do not resolve the virtualenv symlink: its location selects its packages.
    updated = cron_text(existing, recipient, ROOT.resolve(), Path(sys.executable).absolute())
    if not args.install:
        print(updated, end="")
        return
    os.umask(0o077)
    backup_dir = ROOT / "logs"
    backup_dir.mkdir(exist_ok=True)
    backup = backup_dir / ("crontab-before-" + datetime.now().strftime("%Y%m%dT%H%M%S%f") + ".txt")
    backup.write_text(existing, encoding="utf-8")
    subprocess.run(["crontab", "-"], input=updated, text=True, check=True)
    installed = read_crontab("crontab")
    if installed.strip() != updated.strip():
        raise RuntimeError("Cron verification did not match the requested schedule.")
    check_native_crontab(updated)
    print("Cron entry installed and read back: Sunday 10 PM server local time. Execution has not been tested.")


if __name__ == "__main__":
    main()
