from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import weekly_run as weekly


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

    def test_success_requires_update_then_backup_then_deploy(self):
        with patch.object(weekly, "run_step") as step:
            self.assertEqual(weekly.execute_run(), 0)
        self.assertEqual([call.args[1] for call in step.call_args_list],
                         ["update_all.py", "backup_all.py", "deploy_all.py"])
        self.assertEqual(self.state()["status"], "success")
        self.assertTrue(Path(self.state()["log"]).exists())
        self.assertTrue((self.root / "WEEKLY_STATUS.txt").exists())
        self.notify.assert_called_once_with(Path(self.state()["log"]), resolved=True)

    def test_update_failure_prevents_backup_and_deploy_and_alerts(self):
        with patch.object(weekly, "run_step", side_effect=RuntimeError("test update failure")) as step:
            self.assertEqual(weekly.execute_run(), 1)
        step.assert_called_once()
        self.assertEqual(self.state()["status"], "failed")
        self.assertIn("test update failure", Path(self.state()["log"]).read_text())
        self.assertIn("FAILED", (self.root / "WEEKLY_STATUS.txt").read_text())
        self.notify.assert_called_once()

    def test_backup_failure_prevents_deploy(self):
        with patch.object(weekly, "run_step", side_effect=[None, RuntimeError("test backup failure")]) as step:
            self.assertEqual(weekly.execute_run(), 1)
        self.assertEqual(step.call_count, 2)
        self.assertEqual(self.state()["step"], "Back up live websites")
        self.assertEqual(self.state()["status"], "failed")

    def test_deploy_failure_is_not_reported_as_success(self):
        with patch.object(weekly, "run_step", side_effect=[None, None, RuntimeError("test deploy failure")]):
            self.assertEqual(weekly.execute_run(), 1)
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
        self.assertEqual(step.call_count, 6)

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
        with (self.logs / "test.log").open("ab") as log:
            process = MagicMock()
            process.wait.return_value = 0

            def launch(*args, **kwargs):
                kwargs["stdout"].write(b'{"event":"research_error","category":"ethics"}\n')
                kwargs["stdout"].flush()
                return process

            with patch.object(weekly.subprocess, "Popen", side_effect=launch):
                with self.assertRaisesRegex(RuntimeError, "research_error"):
                    weekly.run_step("Update", "update_all.py", (), log)

    def test_nonzero_exit_and_timeout_are_failures(self):
        self.logs.mkdir(parents=True)
        with (self.logs / "test.log").open("ab") as log:
            process = MagicMock(pid=12345)
            process.wait.return_value = 7
            with patch.object(weekly.subprocess, "Popen", return_value=process):
                with self.assertRaisesRegex(RuntimeError, "exit code 7"):
                    weekly.run_step("Backup", "backup_all.py", (), log)
            process.wait.side_effect = [subprocess.TimeoutExpired("test", 1), 0]
            with (
                patch.object(weekly.subprocess, "Popen", return_value=process),
                patch.object(weekly.subprocess, "run") as terminate,
            ):
                with self.assertRaisesRegex(RuntimeError, "process tree was stopped"):
                    weekly.run_step("Backup", "backup_all.py", (), log)
            self.assertEqual(terminate.call_args.args[0], ["taskkill.exe", "/PID", "12345", "/T", "/F"])

    def test_notification_failure_still_leaves_failure_log_and_exit_code(self):
        self.notify.side_effect = RuntimeError("notifications disabled")
        with patch.object(weekly, "run_step", side_effect=RuntimeError("step failed")):
            self.assertEqual(weekly.execute_run(), 1)
        self.assertEqual(self.state()["status"], "failed")
        self.assertIn("notifications disabled", Path(self.state()["log"]).read_text())


if __name__ == "__main__":
    unittest.main()
