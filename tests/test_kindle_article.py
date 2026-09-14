from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (
    ROOT
    / "plugins"
    / "lectern"
    / "scripts"
    / "kindle_article.py"
)
MAIL_SCRIPT = SCRIPT.with_name("mail-kindle.applescript")
FIXTURES = ROOT / "tests" / "fixtures" / "kindle_article"

spec = importlib.util.spec_from_file_location("kindle_article", SCRIPT)
assert spec and spec.loader
kindle_article = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = kindle_article
spec.loader.exec_module(kindle_article)


class MetadataTests(unittest.TestCase):
    def resolve(self, name: str):
        return kindle_article.resolve_metadata(
            (FIXTURES / name).read_text(encoding="utf-8"),
            f"https://example.test/{name}",
        )

    def test_visible_byline_precedes_acknowledgments(self):
        metadata = self.resolve("visible-byline.html")
        self.assertEqual(metadata["authors"], ["Ada Writer"])
        self.assertEqual(metadata["author_source"], "visible-byline")
        self.assertNotIn("Wrong Person", metadata["authors"])

    def test_jsonld_metadata(self):
        metadata = self.resolve("jsonld-author.html")
        self.assertEqual(metadata["authors"], ["Jules Author"])
        self.assertEqual(metadata["publisher"], "Structured Press")
        self.assertEqual(metadata["published_date"], "2026-08-01")

    def test_standard_metadata_supports_multiple_authors(self):
        metadata = self.resolve("multiple-authors.html")
        self.assertEqual(metadata["authors"], ["Alice Smith", "Bob Jones", "Carol Roe"])
        self.assertEqual(metadata["author_source"], "standard-metadata")

    def test_open_graph_author(self):
        metadata = self.resolve("open-graph-author.html")
        self.assertEqual(metadata["authors"], ["Social Author"])
        self.assertEqual(metadata["author_source"], "open-graph")

    def test_publisher_fallback_is_explicitly_labelled(self):
        metadata = self.resolve("publisher-fallback.html")
        self.assertEqual(metadata["authors"], ["Example Research"])
        self.assertTrue(metadata["author_inferred"])
        self.assertTrue(metadata["author_source"].startswith("explicit-publisher-fallback:"))
        self.assertNotIn("Acknowledged Person", metadata["authors"])

    def test_missing_authorship_fails(self):
        with self.assertRaisesRegex(kindle_article.KindleArticleError, "authorship"):
            self.resolve("missing-authorship.html")


