from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from schedule import github_ssh_entry as entry


class RestrictedSSHTests(unittest.TestCase):
    def setUp(self):
        self.original_cwd = Path.cwd()
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.addCleanup(os.chdir, self.original_cwd)
        self.root = Path(self.directory)
        self.enterContext(patch.object(entry, "ROOT", self.root))
        self.enterContext(patch.object(entry.weekly, "weekly_period", return_value="2026-10-04"))
        for name in ("private.py", "weekly_run.py", "update_all.py", "deploy_all.py"):
            (self.root / name).write_text("", encoding="utf-8")
            (self.root / name).chmod(0o600)
        self.logs = self.root / "logs/weekly"
        self.logs.mkdir(parents=True)

    def state(self, **values):
        (self.logs / "latest-status.json").write_text(json.dumps(values), encoding="utf-8")

    def invoke(self, command):
        with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
            code = entry.main(command)
        return code, output.getvalue()

    def test_arbitrary_commands_are_rejected_without_starting_a_process(self):
        with patch.object(entry.subprocess, "run") as run:
            for command in ("", "bash", "cat private.py", "run; id", "run\nstatus", "run --force", " run", "scp -f private.py"):
                self.assertEqual(self.invoke(command)[0], 64)
            run.assert_not_called()

    def test_check_uses_only_dry_run_and_does_not_expose_private_status(self):
        self.state(status="failed", period="2026-10-04", error="PRIVATE_VALUE", log="PRIVATE_PATH")
        with patch.object(entry.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            code, output = self.invoke("check")
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.args[0][-1], "--dry-run")
        self.assertNotIn("PRIVATE", output)
        self.assertEqual(json.loads(output)["result"], "check_passed")

    def test_skipped_failed_running_or_old_attempt_is_not_reported_successful(self):
        with patch.object(entry.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            for state in ({"status": "failed", "period": "2026-10-04"},
                          {"status": "running", "period": "2026-10-04"},
                          {"status": "success", "period": "2026-09-27"}):
                self.state(**state)
                self.assertEqual(self.invoke("run")[0], 1)

    def test_success_requires_matching_period_and_zero_exit(self):
        self.state(status="success", period="2026-10-04")
        with patch.object(entry.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(self.invoke("run")[0], 0)
            self.assertEqual(run.call_args.args[0][-1], "--scheduled")
        with patch.object(entry.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
            self.assertEqual(self.invoke("run")[0], 1)

    def test_status_does_not_start_work_and_omits_non_date_values(self):
        self.state(status="success", period="2026-10-04", started_at="PRIVATE_VALUE", error="PRIVATE_VALUE")
        with patch.object(entry.subprocess, "run") as run:
            code, output = self.invoke("status")
        run.assert_not_called()
        self.assertEqual(code, 0)
        self.assertNotIn("PRIVATE", output)

    def test_missing_configuration_fails_without_exposing_paths(self):
        (self.root / "private.py").unlink()
        with patch.object(entry.subprocess, "run") as run:
            code, output = self.invoke("check")
        self.assertEqual(code, 1)
        run.assert_not_called()
        self.assertNotIn(str(self.root), output)


if __name__ == "__main__":
    unittest.main()
