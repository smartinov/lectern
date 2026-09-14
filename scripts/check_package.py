#!/usr/bin/env python3
"""Validate the distributable package without reading private workspace data."""

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def validate(package: Path) -> None:
    package = package.resolve()
    manifests = [json.loads((package / folder / "plugin.json").read_text()) for folder in (".codex-plugin", ".claude-plugin")]
    if any(manifest.get("name") != "lectern" for manifest in manifests):
        raise ValueError("both clients must identify the lectern plugin")
    if manifests[0].get("version") != manifests[1].get("version"):
        raise ValueError("client plugin versions differ")
    required = ["scripts/course.py", "scripts/kindle_article.py", "scripts/mail-kindle.applescript", "templates/course.yaml", "templates/profile.yaml", "templates/epub.css", "requirements.txt"]
    for name in required:
        if not (package / name).is_file():
            raise ValueError(f"package resource missing: {name}")
    forbidden = {"learner", "courses", "articles", "build", ".git", ".venv", "__pycache__"}
    for path in package.rglob("*"):
        if path.name in forbidden or path.suffix in {".epub", ".pyc"}:
            raise ValueError(f"private or generated resource in plugin: {path.relative_to(package)}")
        if path.is_symlink():
            raise ValueError(f"plugin resources must be real files: {path.relative_to(package)}")
    for name in ("lectern", "kindle-article"):
        skill = package / "skills" / name / "SKILL.md"
        text = skill.read_text()
        frontmatter = yaml.safe_load(text.split("---", 2)[1])
        if frontmatter.get("name") != name or not frontmatter.get("description"):
            raise ValueError(f"invalid skill frontmatter: {skill}")
    for path in (package / "skills").rglob("*.md"):
        for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text()):
            if "://" in link or link.startswith("#"):
                continue
            target = (path.parent / link.split("#", 1)[0]).resolve()
            if not target.is_relative_to(package) or not target.exists():
                raise ValueError(f"broken or external package reference in {path.name}: {link}")


def main():
    validate(ROOT / "plugins/lectern")
    codex = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())
    claude = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    for market in (codex, claude):
        if market["name"] != "lectern" or len(market["plugins"]) != 1:
            raise ValueError("unexpected marketplace contents")
        source = market["plugins"][0]["source"]
        if (source["path"] if isinstance(source, dict) else source) != "./plugins/lectern":
            raise ValueError("marketplace does not point to the distributable package")
    print("Plugin manifests, resources, references, and privacy boundaries: PASS")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
