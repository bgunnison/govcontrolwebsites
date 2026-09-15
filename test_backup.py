from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import backup_all as backup


SITE = "governmentguncontrol.com"


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.run_dir = Path(self.temp)
        self.settings = {
            "site_name": SITE, "remote_path": "/home/test/public_html/gun",
            "enabled": True, "password": "SECRET-MUST-NOT-BE-IN-REPORT",
        }
        self.client = MagicMock()
        self.sftp = self.client.open_sftp.return_value
        self.scp = MagicMock()
        self.sftp.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755)
        self.enterContext(patch.object(backup, "connect", return_value=self.client))
        self.scp_factory = self.enterContext(patch.object(backup, "SCPClient", return_value=self.scp))
        self.contents = {"index.html": b"page", ".htaccess": b"hidden", "assets/style.css": b"css"}
        self.inventory = {
            name: {"kind": "file", "mode": 0o644, "mtime": 123, "size": len(data)}
            for name, data in self.contents.items()
        }
        self.inventory["assets"] = {"kind": "directory", "mode": 0o755, "mtime": None, "size": 0}

        def download(remote_path, local_path, recursive, preserve_times):
            target = Path(local_path)
            target.mkdir()
            for name, data in self.contents.items():
                file_path = target / name
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(data)

        self.scp.get.side_effect = download

    def test_download_includes_hidden_files_and_marks_complete_after_verification(self):
        with patch.object(backup, "remote_inventory", return_value=self.inventory) as inventory:
            result = backup.backup_site(self.settings, self.run_dir)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["files"], 3)
        self.assertTrue((self.run_dir / SITE / ".htaccess").is_file())
        self.assertFalse((self.run_dir / (SITE + ".partial")).exists())
        self.scp.get.assert_called_once_with(
            self.settings["remote_path"], local_path=str(self.run_dir / (SITE + ".partial")),
            recursive=True, preserve_times=True,
        )
        self.assertEqual(inventory.call_count, 2)
        manifest = json.loads((self.run_dir / result["manifest"]).read_text())
        self.assertEqual(len(manifest["local_files"]["index.html"]["sha256"]), 64)
        self.assertNotIn(self.settings["password"], json.dumps(manifest))
        self.scp.put.assert_not_called()
        self.sftp.put.assert_not_called()
        self.sftp.mkdir.assert_not_called()
        self.sftp.remove.assert_not_called()
        self.sftp.rename.assert_not_called()
        self.client.exec_command.assert_not_called()
        self.scp.close.assert_called_once()
        self.sftp.close.assert_called_once()
        self.client.close.assert_called_once()

    def test_missing_file_retains_partial_download(self):
        expected = {**self.inventory, "missing.txt": {"kind": "file", "size": 9}}
        with patch.object(backup, "remote_inventory", return_value=expected):
            with self.assertRaisesRegex(RuntimeError, "file/directory list differs"):
                backup.backup_site(self.settings, self.run_dir)
        self.assertTrue((self.run_dir / (SITE + ".partial")).is_dir())
        self.assertFalse((self.run_dir / SITE).exists())

    def test_wrong_size_retains_partial_download(self):
        expected = {**self.inventory, "index.html": {**self.inventory["index.html"], "size": 500}}
        with patch.object(backup, "remote_inventory", return_value=expected):
            with self.assertRaisesRegex(RuntimeError, "file sizes differ"):
                backup.backup_site(self.settings, self.run_dir)
        self.assertFalse((self.run_dir / SITE).exists())

    def test_source_changed_during_copy_is_not_marked_complete(self):
        changed = {**self.inventory, "index.html": {**self.inventory["index.html"], "mtime": 456}}
        with patch.object(backup, "remote_inventory", side_effect=[self.inventory, changed]):
            with self.assertRaisesRegex(RuntimeError, "Server files changed"):
                backup.backup_site(self.settings, self.run_dir)
        self.assertTrue((self.run_dir / (SITE + ".partial")).is_dir())
        self.assertFalse((self.run_dir / SITE).exists())

    def test_existing_backup_is_not_overwritten(self):
        destination = self.run_dir / SITE
        destination.mkdir()
        with self.assertRaisesRegex(RuntimeError, "Refusing to overwrite"):
            backup.backup_site(self.settings, self.run_dir)
        self.client.open_sftp.assert_not_called()

    def test_remote_root_symlink_is_rejected(self):
        self.sftp.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
        with self.assertRaisesRegex(RuntimeError, "real directory"):
            backup.backup_site(self.settings, self.run_dir)
        self.scp.get.assert_not_called()
        self.client.close.assert_called_once()

    def test_inventory_includes_hidden_files_and_subdirectories(self):
        def entry(name, mode, size=0):
            return SimpleNamespace(filename=name, st_mode=mode, st_size=size, st_mtime=123)
        self.sftp.listdir_attr.side_effect = [
            [entry(".htaccess", stat.S_IFREG | 0o644, 6), entry("assets", stat.S_IFDIR | 0o755)],
            [entry("style.css", stat.S_IFREG | 0o644, 3)],
        ]
        result = backup.remote_inventory(self.sftp, "/site")
        self.assertEqual(set(result), {".htaccess", "assets", "assets/style.css"})

    def test_inventory_rejects_unsafe_or_incompatible_names(self):
        for name in ("..", "../outside", "a\\b", "a:b", "CON.txt", "trailing."):
            with self.subTest(name=name):
                self.sftp.listdir_attr.return_value = [
                    SimpleNamespace(filename=name, st_mode=stat.S_IFREG | 0o644, st_size=0, st_mtime=0)
                ]
                with self.assertRaisesRegex(RuntimeError, "safely to Windows"):
                    backup.remote_inventory(self.sftp, "/site")

    def test_inventory_rejects_case_collisions_and_symlinks(self):
        self.sftp.listdir_attr.return_value = [
            SimpleNamespace(filename=name, st_mode=stat.S_IFREG | 0o644, st_size=0, st_mtime=0)
            for name in ("File", "file")
        ]
        with self.assertRaisesRegex(RuntimeError, "collide"):
            backup.remote_inventory(self.sftp, "/site")
        self.sftp.listdir_attr.return_value = [
            SimpleNamespace(filename="link", st_mode=stat.S_IFLNK | 0o777)
        ]
        with self.assertRaisesRegex(RuntimeError, "symbolic link"):
            backup.remote_inventory(self.sftp, "/site")

    def test_failed_site_does_not_stop_remaining_sites(self):
        second = {**self.settings, "site_name": "governmentaicontrol.com"}
        with patch.object(backup, "backup_site", side_effect=[
            RuntimeError("test connection failure"), {"status": "complete", "files": 2, "bytes": 10}
        ]) as download:
            run_dir, success = backup.run_backups([self.settings, second], self.run_dir)
        self.assertFalse(success)
        self.assertEqual(download.call_count, 2)
        report = json.loads((run_dir / "backup.json").read_text())
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["sites"][SITE]["status"], "failed")
        self.assertEqual(report["sites"][second["site_name"]]["status"], "complete")
        self.assertNotIn(self.settings["password"], json.dumps(report))

    def test_new_runs_keep_previous_backup_directories(self):
        with patch.object(backup, "backup_site", return_value={"status": "complete", "files": 1, "bytes": 5}):
            first, _ = backup.run_backups([self.settings], self.run_dir)
            second, _ = backup.run_backups([self.settings], self.run_dir)
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_dir())
        self.assertTrue(second.is_dir())

    def test_interruption_is_recorded(self):
        with patch.object(backup, "backup_site", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                backup.run_backups([self.settings], self.run_dir)
        report_path = next(self.run_dir.glob("*/backup.json"))
        self.assertEqual(json.loads(report_path.read_text())["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
