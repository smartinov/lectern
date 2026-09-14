#!/usr/bin/env python3
"""Keep course sources and private learner state in an explicit workspace."""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True

import yaml

import kindle_article as epub

PACKAGE = Path(__file__).resolve().parent.parent
TEMPLATES = PACKAGE / "templates"
STYLES = {"blunt-mentor", "case-study-storyteller", "socratic-guide"}
QUIZ_MARKER = "<!-- lectern:quiz -->"
IDENTIFIER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            epub.fail("YAML keys must be unique strings")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def read_yaml(path: Path) -> dict:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
    except (OSError, yaml.YAMLError) as exc:
        epub.fail(f"cannot read {path}: {exc}")
    if not isinstance(value, dict):
        epub.fail(f"expected a mapping in {path}")
    return value


def write_yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(yaml.safe_dump(value, sort_keys=False, allow_unicode=True))
    path.chmod(0o600)


def contained(root: Path, path: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to(root.resolve()):
        epub.fail(f"path must stay within {root}: {path}")
    return path


def workspace_path(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if root.is_relative_to(PACKAGE):
        epub.fail("workspace must be outside the installed plugin")
    return root


def course_path(workspace: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    courses = contained(workspace, workspace / "courses")
    path = contained(courses, candidate)
    if path.parent != courses or not IDENTIFIER.fullmatch(path.name):
        epub.fail("course must be a courses/<slug> directory with a lowercase hyphenated slug")
    return path


def merged(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if value is None:
            continue
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merged(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def resolve_settings(course: dict, profile: dict) -> dict:
    if not isinstance(profile, dict):
        epub.fail("learner settings must be a mapping")
    defaults = read_yaml(TEMPLATES / "profile.yaml")
    settings = merged(defaults, profile)
    overrides = course.get("personalization") or {}
    if not isinstance(overrides, dict):
        epub.fail("personalization must be a mapping")
    settings = merged(settings, overrides)
    for key in ("language", "reading_minutes", "practice_minutes", "style"):
        if course.get(key) is not None:
            settings[key] = course[key]
    if settings.get("style") not in STYLES:
        epub.fail(f"unknown writing style: {settings.get('style')}")
    required_text(settings.get("language"), "language")
    for key in ("reading_minutes", "practice_minutes"):
        if type(settings.get(key)) is not int or settings[key] <= 0:
            epub.fail(f"{key} must be a positive integer")
    return settings


def command_init(args) -> None:
    workspace = workspace_path(args.workspace)
    course = course_path(workspace, f"courses/{args.slug}")
    if course.exists():
        epub.fail(f"refusing to overwrite existing course: {course}")
    learner = contained(workspace, workspace / "learner")
    learner.mkdir(parents=True, exist_ok=True, mode=0o700)
    profile = contained(learner, learner / "profile.yaml")
    if not profile.exists():
        write_yaml(profile, read_yaml(TEMPLATES / "profile.yaml"))
    course.mkdir(parents=True, mode=0o700)
    (course / "lessons").mkdir()
    (course / "assets").mkdir()
    spec = read_yaml(TEMPLATES / "course.yaml")
    spec["slug"] = args.slug
    write_yaml(course / "course.yaml", spec)
    for name in ("questions.yaml", "sources.yaml", "outline.md"):
        shutil.copyfile(TEMPLATES / name, course / name)
    ignore = contained(workspace, workspace / ".gitignore")
    existing = ignore.read_text() if ignore.exists() else ""
    rules = [f"/{name}/" for name in ("courses", "articles", "learner", "build")]
    missing = [rule for rule in rules if rule not in existing.splitlines()]
    if missing:
        with ignore.open("a") as handle:
            handle.write(("\n" if existing and not existing.endswith("\n") else "") + "\n".join(missing) + "\n")
    print(json.dumps({"course": str(course), "next": "Fill the specification, then resolve the authoring brief"}))


def command_resolve(args) -> None:
    workspace = workspace_path(args.workspace)
    course = course_path(workspace, args.course)
    spec_path = contained(course, course / "course.yaml")
    spec = read_yaml(spec_path)
    profile_path = contained(workspace, workspace / "learner/profile.yaml")
    profile = read_yaml(profile_path) if profile_path.exists() else {}
    settings = resolve_settings(spec, profile)
    brief = {
        "course_sha256": epub.sha256_file(spec_path),
        "settings": settings,
        "session_minutes": settings["reading_minutes"] + settings["practice_minutes"],
    }
    write_yaml(contained(course, course / "brief.yaml"), brief)
    print(json.dumps({"brief": str(course / "brief.yaml"), "style": settings["style"], "session_minutes": brief["session_minutes"]}))


def required_text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        epub.fail(f"{label} must be nonempty text")
    return value.strip()


def identifier(value, label: str) -> str:
    value = required_text(value, label)
    if not IDENTIFIER.fullmatch(value):
        epub.fail(f"invalid {label}: {value}")
    return value


def validate_questions(spec: dict, lessons: list[Path], document: dict) -> list[dict]:
    outcomes = spec.get("outcomes")
    if not isinstance(outcomes, list) or not outcomes:
        epub.fail("course outcomes must contain IDs and descriptions")
    objectives = set()
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            epub.fail("each outcome must contain id and description")
        oid = identifier(outcome.get("id"), "objective id")
        required_text(outcome.get("description"), "objective description")
        if oid in objectives:
            epub.fail(f"duplicate objective id: {oid}")
        objectives.add(oid)
    questions = document.get("questions")
    if not isinstance(questions, list) or not questions:
        epub.fail("questions.yaml must contain questions")
    seen, covered, assessed = set(), set(), set()
    lesson_ids = {lesson.stem for lesson in lessons}
    for question in questions:
        if not isinstance(question, dict):
            epub.fail("each question must be a mapping")
        qid = identifier(question.get("id"), "question id")
        if qid in seen:
            epub.fail(f"duplicate question id: {qid}")
        seen.add(qid)
        if question.get("lesson") not in lesson_ids:
            epub.fail(f"unknown lesson for question {qid}")
        assessed.add(question["lesson"])
        ids = question.get("objectives")
        if not isinstance(ids, list) or not ids or any(not isinstance(oid, str) or oid not in objectives for oid in ids):
            epub.fail(f"unknown or missing objectives for question {qid}")
        covered.update(ids)
        for field in ("prompt", "answer"):
            required_text(question.get(field), f"{qid} {field}")
        rubric = question.get("rubric")
        if not isinstance(rubric, list) or not rubric or any(not isinstance(item, str) or not item.strip() for item in rubric):
            epub.fail(f"question {qid} needs a nonempty rubric")
        kind = question.get("type")
        if kind not in {"free_text", "single_choice", "multiple_choice"}:
            epub.fail(f"unsupported question type: {kind}")
        if kind != "free_text":
            choices, correct = question.get("choices"), question.get("correct")
            if not isinstance(choices, dict) or len(choices) < 2:
                epub.fail(f"question {qid} needs at least two choices")
            for key, value in choices.items():
                identifier(key, "choice id")
                required_text(value, "choice text")
            if not isinstance(correct, list) or not correct or any(not isinstance(key, str) or key not in choices for key in correct):
                epub.fail(f"question {qid} has invalid correct choices")
            if len(correct) != len(set(correct)) or (kind == "single_choice" and len(correct) != 1):
                epub.fail(f"question {qid} has invalid correct choice count")
    if covered != objectives:
        epub.fail(f"unassessed objectives: {', '.join(sorted(objectives - covered))}")
    if assessed != lesson_ids:
        epub.fail(f"lessons without questions: {', '.join(sorted(lesson_ids - assessed))}")
    return questions


def render_quiz(lesson_id: str, questions: list[dict]) -> str:
    parts = [f"## Self-check {{#quiz-{lesson_id}}}\n", "Try each question before following its answer link.\n"]
    for number, question in enumerate(questions, 1):
        qid = question["id"]
        parts.append(f"[]{{#question-{qid}}}\n\n**{number}.** {question['prompt']}\n")
        if question["type"] != "free_text":
            parts.append("Choose one answer.\n" if question["type"] == "single_choice" else "Choose all correct answers.\n")
            parts.extend(f"- **{key}.** {value}\n" for key, value in question["choices"].items())
        parts.append(f"\n[Answer](#answer-{qid})\n\n[]{{#continue-{qid}}}\n")
    return "\n".join(parts)


def render_answers(lessons: list[Path], questions: list[dict]) -> str:
    parts = ["# Answers {#answers}\n"]
    for lesson in lessons:
        parts.append(f"## {lesson.stem} {{#answers-{lesson.stem}}}\n")
        for question in (q for q in questions if q["lesson"] == lesson.stem):
            qid = question["id"]
            parts.append(f"[]{{#answer-{qid}}}\n\n**{qid}**\n")
            if question["type"] != "free_text":
                parts.append(f"Correct choices: {', '.join(question['correct'])}.\n")
            parts.append(question["answer"] + "\n\nCheck your explanation:\n")
            parts.extend(f"- {item}\n" for item in question["rubric"])
            parts.append(f"\n[Continue in the lesson](#continue-{qid})\n")
    return "\n".join(parts)


def render_sources(document: dict) -> str:
    sources = document.get("sources")
    if not isinstance(sources, list) or not sources:
        epub.fail("sources.yaml must contain verified sources")
    parts, seen = ["# Source notes {#source-notes}\n"], set()
    for source in sources:
        if not isinstance(source, dict):
            epub.fail("each source must be a mapping")
        sid = identifier(source.get("id"), "source id")
        if sid in seen:
            epub.fail(f"duplicate source id: {sid}")
        seen.add(sid)
        for field in ("title", "url", "author", "accessed", "finding"):
            required_text(source.get(field), f"source {sid} {field}")
        if urllib.parse.urlsplit(source["url"]).scheme not in {"http", "https"}:
            epub.fail(f"source {sid} needs an HTTP(S) URL")
        parts.append(f"## {source['title']} {{#source-{sid}}}\n\n{source['author']}. "
                     f"{source.get('date') or 'Publication date not stated'}. "
                     f"Accessed {source['accessed']}.\n\n{source['finding']}\n\n[Original source]({source['url']})\n")
        if source.get("limitations"):
            parts.append(f"Limitations: {required_text(source['limitations'], 'source limitations')}\n")
    return "\n".join(parts)


def check_media(text: str, course: Path, pandoc: str) -> None:
    result = subprocess.run([pandoc, "--from=markdown-raw_html", "--to=json"], input=text, text=True, capture_output=True, check=True)

    def walk(value):
        if isinstance(value, dict):
            if value.get("t") == "Image":
                target = value["c"][-1][0]
                parsed = urllib.parse.urlsplit(target)
                if parsed.scheme or parsed.netloc:
                    epub.fail(f"images must be localized for offline reading: {target}")
                image = contained(course, course / urllib.parse.unquote(parsed.path))
                if not image.is_file():
                    epub.fail(f"missing image: {target}")
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(json.loads(result.stdout))


def command_build(args) -> None:
    workspace = workspace_path(args.workspace)
    course = course_path(workspace, args.course)
    spec_file = contained(course, course / "course.yaml")
    spec = read_yaml(spec_file)
    title = required_text(spec.get("title"), "course title")
    if spec.get("slug") != course.name:
        epub.fail("course slug must match its directory")
    brief = read_yaml(contained(course, course / "brief.yaml"))
    if brief.get("course_sha256") != epub.sha256_file(spec_file):
        epub.fail("course specification changed; resolve and review the authoring brief again")
    settings = resolve_settings({}, brief.get("settings", {}))
    lessons_dir = contained(course, course / "lessons")
    lessons = sorted(contained(course, path) for path in lessons_dir.glob("*.md"))
    if not lessons or type(spec.get("lessons")) is not int or len(lessons) != spec["lessons"]:
        epub.fail("lesson files must match the lesson count in course.yaml")
    for lesson in lessons:
        identifier(lesson.stem, "lesson filename")
    questions_file = contained(course, course / "questions.yaml")
    sources_file = contained(course, course / "sources.yaml")
    questions = validate_questions(spec, lessons, read_yaml(questions_file))
    source_notes = render_sources(read_yaml(sources_file))
    texts = []
    for lesson in lessons:
        text = lesson.read_text(encoding="utf-8")
        if text.count(QUIZ_MARKER) != 1:
            epub.fail(f"{lesson.name} must contain exactly one {QUIZ_MARKER}")
        text = text.replace(QUIZ_MARKER, render_quiz(lesson.stem, [q for q in questions if q["lesson"] == lesson.stem]))
        texts.append(text)
    text = "\n\n".join([*texts, render_answers(lessons, questions), source_notes])
    check_media(text, course, args.pandoc)
    build = contained(workspace, workspace / "build")
    build.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = contained(build, build / f"{course.name}.epub")
    metadata = {"title": title, "author": spec.get("author") or "", "language": settings["language"], "content_kind": "course"}
    with tempfile.TemporaryDirectory(prefix=f".{course.name}-", dir=build) as temporary:
        staging = Path(temporary)
        cover_source = contained(course, course / "cover.jpg")
        if cover_source.exists():
            epub.validate_cover(cover_source, args.magick)
            cover = cover_source
        else:
            cover = epub.create_cover(staging, metadata, None, args.magick)
        manuscript = staging / "course.md"
        manuscript.write_text(text, encoding="utf-8")
        pandoc_metadata = {"title": title, "lang": settings["language"], "identifier": f"urn:lectern:{course.name}"}
        if metadata["author"]:
            pandoc_metadata["author"] = metadata["author"]
        metadata_file = staging / "metadata.json"
        epub.write_json(metadata_file, pandoc_metadata)
        draft = staging / "course.epub"
        epub.run_checked([
            args.pandoc, str(manuscript), "--from=markdown-raw_html", "--to=epub3", "--toc", "--toc-depth=2", "--split-level=1",
            f"--css={TEMPLATES / 'epub.css'}", f"--epub-cover-image={cover}", f"--resource-path={course}",
            f"--metadata-file={metadata_file}", f"--output={draft}",
        ], "course EPUB build", cwd=course)
        unpacked = staging / "unpacked"
        with zipfile.ZipFile(draft) as archive:
            epub.zip_safety(archive)
            archive.extractall(unpacked)
        epub.patch_navigation(unpacked)
        epub.repack_epub(unpacked, draft)
        report = epub.verify_epub(draft, args.epubcheck_command)
        report["epub"] = str(output)
        report["assessment_questions"] = len(questions)
        inputs = [spec_file, course / "brief.yaml", questions_file, sources_file, *lessons]
        inputs.extend(path for path in (course / "assets").rglob("*") if path.is_file())
        if cover_source.exists():
            inputs.append(cover_source)
        report["source_sha256"] = {str(path.relative_to(course)): epub.sha256_file(contained(course, path)) for path in inputs}
        draft.replace(output)
        epub.write_json(contained(build, output.with_suffix(".verification.json")), report)
        epub.write_json(contained(build, output.with_suffix(".metadata.json")), metadata)
    print(json.dumps({"epub": str(output), "status": "PASS", "visual_inspection": "NOT_RUN"}))


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--workspace", required=True)
    init.add_argument("slug")
    init.set_defaults(handler=command_init)
    resolve = commands.add_parser("resolve")
    resolve.add_argument("--workspace", required=True)
    resolve.add_argument("course")
    resolve.set_defaults(handler=command_resolve)
    build = commands.add_parser("build")
    build.add_argument("--workspace", required=True)
    build.add_argument("course")
    build.add_argument("--pandoc", default="pandoc")
    build.add_argument("--magick", default="magick")
    build.add_argument("--epubcheck-command", default="epubcheck")
    build.set_defaults(handler=command_build)
    return root


def main():
    args = parser().parse_args()
    try:
        args.handler(args)
    except (epub.KindleArticleError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