class MarkdownTests(unittest.TestCase):
    def test_human_published_date_normalizes_to_iso(self):
        self.assertEqual(
            kindle_article.normalize_published_date("Aug 21, 2026"),
            "2026-08-21",
        )

    def test_repairs_source_fragment_from_stage_label(self):
        source = """# Plan

# Deploy

See [Stage 5: Deploy](#sd-s5) and [Stage 1: Plan](#sd-s1).
"""
        self.assertEqual(
            kindle_article.repair_internal_links(source),
            """# Plan

# Deploy

See [Stage 5: Deploy](#deploy) and [Stage 1: Plan](#plan).
""",
        )

    def test_removes_only_labelled_internal_contents(self):
        source = """# Contents

- [First](#first)
- [Second](#second)

# First

Text.

# Second

More.
"""
        result = kindle_article.strip_explicit_contents(source)
        self.assertNotIn("# Contents", result)
        self.assertNotIn("[First](#first)", result)
        self.assertIn("# First", result)

    def test_preserves_ordinary_linked_list(self):
        source = """# Resources

- [External](https://example.test)
- [First](#first)

# First
"""
        self.assertEqual(kindle_article.strip_explicit_contents(source), source)

    def test_contents_with_unresolved_target_is_preserved(self):
        source = """# Table of Contents

- [Missing](#missing)

# First
"""
        self.assertIn("Table of Contents", kindle_article.strip_explicit_contents(source))

    def test_heading_promotion_ignores_fenced_code(self):
        source = """## Article Section

```markdown
# Example Heading
```

### Child Section
"""
        normalized = kindle_article.normalize_markdown(source, "Article Title")
        self.assertTrue(normalized.startswith("# Article Section"))
        self.assertIn("\n# Example Heading\n", normalized)
        self.assertIn("\n## Child Section\n", normalized)

    def test_contents_heading_inside_fence_is_preserved(self):
        source = """```markdown
# Contents

- [First](#first)
```

# First
"""
        self.assertEqual(kindle_article.strip_explicit_contents(source), source)

    def test_removes_decorative_stage_number_before_section_heading(self):
        source = """# Plays

Adopt the prerequisites before it.

**01**

# Plan

Capture the intent.
"""
        self.assertEqual(
            kindle_article.normalize_markdown(source, "Article Title"),
            """# Plays

Adopt the prerequisites before it.

# Plan

Capture the intent.
""",
        )

    def test_separates_unindented_image_from_preceding_list(self):
        source = """# Constraints

- First constraint.
- Final constraint.
![Lifecycle diagram](https://example.test/lifecycle.png)
"""
        self.assertEqual(
            kindle_article.normalize_markdown(source, "Article Title"),
            """# Constraints

- First constraint.
- Final constraint.

![Lifecycle diagram](https://example.test/lifecycle.png)
""",
        )

    def test_preserves_image_indented_inside_list_item(self):
        source = """# Constraints

- Constraint with supporting image.
  ![Detail](https://example.test/detail.png)
"""
        self.assertEqual(
            kindle_article.normalize_markdown(source, "Article Title"),
            source,
        )


