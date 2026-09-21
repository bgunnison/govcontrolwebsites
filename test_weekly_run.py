from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path, PurePosixPath
from unittest.mock import patch

import weekly_run as weekly
from schedule import install_linux as linux_schedule
from schedule.install_linux import cron_text


class WeeklyRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        self.logs = self.root / "logs" / "weekly"
        self.enterContext(patch.object(weekly, "ROOT", self.root))
        self.enterContext(patch.object(weekly, "LOG_DIR", self.logs))
        self.lock = self.enterContext(patch.object(weekly, "run_lock", side_effect=lambda path: nullcontext()))
        self.notify = self.enterContext(patch.object(weekly, "notify_failure"))

    def state(self):
        return json.loads((self.logs / "latest-status.json").read_text())

    def test_weekly_boundary_is_sunday_at_10_pm(self):
        self.assertEqual(weekly.weekly_period(datetime(2026, 9, 20, 21, 59)), "2026-09-13")
        self.assertEqual(weekly.weekly_period(datetime(2026, 9, 20, 22)), "2026-09-20")
        self.assertEqual(weekly.weekly_period(datetime(2026, 9, 21, 10)), "2026-09-20")
        self.assertEqual(weekly.weekly_period(datetime(2026, 11, 1, 22)), "2026-11-01")

    def test_success_requires_update_then_deploy_without_backup(self):
        with patch.object(weekly, "run_step") as step:
            self.assertEqual(weekly.execute_run(), 0)
        self.assertEqual([call.args[1] for call in step.call_args_list],
                         ["update_all.py", "deploy_all.py"])
        self.assertEqual(self.state()["status"], "success")
        self.assertTrue(Path(self.state()["log"]).exists())
        self.assertTrue((self.root / "WEEKLY_STATUS.txt").exists())
        self.notify.assert_called_once_with(Path(self.state()["log"]), resolved=True)

    def test_update_failure_prevents_deploy_and_alerts(self):
        with patch.object(weekly, "run_step", side_effect=RuntimeError("test update failure")) as step:
            self.assertEqual(weekly.execute_run(), 1)
        step.assert_called_once()
        self.assertEqual(self.state()["status"], "failed")
        self.assertIn("test update failure", Path(self.state()["log"]).read_text())
        self.assertIn("FAILED", (self.root / "WEEKLY_STATUS.txt").read_text())
        self.notify.assert_called_once()

    def test_deploy_failure_is_not_reported_as_success(self):
        with patch.object(weekly, "run_step", side_effect=[None, RuntimeError("test deploy failure")]):
            self.assertEqual(weekly.execute_run(), 1)
        self.assertEqual(self.state()["step"], "Deploy verified websites")
        self.assertEqual(self.state()["status"], "failed")
        self.assertIn("some sites/files may already have been uploaded",
                      (self.root / "WEEKLY_STATUS.txt").read_text())

    def test_failed_attempt_is_not_automatically_repeated_in_same_week(self):
        with patch.object(weekly, "run_step", side_effect=RuntimeError("failed")) as step:
            self.assertEqual(weekly.execute_run(), 1)
            first_state = self.state()
            self.assertEqual(weekly.execute_run(), 0)
        step.assert_called_once()
        self.assertEqual(self.state(), first_state)
        self.notify.assert_called_once()

    def test_next_week_can_run(self):
        with patch.object(weekly, "run_step") as step:
            with patch.object(weekly, "weekly_period", return_value="2026-09-20"):
                weekly.execute_run()
            with patch.object(weekly, "weekly_period", return_value="2026-09-27"):
                weekly.execute_run()
        self.assertEqual(step.call_count, 4)

    def test_busy_lock_does_not_replace_running_status(self):
        self.logs.mkdir(parents=True)
        weekly.write_json(self.logs / "latest-status.json", {"status": "running", "pid": 42})
        self.lock.side_effect = weekly.AlreadyRunningError()
        with patch.object(weekly, "run_step") as step:
            self.assertEqual(weekly.execute_run(), 0)
        self.assertEqual(self.state(), {"status": "running", "pid": 42})
        step.assert_not_called()

    def test_dry_run_never_calls_steps_or_consumes_weekly_attempt(self):
        with patch.object(weekly, "run_step") as step:
            self.assertEqual(weekly.execute_run(dry_run=True), 0)
        step.assert_not_called()
        self.notify.assert_not_called()
        self.lock.assert_not_called()
        self.assertFalse((self.logs / "last-attempt.json").exists())
        self.assertFalse((self.logs / "latest-status.json").exists())
        self.assertIn("no API calls", next(self.logs.glob("dry-run_*.log")).read_text())

    def test_topic_error_output_blocks_deployment_even_with_zero_exit(self):
        self.logs.mkdir(parents=True)
        (self.root / "update_all.py").write_text('print(\'{"event":"research_error","category":"ethics"}\')\n')
        with (self.logs / "test.log").open("ab") as log:
            with self.assertRaisesRegex(RuntimeError, "research_error"):
                weekly.run_step("Update", "update_all.py", (), log)

    def test_nonzero_exit_and_timeout_are_failures(self):
        self.logs.mkdir(parents=True)
        script = self.root / "backup_all.py"
        script.write_text('raise SystemExit(7)\n')
        with (self.logs / "test.log").open("ab") as log:
            with self.assertRaisesRegex(RuntimeError, "exit code 7"):
                weekly.run_step("Backup", "backup_all.py", (), log)
            script.write_text('import time\ntime.sleep(30)\n')
            with patch.object(weekly, "STEP_TIMEOUT_SECONDS", 0.3):
                with self.assertRaisesRegex(RuntimeError, "process tree was stopped"):
                    weekly.run_step("Backup", "backup_all.py", (), log)

    def test_timeout_stops_descendants(self):
        self.logs.mkdir(parents=True)
        marker = self.root / "descendant-survived.txt"
        child = f'import time; from pathlib import Path; time.sleep(2); Path({str(marker)!r}).write_text("alive")'
        (self.root / "backup_all.py").write_text(
            f'import subprocess,sys,time\nsubprocess.Popen([sys.executable, "-c", {child!r}])\ntime.sleep(30)\n'
        )
        with (self.logs / "test.log").open("ab") as log, patch.object(weekly, "STEP_TIMEOUT_SECONDS", 0.7):
            with self.assertRaisesRegex(RuntimeError, "process tree was stopped"):
                weekly.run_step("Backup", "backup_all.py", (), log)
        time.sleep(2)
        self.assertFalse(marker.exists())

    def test_child_output_is_redacted_before_it_reaches_disk(self):
        self.logs.mkdir(parents=True)
        credential = "synthetic-credential-1234"
        (self.root / "private.py").write_text(f'OPENAI_API_KEY = {credential!r}\n')
        (self.root / "backup_all.py").write_text(f'print({credential!r})\n')
        path = self.logs / "test.log"
        with path.open("ab") as log:
            weekly.run_step("Backup", "backup_all.py", (), log)
        self.assertNotIn(credential, path.read_text())
        self.assertIn("[REDACTED]", path.read_text())

    def test_notification_failure_still_leaves_failure_log_and_exit_code(self):
        self.notify.side_effect = RuntimeError("notifications disabled")
        with patch.object(weekly, "run_step", side_effect=RuntimeError("step failed")):
            self.assertEqual(weekly.execute_run(), 1)
        self.assertEqual(self.state()["status"], "failed")
        self.assertIn("notifications disabled", Path(self.state()["log"]).read_text())


class PlatformAndEmailTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        self.enterContext(patch.object(weekly, "ROOT", self.root))
        self.enterContext(patch.object(weekly, "LOG_DIR", self.root / "logs"))
        (self.root / "private.py").write_text('WEEKLY_ALERT_EMAIL = "alerts@example.com"\n')

    def test_real_lock_blocks_another_process_and_releases(self):
        lock_path = self.root / "runner.lock"
        code = (
            'import sys; from pathlib import Path; import weekly_run as w\n'
            'try:\n with w.run_lock(Path(sys.argv[1])): pass\n'
            'except w.AlreadyRunningError:\n sys.exit(7)\n'
        )
        with weekly.run_lock(lock_path):
            result = subprocess.run([sys.executable, '-c', code, str(lock_path)], capture_output=True)
            self.assertEqual(result.returncode, 7, result.stderr)
        result = subprocess.run([sys.executable, '-c', code, str(lock_path)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_linux_success_does_not_send_email(self):
        with patch.object(weekly, "IS_WINDOWS", False), patch.object(weekly, "send_email") as send:
            weekly.notify_failure(self.root / "test.log", resolved=True)
            send.assert_not_called()

    def test_email_has_failure_summary_but_no_credentials_or_raw_log(self):
        (self.root / "logs").mkdir()
        (self.root / "private.py").write_text('WEEKLY_ALERT_EMAIL = "alerts@example.com"\nSSH_PASSWORD = "synthetic-password"\n')
        (self.root / "logs/latest-status.json").write_text(json.dumps({
            "step": "Backup", "error": "synthetic-password authentication failed",
        }))
        with patch.object(weekly.shutil, "which", return_value="/usr/bin/mail"), patch.object(weekly.subprocess, "run") as send:
            weekly.send_email(self.root / "private-run.log")
        self.assertEqual(send.call_args.args[0][-1], "alerts@example.com")
        body = send.call_args.kwargs["input"].decode()
        self.assertIn("Backup", body)
        self.assertIn("[REDACTED]", body)
        self.assertNotIn("synthetic-password", body)

    def test_email_rejects_header_or_argument_injection(self):
        (self.root / "private.py").write_text('WEEKLY_ALERT_EMAIL = "-X/tmp/test@example.com"\n')
        with self.assertRaisesRegex(RuntimeError, "valid WEEKLY_ALERT_EMAIL"):
            weekly.alert_recipient()

    def test_cron_preserves_existing_jobs_and_replaces_its_own_entry(self):
        existing = 'MAILTO="previous@example.com"\n15 2 * * * /bin/true\n'
        configured = cron_text(existing, "alerts@example.com", PurePosixPath("/home/test/private app"), PurePosixPath("/home/test/venv/bin/python"))
        self.assertTrue(configured.startswith(existing))
        self.assertIn("0 22 * * 0 cd '/home/test/private app'", configured)
        self.assertEqual(cron_text(configured, "alerts@example.com", PurePosixPath("/home/test/private app"), PurePosixPath("/home/test/venv/bin/python")), configured)


class CronAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.native = Path(self.temp) / "native-crontab"
        self.native.touch()
        self.enterContext(patch.object(linux_schedule, "NATIVE_CRONTAB", self.native))
        self.enterContext(patch.object(linux_schedule.shutil, "which", return_value=str(Path(self.temp) / "wrapper")))

    def test_native_permission_denial_is_not_treated_as_success(self):
        denied = subprocess.CompletedProcess([], 1, "", "You (test) are not allowed to use this program (crontab)\n")
        with patch.object(linux_schedule.subprocess, "run", return_value=denied) as run:
            with self.assertRaisesRegex(RuntimeError, "hosting provider"):
                linux_schedule.check_native_crontab()
        self.assertEqual(run.call_args.args[0], [str(self.native), "-l"])

    def test_new_account_without_a_crontab_can_pass_the_access_check(self):
        missing = subprocess.CompletedProcess([], 1, "", "no crontab for test\n")
        with patch.object(linux_schedule.subprocess, "run", return_value=missing):
            linux_schedule.check_native_crontab()

    def test_native_commented_or_missing_entry_fails_verification(self):
        expected = "0 22 * * 0 /home/test/run\n"
        for actual in ("#" + expected, ""):
            with self.subTest(actual=actual):
                result = subprocess.CompletedProcess([], 0, actual, "")
                with patch.object(linux_schedule.subprocess, "run", return_value=result):
                    with self.assertRaisesRegex(RuntimeError, "did not retain"):
                        linux_schedule.check_native_crontab(expected)

    def test_matching_native_entry_passes_verification(self):
        expected = "0 22 * * 0 /home/test/run\n"
        result = subprocess.CompletedProcess([], 0, expected, "")
        with patch.object(linux_schedule.subprocess, "run", return_value=result):
            linux_schedule.check_native_crontab(expected)


if __name__ == "__main__":
    unittest.main()
