import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("workspace", Path(__file__).parents[1] / "workspace.py")
workspace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workspace)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.origin = self.root / "origin"
        workspace.git("init", "--quiet", str(self.origin))
        workspace.git("config", "user.name", "Fixture", cwd=self.origin)
        workspace.git("config", "user.email", "fixture@example.invalid", cwd=self.origin)
        (self.origin / "bot.txt").write_text("baseline\n")
        workspace.git("add", ".", cwd=self.origin)
        workspace.git("commit", "--quiet", "-m", "baseline", cwd=self.origin)
        self.revision = workspace.git("rev-parse", "HEAD", cwd=self.origin)
        self.dest = self.root / "workspace"

    def prepare(self, revision=None):
        return workspace.prepare(str(self.origin), revision or self.revision, self.dest)

    def test_pins_and_preserves_agent_work(self):
        self.assertEqual(self.prepare(), "created")
        self.assertEqual(workspace.git("rev-parse", "HEAD", cwd=self.dest), self.revision)
        (self.dest / "bot.txt").write_text("agent work\n")
        workspace.git("-c", "user.name=Fixture", "-c", "user.email=f@example.invalid",
                      "commit", "--quiet", "-am", "agent", cwd=self.dest)
        (self.dest / "new.txt").write_text("uncommitted")
        self.assertEqual(self.prepare(), "existing")
        self.assertEqual((self.dest / "new.txt").read_text(), "uncommitted")
        self.assertNotEqual(workspace.git("rev-parse", "HEAD", cwd=self.dest), self.revision)

    def test_refuses_mutable_revision(self):
        with self.assertRaises(ValueError):
            self.prepare("main")
        self.assertFalse(self.dest.exists())

    def test_failed_fetch_leaves_no_partial_workspace(self):
        with self.assertRaises(subprocess.SubprocessError):
            self.prepare("0" * 40)
        self.assertFalse(self.dest.exists())
        self.assertEqual(list(self.root.glob(".bcenv-checkout-*")), [])

    def test_refuses_existing_unmanaged_directory(self):
        self.dest.mkdir()
        (self.dest / "valuable.txt").write_text("keep")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual((self.dest / "valuable.txt").read_text(), "keep")

    def test_refuses_source_change(self):
        self.prepare()
        with self.assertRaises(ValueError):
            workspace.prepare("https://example.invalid/other.git", self.revision, self.dest)

    def test_refuses_symlink(self):
        self.dest.symlink_to(self.origin, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.prepare()
