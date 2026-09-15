"""Windows-scheduled weekly update -> verified backup -> SCP deployment."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs" / "weekly"
TASK_NAME = "GovControl Weekly Update and Deploy"
ERROR_EVENTS = {
    "research_error", "summary_error", "quota_error", "rate_limit_deferred",
    "cost_limit_deferred", "research_failed", "summary_failed",
}
STEPS = (
    ("Update, build and test", "update_all.py", ()),
    ("Back up live websites", "backup_all.py", ("--no-pause",)),
    ("Deploy verified websites", "deploy_all.py", ()),
)
STEP_TIMEOUT_SECONDS = 3 * 60 * 60
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class AlreadyRunningError(RuntimeError):
    pass


def now_stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def weekly_period(now: datetime) -> str:
    # Installer verifies that Windows uses Pacific time, including DST.
    sunday = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(
        hour=22, minute=0, second=0, microsecond=0
    )
    if now < sunday:
        sunday -= timedelta(days=7)
    return sunday.strftime("%Y-%m-%d")


def write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def python_executable() -> str:
    executable = Path(sys.executable)
    return str(executable.with_name("python.exe")) if executable.name.lower() == "pythonw.exe" else str(executable)


@contextmanager
def run_lock(path: Path):
    import msvcrt
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise AlreadyRunningError("Another scheduled run holds the lock.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def log_line(log, message: str) -> None:
    log.write(f"[{now_stamp()}] {message}\n".encode("utf-8"))
    log.flush()


def run_step(name: str, script: str, arguments: tuple[str, ...], log) -> None:
    log_line(log, f"START: {name}")
    output_start = log.tell()
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    command = [python_executable(), "-u", str(ROOT / script), *arguments]
    process = subprocess.Popen(
        command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT,
        creationflags=NO_WINDOW,
    )
    try:
        result = process.wait(timeout=STEP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        # Stop this exact child process tree, including updater grandchildren.
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW, check=True,
        )
        process.wait(timeout=30)
        raise RuntimeError(f"{name} exceeded its 3-hour limit and its process tree was stopped.")
    log.flush()
    if result:
        raise RuntimeError(f"{name} failed with exit code {result}.")
    if script == "update_all.py":
        # A topic error must stop unattended deployment even if an updater exits 0
        # after saving other successful topics.
        with Path(log.name).open("rb") as output:
            output.seek(output_start)
            for raw_line in output:
                try:
                    event = json.loads(raw_line.decode("utf-8", errors="replace"))
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(event, dict) and event.get("event") in ERROR_EVENTS:
                    raise RuntimeError(f"Update reported {event['event']}; backup/deployment were not started.")
    log_line(log, f"PASS: {name}")


def notify_failure(log_path: Path, *, resolved: bool = False) -> None:
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
         "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "schedule/notify_failure.ps1"),
         "-LogPath", str(log_path), *(["-Resolved"] if resolved else [])],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=NO_WINDOW, timeout=30, check=True,
    )


def publish_status(state: dict) -> None:
    write_json(LOG_DIR / "latest-status.json", state)
    text = (
        f"GOVCONTROL WEEKLY AUTOMATION: {state['status'].upper()}\n"
        f"Updated: {now_stamp()}\n"
        "Schedule: Sunday 10:00 PM Pacific (Windows sign-in required)\n"
        f"Step: {state.get('step', 'not started')}\n"
        f"Log: {state['log']}\n"
        f"Details: {state.get('error', '')}\n\n"
        "Open weekly_status.bat for schedule details and recent log output.\n"
        "If deployment failed, some sites/files may already have been uploaded; review the log.\n"
    )
    (ROOT / "WEEKLY_STATUS.txt").write_text(text, encoding="utf-8")


def execute_run(*, dry_run: bool = False) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f") + "-" + uuid4().hex[:8]
    log_path = LOG_DIR / (("dry-run_" if dry_run else "") + stamp + ".log")
    with log_path.open("ab") as log:
        if dry_run:
            log_line(log, "DRY RUN: no API calls, server connections, backups or deployments.")
            for name, script, arguments in STEPS:
                log_line(log, f"Would run {name}: {script} {' '.join(arguments)}")
            return 0
        state = {
            "status": "running", "started_at": now_stamp(), "pid": os.getpid(),
            "period": weekly_period(datetime.now()), "log": str(log_path), "steps": [],
        }
        try:
            with run_lock(LOG_DIR / "runner.lock"):
                attempt_path = LOG_DIR / "last-attempt.json"
                if attempt_path.exists():
                    previous = json.loads(attempt_path.read_text(encoding="utf-8"))
                    if previous["period"] == state["period"]:
                        log_line(log, "SKIPPED: this weekly period was already attempted; no automatic retry.")
                        return 0
                # Record the attempt BEFORE any paid work. Failed runs are not retried automatically.
                write_json(attempt_path, {"period": state["period"], "started_at": state["started_at"], "log": str(log_path)})
                publish_status(state)
                log_line(log, "Weekly run started. Only a successful update/test and verified backup permit deployment.")
                for name, script, arguments in STEPS:
                    state["step"] = name
                    publish_status(state)
                    run_step(name, script, arguments, log)
                    state["steps"].append({"name": name, "status": "passed"})
                state["status"] = "success"
                state["finished_at"] = now_stamp()
                publish_status(state)
                write_json(log_path.with_suffix(".json"), state)
                log_line(log, "SUCCESS: updates, tests, backups and deployment completed.")
                try:
                    notify_failure(log_path, resolved=True)
                except Exception as notice_error:
                    log_line(log, f"Could not resolve an earlier desktop failure notice: {notice_error}")
                return 0
        except AlreadyRunningError:
            log_line(log, "SKIPPED: another weekly run is active.")
            return 0
        except Exception as exc:
            state["status"] = "failed"
            state["error"] = str(exc)
            state["finished_at"] = now_stamp()
            log_line(log, f"FAILED: {exc}")
            log.write(traceback.format_exc().encode("utf-8"))
            log.flush()
            publish_status(state)
            write_json(log_path.with_suffix(".json"), state)
            try:
                notify_failure(log_path)
            except Exception as notification_error:
                log_line(log, f"Windows notification unavailable: {notification_error}. See WEEKLY_STATUS.txt.")
            return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Once-weekly update, test, backup and SCP deployment.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scheduled", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return execute_run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
