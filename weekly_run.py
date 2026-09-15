"""Once-weekly update, build and test -> SCP deployment on Windows or Linux."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from redaction import configured_secrets, redact_text

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs" / "weekly"
TASK_NAME = "GovControl Weekly Update and Deploy"
ERROR_EVENTS = {
    "research_error", "summary_error", "quota_error", "rate_limit_deferred",
    "cost_limit_deferred", "research_failed", "summary_failed",
}
STEPS = (
    ("Update, build and test", "update_all.py", ()),
    ("Deploy verified websites", "deploy_all.py", ()),
)
STEP_TIMEOUT_SECONDS = 3 * 60 * 60
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
IS_WINDOWS = os.name == "nt"


class AlreadyRunningError(RuntimeError):
    pass


def now_stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def weekly_period(now: datetime) -> str:
    # The schedule and the weekly boundary both use the machine's local time.
    sunday = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(
        hour=22, minute=0, second=0, microsecond=0
    )
    if now < sunday:
        sunday -= timedelta(days=7)
    return sunday.strftime("%Y-%m-%d")


def write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(safe_text(json.dumps(payload, indent=2)) + "\n", encoding="utf-8")
    temporary.replace(path)


def python_executable() -> str:
    executable = Path(sys.executable)
    return str(executable.with_name("python.exe")) if executable.name.lower() == "pythonw.exe" else str(executable)


@contextmanager
def run_lock(path: Path):
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if IS_WINDOWS:
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AlreadyRunningError("Another scheduled run holds the lock.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if IS_WINDOWS:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def safe_text(text: str) -> str:
    return redact_text(text, configured_secrets(ROOT))


def log_line(log, message: str) -> None:
    log.write(safe_text(f"[{now_stamp()}] {message}\n").encode("utf-8"))
    log.flush()


def run_step(name: str, script: str, arguments: tuple[str, ...], log) -> None:
    log_line(log, f"START: {name}")
    output_start = log.tell()
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    command = [python_executable(), "-u", str(ROOT / script), *arguments]
    process = subprocess.Popen(
        command, cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=NO_WINDOW, start_new_session=not IS_WINDOWS,
    )
    read_errors: list[Exception] = []

    def copy_output() -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                log.write(safe_text(line.decode("utf-8", errors="replace")).encode("utf-8"))
                log.flush()
        except Exception as exc:
            read_errors.append(exc)

    reader = threading.Thread(target=copy_output, daemon=True)
    reader.start()
    try:
        result = process.wait(timeout=STEP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        # Stop this exact child process tree, including updater grandchildren.
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=NO_WINDOW, check=True,
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=30)
        raise RuntimeError(f"{name} exceeded its 3-hour limit and its process tree was stopped.")
    finally:
        reader.join(timeout=10)
        if not reader.is_alive() and process.stdout is not None:
            process.stdout.close()
    if reader.is_alive() or read_errors:
        raise RuntimeError(f"Could not finish recording output from {name}.")
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
                    raise RuntimeError(f"Update reported {event['event']}; deployment was not started.")
    log_line(log, f"PASS: {name}")


def notify_failure(log_path: Path, *, resolved: bool = False) -> None:
    if not IS_WINDOWS:
        if not resolved:
            send_email(log_path)
        return
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
         "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "schedule/notify_failure.ps1"),
         "-LogPath", str(log_path), *(["-Resolved"] if resolved else [])],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=NO_WINDOW, timeout=30, check=True,
    )


def alert_recipient() -> str:
    path = ROOT / "private.py"
    spec = importlib.util.spec_from_file_location("govcontrol_alert_private", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Private notification configuration could not be loaded.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    recipient = str(getattr(module, "WEEKLY_ALERT_EMAIL", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]*@[A-Za-z0-9][A-Za-z0-9.\-]*\.[A-Za-z]{2,}", recipient):
        raise RuntimeError("Set a valid WEEKLY_ALERT_EMAIL in private.py.")
    return recipient


def send_email(log_path: Path | None = None, *, test: bool = False) -> None:
    mail = shutil.which("mail") or shutil.which("mailx")
    if not mail:
        raise RuntimeError("A working mail or mailx command is required for failure alerts.")
    recipient = alert_recipient()
    state_path = LOG_DIR / "latest-status.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if not test and state_path.exists() else {}
    subject = "GovControl weekly automation: " + ("test alert" if test else "FAILED")
    body = (
        "This is a test of GovControl's failure email notifications.\n" if test else
        f"The weekly run failed.\nStep: {state.get('step', 'startup')}\n"
        f"Error: {state.get('error', 'See the private server log.')}\n"
    )
    body += f"Time: {now_stamp()}\nSchedule: Sunday at 10 PM server local time.\n"
    if log_path is not None:
        body += f"Private server log: {log_path}\n"
    if not test:
        body += "No automatic retry will run this week. Review the log before retrying.\n"
    subprocess.run(
        [mail, "-s", subject, recipient], input=safe_text(body).encode("utf-8"),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30, check=True,
    )


def publish_status(state: dict) -> None:
    write_json(LOG_DIR / "latest-status.json", state)
    text = (
        f"GOVCONTROL WEEKLY AUTOMATION: {state['status'].upper()}\n"
        f"Updated: {now_stamp()}\n"
        "Schedule: Sunday 10:00 PM machine local time\n"
        f"Step: {state.get('step', 'not started')}\n"
        f"Log: {state['log']}\n"
        f"Details: {state.get('error', '')}\n\n"
        "See logs/weekly/latest-status.json and the private log for details.\n"
        "If deployment failed, some sites/files may already have been uploaded; review the log.\n"
    )
    (ROOT / "WEEKLY_STATUS.txt").write_text(safe_text(text), encoding="utf-8")


def execute_run(*, dry_run: bool = False) -> int:
    if not IS_WINDOWS:
        os.umask(0o077)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f") + "-" + uuid4().hex[:8]
    log_path = LOG_DIR / (("dry-run_" if dry_run else "") + stamp + ".log")
    with log_path.open("ab") as log:
        if dry_run:
            log_line(log, "DRY RUN: no API calls, server connections or deployments.")
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
                log_line(log, "Weekly run started. Only a successful update, build and test permit deployment.")
                for name, script, arguments in STEPS:
                    state["step"] = name
                    publish_status(state)
                    run_step(name, script, arguments, log)
                    state["steps"].append({"name": name, "status": "passed"})
                state["status"] = "success"
                state["finished_at"] = now_stamp()
                publish_status(state)
                write_json(log_path.with_suffix(".json"), state)
                log_line(log, "SUCCESS: updates, tests and deployment completed.")
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
            state["error"] = safe_text(str(exc))
            state["finished_at"] = now_stamp()
            log_line(log, f"FAILED: {exc}")
            log.write(safe_text(traceback.format_exc()).encode("utf-8"))
            log.flush()
            publish_status(state)
            write_json(log_path.with_suffix(".json"), state)
            try:
                notify_failure(log_path)
            except Exception as notification_error:
                log_line(log, f"Failure notification unavailable: {notification_error}. See WEEKLY_STATUS.txt.")
                print("GovControl failed and its notification could not be sent. See the private weekly log.", file=sys.stderr)
            return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Once-weekly update, test and SCP deployment.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scheduled", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--test-email", action="store_true", help="Send a test email without running any update or deployment.")
    args = parser.parse_args()
    if args.test_email:
        send_email(test=True)
        print("Test email accepted by the server mail command.")
        return 0
    return execute_run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
