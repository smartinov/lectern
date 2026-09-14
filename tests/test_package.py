import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_package", ROOT / "scripts/check_package.py")
check_package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_package)


class PackageTests(unittest.TestCase):
    def test_private_data_and_missing_helpers_are_not_distributable(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "lectern"
            shutil.copytree(ROOT / "plugins/lectern", package, ignore=shutil.ignore_patterns("__pycache__"))
            check_package.validate(package)
            private = package / "learner"
            private.mkdir()
            (private / "profile.yaml").write_text("background: synthetic-private-marker\n")
            with self.assertRaisesRegex(ValueError, "private or generated"):
                check_package.validate(package)
            shutil.rmtree(private)
            (package / "scripts/course.py").unlink()
            with self.assertRaisesRegex(ValueError, "resource missing"):
                check_package.validate(package)

    def test_git_ignores_private_state_but_tracks_agent_guidance(self):
        import subprocess
        result = subprocess.run(["git", "check-ignore", "--stdin"], input="learner/profile.yaml\ncourses/personal/course.yaml\narticles/personal/article.md\nbuild/personal.epub\nAGENTS.md\n", cwd=ROOT, capture_output=True, text=True, check=True)
        self.assertEqual(set(result.stdout.splitlines()), {"learner/profile.yaml", "courses/personal/course.yaml", "articles/personal/article.md", "build/personal.epub"})
