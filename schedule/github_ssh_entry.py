"""Forced SSH command for a scheduler key; never execute client-supplied code."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import weekly_run as weekly

COMMANDS = {"check", "status", "run"}


def public_status() -> dict:
    """Return only fixed status labels and dates; logs and content stay private."""
    path = ROOT / "logs/weekly/latest-status.json"
    if not path.exists():
        return {"status": "not_run"}
    state = json.loads(path.read_text(encoding="utf-8"))
    result = {"status": state.get("status") if state.get("status") in
              {"running", "success", "failed"} else "unknown"}
    for field in ("period", "started_at", "finished_at"):
        value = state.get(field)
        if isinstance(value, str) and re.fullmatch(r"[0-9T:+.\-]+", value):
            datetime.fromisoformat(value)
            result[field] = value
    return result


def preflight() -> None:
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or newer is required.")
    for name in ("private.py", "weekly_run.py", "update_all.py", "deploy_all.py"):
        if not (ROOT / name).is_file():
            raise RuntimeError("Missing required server file.")
    if os.name != "nt" and (ROOT / "private.py").stat().st_mode & 0o077:
        raise RuntimeError("Private configuration permissions are too broad.")


def main(command: str | None = None) -> int:
    requested = os.environ.get("SSH_ORIGINAL_COMMAND", "") if command is None else command
    if requested not in COMMANDS:
        print("Only check, status, and run are permitted.", file=sys.stderr)
        return 64
    os.umask(0o077)
    try:
        os.chdir(ROOT)
        period = weekly.weekly_period(datetime.now())
        if requested == "status":
            print(json.dumps({"result": "status", "current_period": period,
                              "latest": public_status()}))
            return 0
        preflight()
        log_dir = ROOT / "logs/weekly"
        log_dir.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        with (log_dir / "ssh-entry.log").open("ab") as log:
            result = subprocess.run(
                [sys.executable, str(ROOT / "weekly_run.py"),
                 "--dry-run" if requested == "check" else "--scheduled"],
                cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, check=False,
            )
        state = public_status()
        success = result.returncode == 0 and (requested == "check" or (
            state.get("status") == "success" and state.get("period") == period
        ))
        print(json.dumps({"result": ("check_passed" if requested == "check" else "run_succeeded")
                          if success else "failed", "current_period": period, "latest": state}))
        return 0 if success else 1
    except Exception:
        # Never send an exception containing paths/configuration to the SSH client.
        print(json.dumps({"result": "entry_failed", "message": "Inspect the private server installation and logs."}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