@unittest.skipUnless(shutil.which("pandoc") and shutil.which("magick"), "Pandoc and ImageMagick required")
class EpubBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.output = self.root / "output"
        self.config = self.root / "config.json"
        kindle_article.write_json(
            self.config,
            {
                "kindle_recipient": "reader@kindle.test",
                "sender": "sender@icloud.test",
                "output_directory": str(self.output),
                "confirmation_required": True,
                "cover_mode": "always-editorial",
                "maximum_attachment_bytes": kindle_article.DEFAULT_MAX_ATTACHMENT,
            },
            mode=0o600,
        )
        article = self.work / "article.md"
        article.write_text(
            """# First Section

Opening text.

![A blue architecture diagram](assets/diagram.png)

## Middle Section

| Stage | State |
|---|---|
| Plan | Ready |

### How to execute it

Middle details.

# Final Section

### How to execute it

Final details.

```text
representative code block that can wrap safely
```
""",
            encoding="utf-8",
        )
        assets = self.work / "assets"
        assets.mkdir()
        subprocess.run(
            ["magick", "-size", "320x180", "xc:#326b9a", str(assets / "diagram.png")],
            check=True,
        )
        metadata = {
            "title": "A Deliberately Long Editorial Article Title About Building Reliable Software with AI-Native Workflows",
            "authors": ["Ada Writer", "Bob Editor"],
            "author": "Ada Writer, Bob Editor",
            "author_source": "visible-byline",
            "author_inferred": False,
            "publisher": "Example Press",
            "publisher_source": "json-ld",
            "published_date": "Aug 21, 2026",
            "language": "en",
            "canonical_url": "https://example.test/article",
            "source_url": "https://example.test/article",
            "accessed_at": "2026-08-27T12:00:00+00:00",
            "images": [
                {
                    "url": "https://example.test/diagram.png",
                    "path": "assets/diagram.png",
                    "alt": "A blue architecture diagram",
                }
            ],
            "source_hero_asset": None,
        }
        kindle_article.write_json(self.work / "metadata.json", metadata)
        kindle_article.write_json(
            self.work / "prepared.json",
            {
                "status": "PASS",
                "article_sha256": kindle_article.sha256_file(article),
                "metadata_sha256": kindle_article.sha256_file(self.work / "metadata.json"),
                "image_count": 1,
            },
        )

    def tearDown(self):
        self.temporary.cleanup()

    def build(self):
        args = argparse.Namespace(
            config=str(self.config),
            work_dir=str(self.work),
            artwork=None,
            approved_cover=None,
            pandoc="pandoc",
            magick="magick",
        )
        kindle_article.command_build(args)
        return next(self.output.glob("*.epub"))

    def test_build_has_single_cover_hidden_nav_matching_ncx_and_article_start(self):
        epub = self.build()
        result = kindle_article.verify_epub_structure(epub, kindle_article.DEFAULT_MAX_ATTACHMENT)
        self.assertEqual(result["cover_items"], 1)
        self.assertFalse(result["navigation_in_spine"])
        self.assertGreaterEqual(result["logical_toc_entries"], 4)
        self.assertTrue(result["reading_start"].startswith("text/ch"))
        identify = subprocess.run(
            ["magick", "identify", "-format", "%wx%h|%[colorspace]", str(self.work / "cover.jpg")],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.lower()
        self.assertEqual(identify, "1600x2560|srgb")
        with kindle_article.zipfile.ZipFile(epub) as archive:
            opf = kindle_article.ET.fromstring(archive.read("EPUB/content.opf"))
            published = opf.find(f".//{{{kindle_article.DC_NS}}}date")
            self.assertEqual(published.text, "2026-08-21")
            nav = kindle_article.ET.fromstring(archive.read("EPUB/nav.xhtml"))
            landmarks = next(
                node
                for node in nav.findall(f".//{{{kindle_article.XHTML_NS}}}nav")
                if "landmarks"
                in node.get(f"{{{kindle_article.EPUB_NS}}}type", "").split()
            )
            anchors = landmarks.findall(f".//{{{kindle_article.XHTML_NS}}}a")
            self.assertEqual(len(anchors), 1)
            self.assertEqual(
                anchors[0].get(f"{{{kindle_article.EPUB_NS}}}type"),
                "bodymatter",
            )

    def test_broken_internal_fragment_fails_structure_verification(self):
        article = self.work / "article.md"
        article.write_text(
            article.read_text(encoding="utf-8") + "\n[Missing](#missing-fragment)\n",
            encoding="utf-8",
        )
        prepared = kindle_article.read_json(self.work / "prepared.json")
        prepared["article_sha256"] = kindle_article.sha256_file(article)
        kindle_article.write_json(self.work / "prepared.json", prepared)
        epub = self.build()
        with self.assertRaisesRegex(kindle_article.KindleArticleError, "fragment"):
            kindle_article.verify_epub_structure(
                epub,
                kindle_article.DEFAULT_MAX_ATTACHMENT,
            )

    def test_epubcheck_only_verification_needs_no_ace_or_previewer(self):
        epub = self.build()
        output = self.root / "verification.json"
        kindle_article.command_verify(
            argparse.Namespace(
                config=str(self.config),
                epub=str(epub),
                epubcheck_command="/usr/bin/true",
                accept_epubcheck_warnings=None,
                output=str(output),
            )
        )
        report = kindle_article.read_json(output)
        self.assertEqual(report["status"], "PASS")
        self.assertNotIn("ace", report)
        self.assertNotIn("kindle_previewer", report)

    def test_collision_is_refused(self):
        self.build()
        with self.assertRaisesRegex(kindle_article.KindleArticleError, "overwrite"):
            self.build()

    def test_article_passes_real_epubcheck_without_mail_config(self):
        epub = self.build()
        report = kindle_article.verify_epub(epub)
        self.assertEqual(report["status"], "PASS")
        self.assertFalse(report["epubcheck"]["warnings"])

    def test_malformed_epub_fails(self):
        malformed = self.root / "bad.epub"
        malformed.write_bytes(b"not a zip")
        with self.assertRaises(Exception):
            kindle_article.verify_epub_structure(malformed, kindle_article.DEFAULT_MAX_ATTACHMENT)


class ValidatorAndDeliveryTests(unittest.TestCase):
    def test_validator_failure_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            validator = root / "validator"
            validator.write_text("#!/bin/sh\necho ERROR >&2\nexit 1\n", encoding="utf-8")
            validator.chmod(0o755)
            epub = root / "sample.epub"
            epub.write_bytes(b"sample")
            with self.assertRaisesRegex(kindle_article.KindleArticleError, "failed"):
                kindle_article.run_external_validator("EPUBCheck", str(validator), epub)

    def test_validator_does_not_treat_zero_warning_summary_as_warning(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            validator = root / "validator"
            validator.write_text(
                "#!/bin/sh\necho 'Check finished with 0 errors and 0 warnings'\n",
                encoding="utf-8",
            )
            validator.chmod(0o755)
            epub = root / "sample.epub"
            epub.write_bytes(b"sample")
            result = kindle_article.run_external_validator(
                "EPUBCheck", str(validator), epub
            )
            self.assertEqual(result["warnings"], [])

    def test_validator_does_not_treat_no_warnings_success_as_warning(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            validator = root / "validator"
            validator.write_text(
                "#!/bin/sh\necho 'No errors or warnings detected.'\n",
                encoding="utf-8",
            )
            validator.chmod(0o755)
            epub = root / "sample.epub"
            epub.write_bytes(b"sample")
            result = kindle_article.run_external_validator(
                "EPUBCheck", str(validator), epub
            )
            self.assertEqual(result["warnings"], [])

    def test_attachment_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            epub = root / "article.epub"
            config = {
                "sender": "sender@icloud.test",
                "kindle_recipient": "reader@kindle.test",
                "maximum_attachment_bytes": 10,
            }
            epub.write_bytes(b"x" * 10)
            payload = kindle_article.message_payload(config, epub, {"title": "Article"})
            self.assertEqual(payload["attachment_size_bytes"], 10)
            epub.write_bytes(b"x" * 11)
            with self.assertRaisesRegex(kindle_article.KindleArticleError, "Mail Drop"):
                kindle_article.message_payload(config, epub, {"title": "Article"})

    def test_failed_download_is_fatal(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            with self.assertRaisesRegex(kindle_article.KindleArticleError, "failed to download"):
                kindle_article.fetch_url("https://example.test/missing.png")

    def test_draft_mode_uses_apple_script_without_send_confirmation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_osascript = root / "osascript"
            fake_osascript.write_text("#!/bin/sh\necho 'draft created'\n", encoding="utf-8")
            fake_osascript.chmod(0o755)
            attachment = root / "article.epub"
            attachment.write_bytes(b"epub")
            payload = {
                "from": "sender@icloud.test",
                "to": "reader@kindle.test",
                "subject": "Article",
                "body": "Body\n",
                "attachment": str(attachment),
                "attachment_name": attachment.name,
                "attachment_size_bytes": 4,
                "mail_drop": "PROHIBITED",
            }
            confirmation = root / "confirmation.json"
            kindle_article.write_json(
                confirmation,
                {
                    "status": "AWAITING_CONFIRMATION",
                    "token": "token",
                    "payload_sha256": kindle_article.hashlib.sha256(
                        json.dumps(payload, sort_keys=True).encode()
                    ).hexdigest(),
                    "expires_at": "2099-01-01T00:00:00+00:00",
                    "payload": payload,
                },
                mode=0o600,
            )
            kindle_article.command_mail(
                argparse.Namespace(
                    mode="draft",
                    confirmation_file=str(confirmation),
                    confirmation_token=None,
                    receipt=str(root / "receipt.json"),
                    osascript=str(fake_osascript),
                )
            )
            self.assertEqual(kindle_article.read_json(root / "receipt.json")["status"], "DRAFT_CREATED")

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("osacompile"), "macOS AppleScript compiler required")
    def test_mail_applescript_compiles(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                ["osacompile", "-o", str(Path(temporary) / "mail.scpt"), str(MAIL_SCRIPT)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0 and "Connection Invalid" in result.stderr:
                self.skipTest("Mail scripting dictionary unavailable in sandbox")
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
