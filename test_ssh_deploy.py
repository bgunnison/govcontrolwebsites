from __future__ import annotations

import errno
import json
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import ssh_deploy as deploy


class SCPDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.local = Path(self.temp)
        (self.local / "index.html").write_text("<!doctype html><h1>Test</h1>", encoding="utf-8")
        self.remote = "/home/test/public_html/site"
        self.settings = {
            "enabled": True, "remote_path": self.remote, "initial_atomic": True,
            "timeout": 30,
        }
        self.sftp = MagicMock()
        self.scp = MagicMock()
        self.client = MagicMock()
        self.client.open_sftp.return_value = self.sftp
        self.enterContext(patch.object(deploy, "connect", return_value=self.client))
        self.scp_factory = self.enterContext(patch.object(deploy, "SCPClient", return_value=self.scp))
        self.sftp.stat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755)

    def test_file_contents_use_scp_and_size_is_verified_before_replace(self):
        path = self.local / "index.html"
        destination = self.remote + "/page with spaces.html"
        self.sftp.stat.return_value = SimpleNamespace(st_size=path.stat().st_size)
        with (
            patch.object(deploy, "ensure_directory"),
            patch.object(deploy, "remote_exists", return_value=False),
            patch.object(deploy, "atomic_replace") as replace,
        ):
            deploy.upload_file(self.sftp, self.scp, path, destination)
        self.scp.put.assert_called_once_with(str(path), remote_path=destination + ".uploading")
        self.sftp.put.assert_not_called()
        self.sftp.chmod.assert_called_once_with(destination + ".uploading", 0o644)
        replace.assert_called_once_with(self.sftp, destination + ".uploading", destination)

    def test_scp_failure_leaves_live_file_untouched(self):
        self.scp.put.side_effect = RuntimeError("test interrupted transfer")
        with (
            patch.object(deploy, "ensure_directory"),
            patch.object(deploy, "remote_exists", return_value=False),
            patch.object(deploy, "atomic_replace") as replace,
        ):
            with self.assertRaisesRegex(RuntimeError, "interrupted transfer"):
                deploy.upload_file(self.sftp, self.scp, self.local / "index.html", self.remote + "/index.html")
        replace.assert_not_called()
        self.sftp.remove.assert_not_called()

    def test_incomplete_scp_file_is_not_published(self):
        self.sftp.stat.return_value = SimpleNamespace(st_size=0)
        with (
            patch.object(deploy, "ensure_directory"),
            patch.object(deploy, "remote_exists", return_value=False),
            patch.object(deploy, "atomic_replace") as replace,
        ):
            with self.assertRaisesRegex(RuntimeError, "size mismatch"):
                deploy.upload_file(self.sftp, self.scp, self.local / "index.html", self.remote + "/index.html")
        replace.assert_not_called()

    def test_manifest_also_uses_scp(self):
        manifest = deploy.local_manifest(self.local)
        captured = {}

        def capture(source, remote_path, mode, size):
            captured["payload"] = source.read()
            captured["path"] = remote_path
            captured["mode"] = mode
            self.assertEqual(len(captured["payload"]), size)
            self.sftp.stat.return_value = SimpleNamespace(st_size=size)

        self.scp.putfo.side_effect = capture
        with (
            patch.object(deploy, "remote_exists", return_value=False),
            patch.object(deploy, "atomic_replace"),
        ):
            deploy.write_remote_manifest(self.sftp, self.scp, self.remote, manifest)
        self.assertEqual(json.loads(captured["payload"])["files"], manifest)
        self.assertEqual(captured["mode"], "0600")
        self.assertEqual(captured["path"], self.remote + "/" + deploy.MANIFEST_NAME + ".uploading")
        self.sftp.putfo.assert_not_called()

    def test_incremental_deploy_only_copies_changed_files(self):
        (self.local / "site.css").write_text("body{}", encoding="utf-8")
        current = deploy.local_manifest(self.local)
        previous = {
            "index.html": {"sha256": "old"},
            "site.css": current["site.css"],
            "old.html": {"sha256": "deleted"},
        }
        with (
            patch.object(deploy, "read_remote_manifest", return_value=previous),
            patch.object(deploy, "upload_file") as upload,
            patch.object(deploy, "remove_managed_file") as remove,
            patch.object(deploy, "write_remote_manifest") as write,
        ):
            result = deploy.deploy_directory(self.local, self.settings)
        self.assertEqual((result.uploaded, result.unchanged, result.deleted), (1, 1, 1))
        upload.assert_called_once_with(self.sftp, self.scp, self.local / "index.html", self.remote + "/index.html")
        remove.assert_called_once_with(self.sftp, self.remote, "old.html")
        write.assert_called_once_with(self.sftp, self.scp, self.remote, current)
        self.scp.close.assert_called_once()
        self.sftp.close.assert_called_once()
        self.client.close.assert_called_once()

    def test_initial_atomic_deploy_uses_scp_and_retains_previous_root(self):
        with (
            patch.object(deploy, "read_remote_manifest", return_value=None),
            patch.object(deploy, "remove_directory_tree"),
            patch.object(deploy, "ensure_directory"),
            patch.object(deploy, "remote_exists", return_value=True),
            patch.object(deploy, "upload_file") as upload,
            patch.object(deploy, "write_remote_manifest") as write,
        ):
            result = deploy.deploy_directory(self.local, self.settings)
        self.assertTrue(result.initial_deploy)
        upload.assert_called_once_with(
            self.sftp, self.scp, self.local / "index.html", self.remote + ".next/index.html"
        )
        self.assertEqual(write.call_args.args[2], self.remote + ".next")
        self.assertEqual(
            [call.args for call in self.sftp.rename.call_args_list],
            [(self.remote, self.remote + ".previous"), (self.remote + ".next", self.remote)],
        )

    def test_initial_in_place_deploy_uses_scp(self):
        with (
            patch.object(deploy, "read_remote_manifest", return_value=None),
            patch.object(deploy, "ensure_directory"),
            patch.object(deploy, "upload_file") as upload,
            patch.object(deploy, "write_remote_manifest"),
        ):
            result = deploy.deploy_directory(self.local, {**self.settings, "initial_atomic": False})
        self.assertTrue(result.initial_deploy)
        upload.assert_called_once_with(self.sftp, self.scp, self.local / "index.html", self.remote + "/index.html")

    def test_initial_and_incremental_dry_runs_never_open_scp_or_write(self):
        for previous in (None, deploy.local_manifest(self.local)):
            with (
                self.subTest(initial=previous is None),
                patch.object(deploy, "read_remote_manifest", return_value=previous),
                patch.object(deploy, "upload_file") as upload,
                patch.object(deploy, "write_remote_manifest") as write,
            ):
                result = deploy.deploy_directory(self.local, {**self.settings, "dry_run": True})
            self.assertTrue(result.dry_run)
            upload.assert_not_called()
            write.assert_not_called()
        self.scp_factory.assert_not_called()
        self.sftp.mkdir.assert_not_called()
        self.sftp.remove.assert_not_called()
        self.sftp.rename.assert_not_called()

    def test_missing_parent_fails_before_any_upload(self):
        self.sftp.stat.side_effect = FileNotFoundError(errno.ENOENT, "missing")
        with self.assertRaisesRegex(RuntimeError, "full absolute server path"):
            deploy.deploy_directory(self.local, self.settings)
        self.scp_factory.assert_not_called()
        self.sftp.mkdir.assert_not_called()
        self.client.close.assert_called_once()

    def test_permission_errors_are_not_treated_as_missing_paths(self):
        self.sftp.stat.side_effect = PermissionError(errno.EACCES, "permission denied")
        with self.assertRaises(PermissionError):
            deploy.remote_exists(self.sftp, self.remote)

    def test_client_closes_if_sftp_setup_fails(self):
        self.client.open_sftp.side_effect = RuntimeError("test setup failure")
        with self.assertRaisesRegex(RuntimeError, "setup failure"):
            deploy.deploy_directory(self.local, self.settings)
        self.client.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
