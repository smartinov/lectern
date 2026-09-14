from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
import zipfile
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "plugins/lectern"
sys.path.insert(0, str(PACKAGE / "scripts"))
import course
import kindle_article as epub

FIXTURE = ROOT / "tests/fixtures/course"


class SettingsTests(unittest.TestCase):
    def test_missing_profile_uses_session_defaults(self):
        settings = course.resolve_settings({}, {})
        self.assertEqual((settings["reading_minutes"], settings["practice_minutes"]), (20, 10))
        self.assertEqual(settings["style"], "blunt-mentor")
        self.assertEqual(settings["background"], "")

    def test_course_fields_then_personalization_then_profile(self):
        settings = course.resolve_settings(
            {"reading_minutes": 12, "style": None, "personalization": {"reading_minutes": 15, "writing": {"profanity": "none"}, "goals": []}},
            {"reading_minutes": 25, "style": "socratic-guide", "writing": {"humor": "light"}, "goals": ["one"]},
        )
        self.assertEqual(settings["reading_minutes"], 12)
        self.assertEqual(settings["style"], "socratic-guide")
        self.assertEqual(settings["writing"]["humor"], "light")
        self.assertEqual(settings["writing"]["profanity"], "none")
        self.assertEqual(settings["goals"], [])

    def test_invalid_settings_and_duplicate_yaml_fail(self):
        for value in (0, -1, True, "20"):
            with self.subTest(value=value), self.assertRaises(epub.KindleArticleError):
                course.resolve_settings({"reading_minutes": value}, {})
        with self.assertRaisesRegex(epub.KindleArticleError, "unknown writing style"):
            course.resolve_settings({"style": "missing"}, {})
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.yaml"
            path.write_text("style: blunt-mentor\nstyle: socratic-guide\n")
            with self.assertRaisesRegex(epub.KindleArticleError, "unique"):
                course.read_yaml(path)

    def test_package_cannot_be_a_workspace(self):
        with self.assertRaisesRegex(epub.KindleArticleError, "outside the installed plugin"):
            course.workspace_path(str(PACKAGE))


class CourseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name).resolve() / "workspace"
        self.directory = self.workspace / "courses/learning-sample"
        shutil.copytree(FIXTURE, self.directory)
        self.args = argparse.Namespace(workspace=str(self.workspace), course="courses/learning-sample", pandoc="pandoc", magick="magick", epubcheck_command="epubcheck")
        with contextlib.redirect_stdout(io.StringIO()):
            course.command_resolve(self.args)

    def test_every_outcome_and_lesson_needs_assessment(self):
        spec = course.read_yaml(self.directory / "course.yaml")
        lessons = sorted((self.directory / "lessons").glob("*.md"))
        bank = course.read_yaml(self.directory / "questions.yaml")
        course.validate_questions(spec, lessons, bank)
        for mutation in ("duplicate", "objective", "correct", "missing-lesson"):
            bank = course.read_yaml(self.directory / "questions.yaml")
            if mutation == "duplicate":
                bank["questions"][1]["id"] = bank["questions"][0]["id"]
            elif mutation == "objective":
                bank["questions"][0]["objectives"] = ["unknown"]
            elif mutation == "correct":
                bank["questions"][1]["correct"] = ["missing"]
            else:
                bank["questions"].pop()
            with self.subTest(mutation=mutation), self.assertRaises(epub.KindleArticleError):
                course.validate_questions(spec, lessons, bank)

    def test_changed_spec_requires_explicit_resolution(self):
        with (self.directory / "course.yaml").open("a") as handle:
            handle.write("\n# changed intent\n")
        with self.assertRaisesRegex(epub.KindleArticleError, "resolve and review"):
            course.command_build(self.args)

    def test_workspace_escape_and_symlink_escape_fail(self):
        with self.assertRaises(epub.KindleArticleError):
            course.course_path(self.workspace, "../elsewhere")
        linked = self.workspace / "courses/linked"
        linked.symlink_to(self.workspace.parent, target_is_directory=True)
        with self.assertRaises(epub.KindleArticleError):
            course.course_path(self.workspace, "courses/linked")

    def test_init_preserves_existing_profile_and_ignores_private_state(self):
        learner = self.workspace / "learner"
        learner.mkdir()
        profile = learner / "profile.yaml"
        profile.write_text("style: socratic-guide\n")
        with contextlib.redirect_stdout(io.StringIO()):
            course.command_init(argparse.Namespace(workspace=str(self.workspace), slug="another-course"))
        self.assertEqual(profile.read_text(), "style: socratic-guide\n")
        self.assertIn("/courses/", (self.workspace / ".gitignore").read_text())
        with self.assertRaisesRegex(epub.KindleArticleError, "overwrite"):
            course.command_init(argparse.Namespace(workspace=str(self.workspace), slug="another-course"))

    def test_remote_and_missing_images_fail_before_build(self):
        for image in ("https://example.test/private.png", "missing.png", "../../outside.png"):
            with self.subTest(image=image), self.assertRaises(epub.KindleArticleError):
                course.check_media(f"![Example]({image})", self.directory, "pandoc")

    def test_installed_read_only_package_builds_from_unrelated_directory(self):
        installed = self.workspace.parent / "installed"
        shutil.copytree(PACKAGE, installed, ignore=shutil.ignore_patterns("__pycache__"))
        for path in installed.rglob("*"):
            path.chmod(0o555 if path.is_dir() else 0o444)
        installed.chmod(0o555)
        unrelated = self.workspace.parent / "unrelated"
        unrelated.mkdir()
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, str(installed / "scripts/course.py"), "build", "--workspace", str(self.workspace), str(self.directory)], cwd=unrelated, env=environment, capture_output=True, text=True)
        for path in installed.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)
        installed.chmod(0o755)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list(unrelated.iterdir()), [])
        self.assertFalse((installed / "build").exists())
        self.assertTrue((self.workspace / "build/learning-sample.epub").is_file())

    def test_epub_answers_return_to_the_lesson_and_metadata_is_correct(self):
        with contextlib.redirect_stdout(io.StringIO()):
            course.command_build(self.args)
        artifact = self.workspace / "build/learning-sample.epub"
        report = epub.read_json(artifact.with_suffix(".verification.json"))
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["visual_inspection"], "NOT_RUN")
        self.assertEqual(report["assessment_questions"], 3)
        self.assertFalse(report["navigation_in_spine"])
        bank = course.read_yaml(self.directory / "questions.yaml")["questions"]
        with zipfile.ZipFile(artifact) as archive:
            documents = {name: ET.fromstring(archive.read(name)) for name in archive.namelist() if name.endswith(".xhtml")}
            ids = {node.get("id"): (name, node) for name, root in documents.items() for node in root.iter() if node.get("id")}
            for question in bank:
                qid = question["id"]
                question_file, _ = ids[f"question-{qid}"]
                answer_file, _ = ids[f"answer-{qid}"]
                continuation_file, _ = ids[f"continue-{qid}"]
                self.assertEqual(question_file, continuation_file)
                self.assertNotEqual(answer_file, question_file)
                question_text = " ".join(documents[question_file].itertext())
                self.assertNotIn(question["answer"], question_text)
                continuation = next(node for node in documents[answer_file].iter() if node.get("href", "").endswith(f"#continue-{qid}"))
                parsed = urllib.parse.urlsplit(continuation.get("href"))
                target = (Path(answer_file).parent / parsed.path).as_posix()
                self.assertEqual(target, continuation_file)
            opf = ET.fromstring(archive.read("EPUB/content.opf"))
            self.assertEqual(opf.find(f".//{{{epub.DC_NS}}}language").text, "en")
            self.assertFalse(opf.findall(f".//{{{epub.DC_NS}}}creator"))
            all_text = " ".join(" ".join(document.itertext()) for document in documents.values())
            limitation = course.read_yaml(self.directory / "sources.yaml")["sources"][0]["limitations"]
            self.assertIn(limitation, all_text)
        original = artifact.read_bytes()
        self.args.epubcheck_command = "/usr/bin/false"
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(epub.KindleArticleError):
            course.command_build(self.args)
        self.assertEqual(artifact.read_bytes(), original)


class DeliveryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.artifact = self.root / "book.epub"
        self.artifact.write_bytes(b"verified")
        self.config = self.root / "config.json"
        epub.write_json(self.config, {"sender": "sender@icloud.test", "kindle_recipient": "reader@kindle.test", "output_directory": str(self.root), "maximum_attachment_bytes": 100, "confirmation_required": True, "cover_mode": "always-editorial"}, mode=0o600)
        self.metadata = self.root / "metadata.json"
        epub.write_json(self.metadata, {"title": "Book", "content_kind": "course"})
        self.verification = self.root / "verification.json"
        epub.write_json(self.verification, {"status": "PASS", "epub_sha256": epub.sha256_file(self.artifact)})
        self.confirmation = self.root / "confirmation.json"
        self.preview_args = argparse.Namespace(config=str(self.config), epub=str(self.artifact), metadata=str(self.metadata), verification=str(self.verification), confirmation_file=str(self.confirmation))
        with contextlib.redirect_stdout(io.StringIO()):
            epub.command_preview_mail(self.preview_args)
        self.send_args = argparse.Namespace(mode="send", confirmation_file=str(self.confirmation), confirmation_token=epub.read_json(self.confirmation)["token"], receipt=str(self.root / "send.json"), osascript="mock-osascript")

    def test_same_size_attachment_change_blocks_mail(self):
        self.artifact.write_bytes(b"modified")
        with mock.patch.object(epub.subprocess, "run") as run:
            with self.assertRaisesRegex(epub.KindleArticleError, "attachment changed"):
                epub.command_mail(self.send_args)
            run.assert_not_called()

    def test_stale_verification_blocks_preview(self):
        self.artifact.write_bytes(b"changed")
        with self.assertRaisesRegex(epub.KindleArticleError, "stale"):
            epub.command_preview_mail(self.preview_args)

    def test_authorized_send_consumes_before_adapter_and_cannot_retry(self):
        def send(*args, **kwargs):
            self.assertEqual(epub.read_json(self.confirmation)["status"], "CONSUMED_BEFORE_SEND")
            return subprocess.CompletedProcess(args, 0, "send requested", "")
        with mock.patch.object(epub.subprocess, "run", side_effect=send) as run, contextlib.redirect_stdout(io.StringIO()):
            epub.command_mail(self.send_args)
            with self.assertRaisesRegex(epub.KindleArticleError, "current confirmation token"):
                epub.command_mail(self.send_args)
            self.assertEqual(run.call_count, 1)
        self.assertIn("attached course", epub.read_json(self.confirmation)["payload"]["body"])

    def test_ambiguous_sent_status_only_queries(self):
        receipt = epub.read_json(self.confirmation)
        receipt["status"] = "CONSUMED_BEFORE_SEND"
        epub.write_json(self.confirmation, receipt)
        with mock.patch.object(epub.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "0", "")) as run:
            with self.assertRaisesRegex(epub.KindleArticleError, "ambiguous"):
                epub.command_verify_sent(self.send_args)
            self.assertEqual(run.call_args.args[0][2], "count-sent")
            self.assertEqual(run.call_args.args[0][-1], receipt["payload"]["delivery_id"])

    def test_unattempted_delivery_cannot_be_verified(self):
        with mock.patch.object(epub.subprocess, "run") as run:
            with self.assertRaisesRegex(epub.KindleArticleError, "attempted delivery"):
                epub.command_verify_sent(self.send_args)
            run.assert_not_called()

    def test_course_rebuild_cannot_replace_the_snapshot_mail_reads(self):
        original_bytes = self.artifact.read_bytes()
        def send(argv, **kwargs):
            replacement = self.root / "rebuilt.epub"
            replacement.write_bytes(b"rebuilt!")
            replacement.replace(self.artifact)
            attachment = Path(argv[7])
            self.assertNotEqual(attachment, self.artifact)
            self.assertEqual(attachment.name, self.artifact.name)
            self.assertEqual(attachment.read_bytes(), original_bytes)
            self.assertEqual(attachment.stat().st_mode & 0o777, 0o400)
            return subprocess.CompletedProcess(argv, 0, "send requested", "")
        with mock.patch.object(epub.subprocess, "run", side_effect=send), contextlib.redirect_stdout(io.StringIO()):
            epub.command_mail(self.send_args)

    def test_only_one_concurrent_send_can_claim_a_confirmation(self):
        from concurrent.futures import ThreadPoolExecutor
        barrier = threading.Barrier(2)
        original_read = epub.read_json
        def read(path):
            value = original_read(path)
            if Path(path) == self.confirmation:
                barrier.wait(timeout=5)
            return value
        def attempt():
            try:
                epub.command_mail(self.send_args)
                return "sent"
            except epub.KindleArticleError:
                return "blocked"
        with mock.patch.object(epub, "read_json", side_effect=read), mock.patch.object(epub.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "send requested", "")) as run, contextlib.redirect_stdout(io.StringIO()):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: attempt(), range(2)))
            self.assertCountEqual(results, ["sent", "blocked"])
            self.assertEqual(run.call_count, 1)

    def test_new_preview_gets_a_distinct_delivery_reference(self):
        first = epub.read_json(self.confirmation)["payload"]["delivery_id"]
        with contextlib.redirect_stdout(io.StringIO()):
            epub.command_preview_mail(self.preview_args)
        self.assertNotEqual(epub.read_json(self.confirmation)["payload"]["delivery_id"], first)

    def test_old_delivery_does_not_match_current_reference(self):
        receipt = epub.read_json(self.confirmation)
        receipt["status"] = "CONSUMED_BEFORE_SEND"
        epub.write_json(self.confirmation, receipt)
        prior_body = "Delivery reference: lectern-prior-attempt"
        def count(argv, **kwargs):
            self.assertNotIn(argv[-1], prior_body)
            return subprocess.CompletedProcess(argv, 0, "0", "")
        with mock.patch.object(epub.subprocess, "run", side_effect=count):
            with self.assertRaisesRegex(epub.KindleArticleError, "ambiguous"):
                epub.command_verify_sent(self.send_args)

    def test_verification_does_not_read_email_config(self):
        with mock.patch.object(epub, "load_config", side_effect=AssertionError("mail config read")), mock.patch.object(epub, "verify_epub", return_value={"status": "PASS", "epub_sha256": "synthetic"}), contextlib.redirect_stdout(io.StringIO()):
            epub.command_verify(argparse.Namespace(config=None, epub=str(self.artifact), epubcheck_command="epubcheck", accept_epubcheck_warnings=None, output=str(self.root / "local.json")))


if __name__ == "__main__":
    unittest.main()
