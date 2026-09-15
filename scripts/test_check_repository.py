"""Exercise content exclusions against a real, isolated Git index."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RepositoryContentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="govcontrol-audit-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "scripts").mkdir()
        shutil.copyfile(ROOT / ".gitignore", self.root / ".gitignore")
        shutil.copyfile(ROOT / "scripts/check_repository.py", self.root / "scripts/check_repository.py")
        self.git("init", "--quiet")
        for site in (
            "governmentaicontrol.com", "governmentguncontrol.com",
            "governmenthealthcarecontrol.com",
        ):
            for relative in (
                "content/posts/example.json", "content/imported_media/example.txt",
                "content/wordpress-import.json", "dist/index.html",
            ):
                path = self.root / site / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")
        self.git("add", "--all")

    def git(self, *args: str) -> bytes:
        return subprocess.check_output(["git", *args], cwd=self.root, stderr=subprocess.PIPE)

    def audit(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "scripts/check_repository.py"], cwd=self.root,
            capture_output=True, text=True, check=False,
        )

    def test_add_all_leaves_local_content_ignored(self) -> None:
        tracked = self.git("ls-files", "-z").decode().split("\0")
        self.assertEqual(set(filter(None, tracked)), {".gitignore", "scripts/check_repository.py"})
        self.assertEqual(self.audit().returncode, 0)

    def test_audit_rejects_force_added_content(self) -> None:
        for path in sorted(self.root.glob("government*.com/**/*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.root).as_posix()
            with self.subTest(path=relative):
                self.git("add", "--force", "--", relative)
                result = self.audit()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(f"Private/generated file is staged: {relative}", result.stdout)
                self.git("rm", "--cached", "--", relative)
                self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
