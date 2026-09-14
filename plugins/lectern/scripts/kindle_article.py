#!/usr/bin/env python3
"""Prepare, build, verify, and deliver article EPUBs for Kindle."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import html
import json
import mimetypes
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from xml.etree import ElementTree as ET


DEFAULT_CONFIG = Path.home() / ".config" / "kindle-article" / "config.json"
DEFAULT_MAX_ATTACHMENT = 14_680_064
MAX_COVER_BYTES = 5_000_000
MAX_HTML_BYTES = 30_000_000
MAX_HTML_FILES = 299
CONFIRMATION_TTL_SECONDS = 600
USER_AGENT = "kindle-article/0.1 (+personal article conversion)"

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_NS = "http://www.idpf.org/2007/ops"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
XML_NS = "http://www.w3.org/XML/1998/namespace"

ET.register_namespace("", OPF_NS)
ET.register_namespace("dc", DC_NS)
ET.register_namespace("opf", OPF_NS)


class KindleArticleError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise KindleArticleError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    if mode is not None:
        temporary.chmod(mode)
    os.replace(temporary, path)
    if mode is not None:
        path.chmod(mode)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read JSON from {path}: {exc}")
    if not isinstance(value, dict):
        fail(f"expected a JSON object in {path}")
    return value


def config_path(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    override = os.environ.get("KINDLE_ARTICLE_CONFIG")
    return Path(override).expanduser().resolve() if override else DEFAULT_CONFIG


def load_config(value: str | None) -> dict[str, Any]:
    path = config_path(value)
    config = read_json(path)
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600:
        fail(f"configuration must have mode 0600, found {mode:04o}: {path}")
    required = {
        "kindle_recipient",
        "sender",
        "output_directory",
        "confirmation_required",
        "cover_mode",
        "maximum_attachment_bytes",
    }
    missing = sorted(required - set(config))
    if missing:
        fail(f"configuration is missing: {', '.join(missing)}")
    if config["confirmation_required"] is not True:
        fail("delivery confirmation cannot be disabled")
    if config["cover_mode"] != "always-editorial":
        fail("cover_mode must be 'always-editorial'")
    return config


def command_configure(args: argparse.Namespace) -> None:
    path = config_path(args.config)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    payload = {
        "kindle_recipient": args.kindle_recipient,
        "sender": args.sender,
        "output_directory": str(Path(args.output_directory).expanduser().resolve()),
        "confirmation_required": True,
        "cover_mode": "always-editorial",
        "maximum_attachment_bytes": args.maximum_attachment_bytes,
    }
    write_json(path, payload, mode=0o600)
    print(json.dumps({"status": "configured", "path": str(path), "mode": "0600"}))


@dataclass
class PageSignals:
    metas: dict[str, list[str]] = field(default_factory=dict)
    json_ld: list[Any] = field(default_factory=list)
    visible_byline: list[str] = field(default_factory=list)
    title_text: list[str] = field(default_factory=list)
    h1_text: list[str] = field(default_factory=list)
    canonical: str | None = None
    language: str | None = None


class MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.signals = PageSignals()
        self._stack: list[tuple[str, bool, bool, list[str]]] = []
        self._script_type: str | None = None
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        tag = tag.lower()
        if tag == "html" and values.get("lang"):
            self.signals.language = values["lang"]
        if tag == "meta":
            key = (values.get("name") or values.get("property") or "").lower()
            content = values.get("content", "").strip()
            if key and content:
                self.signals.metas.setdefault(key, []).append(content)
        if tag == "link" and values.get("rel", "").lower() == "canonical":
            self.signals.canonical = values.get("href") or self.signals.canonical
        if tag == "script" and "ld+json" in values.get("type", "").lower():
            self._script_type = "json-ld"
            self._script_parts = []

        marker = " ".join(
            [
                values.get("class", ""),
                values.get("id", ""),
                values.get("rel", ""),
                values.get("itemprop", ""),
            ]
        ).lower()
        excluded = any(word in marker for word in ("acknowledg", "contributor", "thanks"))
        blocked = excluded or any(entry[2] for entry in self._stack)
        is_byline = not blocked and any(word in marker for word in ("byline", "author"))
        self._stack.append((tag, is_byline, blocked, []))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._script_type == "json-ld":
            self._script_parts.append(data)
        for index, (tag, is_byline, blocked, parts) in enumerate(self._stack):
            if is_byline or tag in {"title", "h1"}:
                parts.append(data)
                self._stack[index] = (tag, is_byline, blocked, parts)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._script_type == "json-ld":
            raw = "".join(self._script_parts).strip()
            if raw:
                with contextlib.suppress(json.JSONDecodeError):
                    self.signals.json_ld.append(json.loads(raw))
            self._script_type = None
            self._script_parts = []

        for index in range(len(self._stack) - 1, -1, -1):
            current_tag, is_byline, _blocked, parts = self._stack[index]
            if current_tag != tag.lower():
                continue
            del self._stack[index:]
            text = clean_text(" ".join(parts))
            if text:
                if is_byline:
                    self.signals.visible_byline.append(text)
                if current_tag == "title":
                    self.signals.title_text.append(text)
                if current_tag == "h1":
                    self.signals.h1_text.append(text)
            break


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = clean_text(value)
        key = cleaned.casefold()
        if cleaned and key not in seen:
            result.append(cleaned)
            seen.add(key)
    return result


def split_authors(value: str) -> list[str]:
    value = re.sub(
        r"^\s*(?:(?:written\s+)?by|authors?\s*(?:\(s\))?)\s*:?[\s]+",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\s+(?:and|&)\s+", ",", value)
    return unique(part.strip() for part in value.split(",") if part.strip())


def normalize_published_date(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    value = value.strip()
    if match := re.match(r"^(\d{4}-\d{2}-\d{2})", value):
        return match.group(1)
    for date_format in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return dt.datetime.strptime(value, date_format).date().isoformat()
        except ValueError:
            continue
    return None


def flatten_json_ld(values: Iterable[Any]) -> Iterable[dict[str, Any]]:
    for value in values:
        if isinstance(value, dict):
            yield value
            graph = value.get("@graph")
            if isinstance(graph, list):
                yield from flatten_json_ld(graph)
        elif isinstance(value, list):
            yield from flatten_json_ld(value)


def json_ld_names(value: Any) -> list[str]:
    if isinstance(value, str):
        return split_authors(value)
    if isinstance(value, dict):
        name = value.get("name")
        return [clean_text(name)] if isinstance(name, str) and clean_text(name) else []
    if isinstance(value, list):
        return unique(name for item in value for name in json_ld_names(item))
    return []


def article_json_ld(signals: PageSignals) -> list[dict[str, Any]]:
    documents = list(flatten_json_ld(signals.json_ld))
    article_types = {"article", "newsarticle", "blogposting", "techarticle"}
    selected = []
    for document in documents:
        raw_type = document.get("@type")
        types = raw_type if isinstance(raw_type, list) else [raw_type]
        if any(isinstance(value, str) and value.lower() in article_types for value in types):
            selected.append(document)
    return selected or documents


def first_meta(signals: PageSignals, *keys: str) -> str | None:
    for key in keys:
        values = signals.metas.get(key.lower(), [])
        if values:
            return values[0]
    return None


def infer_publisher(signals: PageSignals, url: str) -> tuple[str | None, str | None]:
    for document in article_json_ld(signals):
        names = json_ld_names(document.get("publisher"))
        if names:
            return names[0], "json-ld"
    site_name = first_meta(signals, "og:site_name", "application-name")
    if site_name:
        return clean_text(site_name), "open-graph"
    return None, None


def resolve_metadata(page_html: str, url: str) -> dict[str, Any]:
    parser = MetadataParser()
    parser.feed(page_html)
    signals = parser.signals
    documents = article_json_ld(signals)

    title = None
    title_source = None
    for document in documents:
        candidate = document.get("headline") or document.get("name")
        if isinstance(candidate, str) and clean_text(candidate):
            title, title_source = clean_text(candidate), "json-ld"
            break
    if not title:
        candidate = first_meta(signals, "og:title", "twitter:title")
        if candidate:
            title, title_source = clean_text(candidate), "open-graph"
    if not title and signals.h1_text:
        title, title_source = signals.h1_text[0], "visible-heading"
    if not title and signals.title_text:
        title, title_source = signals.title_text[0], "html-title"
    if not title:
        fail("article title could not be resolved")

    authors: list[str] = []
    author_source = None
    for byline in signals.visible_byline:
        authors = split_authors(byline)
        if authors:
            author_source = "visible-byline"
            break
    if not authors:
        for document in documents:
            authors = json_ld_names(document.get("author"))
            if authors:
                author_source = "json-ld"
                break
    if not authors:
        standard = first_meta(signals, "author", "parsely-author", "byl")
        if standard:
            authors, author_source = split_authors(standard), "standard-metadata"
    if not authors:
        social = first_meta(signals, "article:author", "og:article:author", "twitter:creator")
        if social:
            authors, author_source = split_authors(social.lstrip("@")), "open-graph"

    publisher, publisher_source = infer_publisher(signals, url)
    author_inferred = False
    if not authors and publisher:
        authors = [publisher]
        author_source = f"explicit-publisher-fallback:{publisher_source}"
        author_inferred = True
    if not authors:
        fail("article authorship could not be resolved or explicitly inferred")

    published_date = None
    for document in documents:
        value = document.get("datePublished")
        if isinstance(value, str) and value.strip():
            published_date = value.strip()
            break
    published_date = published_date or first_meta(
        signals, "article:published_time", "date", "datepublished"
    )
    published_date = normalize_published_date(published_date)
    language = (
        signals.language
        or first_meta(signals, "og:locale", "content-language")
        or "en"
    ).replace("_", "-")

    return {
        "title": title,
        "title_source": title_source,
        "authors": authors,
        "author": ", ".join(authors),
        "author_source": author_source,
        "author_inferred": author_inferred,
        "publisher": publisher,
        "publisher_source": publisher_source,
        "published_date": published_date,
        "language": language,
        "canonical_url": urllib.parse.urljoin(url, signals.canonical) if signals.canonical else url,
        "hero_url": first_meta(signals, "og:image", "twitter:image"),
    }


def fetch_url(url: str) -> tuple[bytes, str | None]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
            content_type = response.headers.get_content_type()
    except Exception as exc:
        fail(f"failed to download {url}: {exc}")
    if not payload:
        fail(f"downloaded resource is empty: {url}")
    return payload, content_type


def heading_slug(value: str) -> str:
    value = re.sub(r"[`*_~]", "", value)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^a-zA-Z0-9\s-]", "", value).strip().lower()
    return re.sub(r"[-\s]+", "-", value)


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
LINK_LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)\[([^]]+)]\(#([^)]+)\)\s*$")
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)\S")
IMAGE_RE = re.compile(r"!\[([^]]*)]\((https?://[^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)")


def markdown_headings(lines: list[str]) -> list[tuple[int, re.Match[str]]]:
    result: list[tuple[int, re.Match[str]]] = []
    fence: str | None = None
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        marker_match = re.match(r"(`{3,}|~{3,})", stripped)
        if marker_match:
            marker = marker_match.group(1)[0]
            fence = None if fence == marker else marker if fence is None else fence
            continue
        if fence is None and (match := HEADING_RE.match(line)):
            result.append((index, match))
    return result


def strip_explicit_contents(markdown: str) -> str:
    lines = markdown.splitlines()
    heading_indexes = {index for index, _match in markdown_headings(lines)}
    heading_ids = {
        explicit or heading_slug(text)
        for _index, match in markdown_headings(lines)
        for text, explicit in [
            (
                re.sub(r"\s*\{#[-\w:.]+}\s*$", "", match.group(2)),
                (
                    re.search(r"\{#([-\w:.]+)}\s*$", match.group(2)).group(1)
                    if re.search(r"\{#([-\w:.]+)}\s*$", match.group(2))
                    else ""
                ),
            )
        ]
    }
    index = 0
    result: list[str] = []
    while index < len(lines):
        match = HEADING_RE.match(lines[index]) if index in heading_indexes else None
        label = clean_text(match.group(2)) if match else ""
        if label.casefold() not in {"table of contents", "contents"}:
            result.append(lines[index])
            index += 1
            continue
        end = index + 1
        while end < len(lines) and not lines[end].strip():
            end += 1
        link_start = end
        targets: list[str] = []
        while end < len(lines):
            if not lines[end].strip():
                end += 1
                continue
            link_match = LINK_LIST_RE.match(lines[end])
            if not link_match:
                break
            targets.append(link_match.group(2))
            end += 1
        if targets and link_start < end and all(target in heading_ids for target in targets):
            while result and not result[-1].strip():
                result.pop()
            result.append("")
            index = end
            continue
        result.append(lines[index])
        index += 1
    return "\n".join(result).strip() + "\n"


def normalize_markdown(markdown: str, title: str) -> str:
    markdown = strip_explicit_contents(markdown)
    lines = markdown.splitlines()
    separated_lines: list[str] = []
    fence: str | None = None
    for line in lines:
        stripped = line.lstrip()
        marker_match = re.match(r"(`{3,}|~{3,})", stripped)
        if marker_match:
            marker = marker_match.group(1)[0]
            fence = None if fence == marker else marker if fence is None else fence
        if (
            fence is None
            and line == stripped
            and IMAGE_RE.fullmatch(stripped.strip())
            and separated_lines
            and LIST_ITEM_RE.match(separated_lines[-1])
        ):
            separated_lines.append("")
        separated_lines.append(line)
    lines = separated_lines
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and (match := HEADING_RE.match(lines[0])):
        candidate = re.sub(r"\s*\{#[-\w:.]+}\s*$", "", match.group(2))
        if clean_text(candidate).casefold() == clean_text(title).casefold():
            lines.pop(0)
            while lines and not lines[0].strip():
                lines.pop(0)
    headings = markdown_headings(lines)
    if headings:
        top_level = min(len(match.group(1)) for _index, match in headings)
        decorative_lines: set[int] = set()
        for index, match in headings:
            if len(match.group(1)) != top_level:
                continue
            previous = index - 1
            while previous >= 0 and not lines[previous].strip():
                previous -= 1
            if previous >= 0 and re.fullmatch(
                r"\*\*0[1-9]\*\*", lines[previous].strip()
            ):
                decorative_lines.add(previous)
                if previous + 1 < len(lines) and not lines[previous + 1].strip():
                    decorative_lines.add(previous + 1)
        lines = [line for index, line in enumerate(lines) if index not in decorative_lines]
    heading_levels = [len(match.group(1)) for _index, match in markdown_headings(lines)]
    if heading_levels:
        shift = min(heading_levels) - 1
        if shift:
            heading_indexes = {index: match for index, match in markdown_headings(lines)}
            lines = [
                "#" * (len(heading_indexes[index].group(1)) - shift)
                + line[len(heading_indexes[index].group(1)) :]
                if index in heading_indexes
                else line
                for index, line in enumerate(lines)
            ]
    return "\n".join(lines).strip() + "\n"


def repair_internal_links(markdown: str) -> str:
    lines = markdown.splitlines()
    heading_labels: dict[str, str] = {}
    valid_targets: set[str] = set()
    for _index, match in markdown_headings(lines):
        raw = match.group(2)
        explicit_match = re.search(r"\{#([-\w:.]+)}\s*$", raw)
        label = clean_text(re.sub(r"\s*\{#[-\w:.]+}\s*$", "", raw))
        target = explicit_match.group(1) if explicit_match else heading_slug(label)
        valid_targets.add(target)
        heading_labels[label.casefold()] = target

    link_pattern = re.compile(r"(?<!!)\[([^]]+)]\(#([^)]+)\)")

    def replace(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        if target in valid_targets:
            return match.group(0)
        normalized_label = clean_text(
            re.sub(r"^Stage\s+\d+\s*:\s*", "", label, flags=re.IGNORECASE)
        )
        replacement = heading_labels.get(normalized_label.casefold())
        return f"[{label}](#{replacement})" if replacement else match.group(0)

    return "\n".join(link_pattern.sub(replace, line) for line in lines).strip() + "\n"


def infer_image_alt(lines: list[str], index: int) -> str:
    for candidate in lines[index + 1 : index + 5]:
        value = candidate.strip()
        if not value:
            continue
        if HEADING_RE.match(value) or value.startswith(("|", "```", "- ", "* ")):
            return "Illustration from the original article"
        value = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", value)
        value = re.sub(r"[`*_~]", "", value)
        value = clean_text(value)
        if value:
            return value[:180]
    return "Illustration from the original article"


def safe_extension(url: str, content_type: str | None) -> str:
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    guessed = mimetypes.guess_extension(content_type or "") or ".bin"
    return ".jpg" if guessed == ".jpe" else guessed


def persist_kindle_image(
    payload: bytes,
    url: str,
    content_type: str | None,
    target_stem: Path,
    magick: str,
) -> Path:
    extension = safe_extension(url, content_type)
    target = target_stem.with_suffix(extension)
    target.write_bytes(payload)
    if extension == ".webp":
        converted = target_stem.with_suffix(".png")
        try:
            subprocess.run([magick, str(target), str(converted)], check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            fail(f"could not convert unsupported WebP resource {url}: {exc}")
        target.unlink()
        target = converted
    if target.suffix.lower() not in {".jpg", ".png", ".gif", ".svg"}:
        fail(f"unsupported Kindle image resource type for {url}: {target.suffix}")
    if not target.is_file() or target.stat().st_size == 0:
        fail(f"failed to persist article resource: {url}")
    return target


def localize_images(
    markdown: str,
    work_dir: Path,
    magick: str,
) -> tuple[str, list[dict[str, str]]]:
    lines = markdown.splitlines()
    assets = work_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    downloaded: dict[str, str] = {}
    records: list[dict[str, str]] = []

    for index, line in enumerate(lines):
        def replace(match: re.Match[str]) -> str:
            alt, url = match.group(1).strip(), match.group(2)
            if url not in downloaded:
                payload, content_type = fetch_url(url)
                target = persist_kindle_image(
                    payload,
                    url,
                    content_type,
                    assets / f"image-{len(downloaded) + 1:02d}",
                    magick,
                )
                filename = target.name
                downloaded[url] = f"assets/{filename}"
            localized = downloaded[url]
            description = alt or infer_image_alt(lines, index)
            records.append({"url": url, "path": localized, "alt": description})
            return f"![{description}]({localized})"

        lines[index] = IMAGE_RE.sub(replace, line)
    return "\n".join(lines).strip() + "\n", records


def download_hero(metadata: dict[str, Any], work_dir: Path) -> str | None:
    url = metadata.get("hero_url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    payload, content_type = fetch_url(url)
    target = work_dir / "assets" / f"source-hero{safe_extension(url, content_type)}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return str(target.relative_to(work_dir))


def command_prepare(args: argparse.Namespace) -> None:
    work_dir = Path(args.work_dir).expanduser().resolve()
    if work_dir.exists() and any(work_dir.iterdir()):
        fail(f"work directory is not empty: {work_dir}")
    work_dir.mkdir(parents=True, exist_ok=True)
    raw_path = work_dir / "defuddle.md"
    command = [args.defuddle, "parse", args.url, "--md", "-o", str(raw_path)]
    try:
        subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        fail(f"Defuddle extraction failed: {exc}")
    if not raw_path.is_file() or raw_path.stat().st_size == 0:
        fail("Defuddle produced no article content")

    page_bytes, _ = fetch_url(args.url)
    page_html = page_bytes.decode("utf-8", errors="replace")
    metadata = resolve_metadata(page_html, args.url)
    if args.author:
        metadata["authors"] = args.author
        metadata["author"] = ", ".join(args.author)
        metadata["author_source"] = "explicit-user-override"
        metadata["author_inferred"] = False
    if args.publisher:
        metadata["publisher"] = args.publisher
        metadata["publisher_source"] = "explicit-user-override"

    normalized = repair_internal_links(
        normalize_markdown(raw_path.read_text(encoding="utf-8"), metadata["title"])
    )
    localized, images = localize_images(normalized, work_dir, args.magick)
    article_path = work_dir / "article.md"
    article_path.write_text(localized, encoding="utf-8")
    metadata.update(
        {
            "source_url": args.url,
            "accessed_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            "images": images,
            "source_hero_asset": download_hero(metadata, work_dir),
        }
    )
    write_json(work_dir / "metadata.json", metadata)
    write_json(
        work_dir / "prepared.json",
        {
            "status": "PASS",
            "article_sha256": sha256_file(article_path),
            "metadata_sha256": sha256_file(work_dir / "metadata.json"),
            "image_count": len(images),
        },
    )
    print(json.dumps({"status": "prepared", "work_dir": str(work_dir), **metadata}))


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(char for char in normalized if not unicodedata.combining(char))
    ascii_value = re.sub(r"[^A-Za-z0-9]+", "-", ascii_value).strip("-").lower()
    return ascii_value[:100] or "article"


def run_checked(command: list[str], label: str, cwd: Path | None = None) -> None:
    try:
        subprocess.run(command, cwd=cwd, check=True)
    except FileNotFoundError:
        fail(f"missing dependency for {label}: {command[0]}")
    except subprocess.CalledProcessError as exc:
        fail(f"{label} failed with exit code {exc.returncode}")


def create_geometric_background(path: Path, magick: str) -> None:
    run_checked(
        [
            magick,
            "-size",
            "1600x2560",
            "gradient:#111827-#9A4E3F",
            "-fill",
            "rgba(240,180,90,0.32)",
            "-draw",
            "polygon 0,1800 1600,950 1600,1600 0,2450",
            "-fill",
            "rgba(70,150,190,0.24)",
            "-draw",
            "polygon 0,400 1600,0 1600,520 0,1050",
            str(path),
        ],
        "deterministic cover background",
    )


def cover_font(bold: bool) -> str:
    candidates = (
        [
            Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
            Path("/Library/Fonts/Microsoft/Arial Bold.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ]
        if bold
        else [
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
            Path("/Library/Fonts/Microsoft/Arial.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    fail("no supported local font is available for deterministic cover typography")


def validate_cover(cover: Path, magick: str) -> None:
    if not cover.is_file():
        fail(f"cover does not exist: {cover}")
    if cover.stat().st_size > MAX_COVER_BYTES:
        fail(f"cover exceeds {MAX_COVER_BYTES} bytes: {cover.stat().st_size}")
    try:
        identify = subprocess.run(
            [magick, "identify", "-format", "%wx%h|%[colorspace]", str(cover)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        fail(f"cover inspection failed: {exc}")
    if identify.lower() != "1600x2560|srgb":
        fail(f"cover must be 1600x2560 sRGB, found {identify}")


def create_cover(work_dir: Path, metadata: dict[str, Any], artwork: Path | None, magick: str) -> Path:
    cover = work_dir / "cover.jpg"
    background = work_dir / ".cover-background.png"
    if artwork is None:
        source = metadata.get("source_hero_asset")
        candidate = work_dir / source if isinstance(source, str) else None
        artwork = candidate if candidate and candidate.is_file() else None
    if artwork:
        run_checked(
            [
                magick,
                str(artwork),
                "-auto-orient",
                "-resize",
                "1600x2560^",
                "-gravity",
                "center",
                "-extent",
                "1600x2560",
                "-colorspace",
                "sRGB",
                "-fill",
                "rgba(0,0,0,0.42)",
                "-colorize",
                "42%",
                str(background),
            ],
            "cover artwork normalization",
        )
    else:
        create_geometric_background(background, magick)

    title = metadata["title"]
    author = metadata["author"]
    run_checked(
        [
            magick,
            str(background),
            "(",
            "-size",
            "1280x1150",
            "-background",
            "none",
            "-fill",
            "white",
            "-font",
            cover_font(True),
            "-gravity",
            "center",
            f"caption:{title}",
            ")",
            "-gravity",
            "center",
            "-geometry",
            "+0-180",
            "-composite",
            "(",
            "-size",
            "1280x260",
            "-background",
            "none",
            "-fill",
            "white",
            "-font",
            cover_font(False),
            "-gravity",
            "center",
            f"caption:{author}",
            ")",
            "-gravity",
            "south",
            "-geometry",
            "+0+210",
            "-composite",
            "-strip",
            "-colorspace",
            "sRGB",
            "-quality",
            "88",
            str(cover),
        ],
        "cover typography",
    )
    background.unlink(missing_ok=True)
    if cover.stat().st_size > MAX_COVER_BYTES:
        run_checked([magick, str(cover), "-strip", "-quality", "76", str(cover)], "cover compression")
    validate_cover(cover, magick)
    return cover


def command_cover(args: argparse.Namespace) -> None:
    work_dir = Path(args.work_dir).expanduser().resolve()
    metadata = read_json(work_dir / "metadata.json")
    artwork = Path(args.artwork).expanduser().resolve() if args.artwork else None
    if artwork and not artwork.is_file():
        fail(f"cover artwork does not exist: {artwork}")
    cover = create_cover(work_dir, metadata, artwork, args.magick)
    write_json(
        work_dir / "cover.json",
        {
            "status": "AWAITING_APPROVAL",
            "cover": str(cover),
            "cover_sha256": sha256_file(cover),
            "artwork": str(artwork) if artwork else None,
        },
    )
    print(json.dumps({"status": "AWAITING_APPROVAL", "cover": str(cover)}))


EPUB_CSS = """\
body { margin-left: 0; margin-right: 0; }
h1, h2, h3, h4, h5, h6 { text-align: left; break-after: avoid; }
p { orphans: 2; widows: 2; }
img { display: block; max-width: 100%; height: auto; object-fit: contain; margin: 1em auto; }
figure { break-inside: avoid; margin: 1em 0; }
figcaption { font-size: 0.9em; text-align: center; }
table { border-collapse: collapse; width: 100%; font-size: 0.9em; }
th, td { border: 1px solid #888; padding: 0.3em; vertical-align: top; }
th { text-align: left; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; }
code { font-size: 0.9em; }
a { text-decoration: underline; }
.article-attribution { margin-top: 2em; font-size: 0.85em; }
"""


def attribution(metadata: dict[str, Any]) -> str:
    parts = [
        "\n\n---\n\n<section class=\"article-attribution\" epub:type=\"colophon\">\n",
        "## Source and attribution\n\n",
        f"**Author:** {metadata['author']}  \n",
    ]
    if metadata.get("publisher"):
        parts.append(f"**Publisher:** {metadata['publisher']}  \n")
    if metadata.get("published_date"):
        parts.append(f"**Published:** {metadata['published_date']}  \n")
    parts.extend(
        [
            f"**Original article:** [{metadata['canonical_url']}]({metadata['canonical_url']})  \n",
            f"**Retrieved:** {metadata['accessed_at']}\n\n",
            "</section>\n",
        ]
    )
    return "".join(parts)


def opf_path(root: Path) -> Path:
    container = ET.parse(root / "META-INF" / "container.xml").getroot()
    rootfile = container.find(f".//{{{CONTAINER_NS}}}rootfile")
    if rootfile is None or not rootfile.get("full-path"):
        fail("EPUB container does not identify a package document")
    path = root / PurePosixPath(rootfile.get("full-path", ""))
    if not path.is_file():
        fail(f"EPUB package document is missing: {path}")
    return path


def package_items(opf_root: ET.Element) -> dict[str, ET.Element]:
    return {
        item.get("id", ""): item
        for item in opf_root.findall(f".//{{{OPF_NS}}}manifest/{{{OPF_NS}}}item")
        if item.get("id")
    }


def remove_manifest_item(opf_root: ET.Element, item: ET.Element, opf_file: Path) -> None:
    manifest = opf_root.find(f"{{{OPF_NS}}}manifest")
    if manifest is not None:
        manifest.remove(item)
    href = item.get("href")
    if href:
        (opf_file.parent / PurePosixPath(href)).unlink(missing_ok=True)


def patch_navigation(root: Path) -> None:
    opf_file = opf_path(root)
    tree = ET.parse(opf_file)
    package = tree.getroot()
    items = package_items(package)
    spine = package.find(f"{{{OPF_NS}}}spine")
    if spine is None:
        fail("EPUB spine is missing")

    removable_ids = {
        item_id
        for item_id, item in items.items()
        if item.get("href", "").endswith(("cover.xhtml", "title_page.xhtml"))
    }
    nav_ids = {
        item_id
        for item_id, item in items.items()
        if "nav" in item.get("properties", "").split()
    }
    for itemref in list(spine):
        if itemref.get("idref") in removable_ids | nav_ids:
            spine.remove(itemref)
    if not list(spine):
        fail("EPUB has no article content in its spine")

    for item_id in removable_ids:
        remove_manifest_item(package, items[item_id], opf_file)

    guide = package.find(f"{{{OPF_NS}}}guide")
    if guide is not None:
        for reference in list(guide):
            if reference.get("type") == "cover" or reference.get("href", "").endswith(
                ("cover.xhtml", "title_page.xhtml")
            ):
                guide.remove(reference)

    items = package_items(package)
    first_id = list(spine)[0].get("idref")
    first_item = items.get(first_id or "")
    if first_item is None or not first_item.get("href"):
        fail("cannot resolve the first article spine item")
    first_href = first_item.get("href", "")

    if len(nav_ids) != 1:
        fail("EPUB must contain exactly one navigation document")
    nav_item = items[next(iter(nav_ids))]
    nav_file = opf_file.parent / PurePosixPath(nav_item.get("href", ""))
    nav_tree = ET.parse(nav_file)
    nav_root = nav_tree.getroot()
    landmarks = next(
        (
            nav
            for nav in nav_root.findall(f".//{{{XHTML_NS}}}nav")
            if "landmarks" in nav.get(f"{{{EPUB_NS}}}type", "").split()
        ),
        None,
    )
    if landmarks is None:
        body = nav_root.find(f"{{{XHTML_NS}}}body")
        if body is None:
            fail("navigation document has no body")
        landmarks = ET.SubElement(body, f"{{{XHTML_NS}}}nav")
        landmarks.set(f"{{{EPUB_NS}}}type", "landmarks")
        landmarks.set("hidden", "hidden")
        ET.SubElement(landmarks, f"{{{XHTML_NS}}}ol")
    ol = landmarks.find(f"{{{XHTML_NS}}}ol")
    if ol is None:
        ol = ET.SubElement(landmarks, f"{{{XHTML_NS}}}ol")
    for li in list(ol):
        ol.remove(li)
    li = ET.SubElement(ol, f"{{{XHTML_NS}}}li")
    anchor = ET.SubElement(li, f"{{{XHTML_NS}}}a")
    anchor.set(f"{{{EPUB_NS}}}type", "bodymatter")
    anchor.set("href", urllib.parse.urljoin(nav_item.get("href", ""), first_href))
    anchor.text = "Begin Reading"
    nav_tree.write(nav_file, encoding="utf-8", xml_declaration=True)

    ncx_id = spine.get("toc")
    ncx_item = items.get(ncx_id or "")
    if ncx_item is not None:
        ncx_file = opf_file.parent / PurePosixPath(ncx_item.get("href", ""))
        ncx_tree = ET.parse(ncx_file)
        ncx_root = ncx_tree.getroot()
        nav_map = ncx_root.find(f"{{{NCX_NS}}}navMap")
        if nav_map is not None:
            for point in list(nav_map):
                content = point.find(f"{{{NCX_NS}}}content")
                src = content.get("src", "") if content is not None else ""
                if src.endswith(("cover.xhtml", "title_page.xhtml")):
                    nav_map.remove(point)
        ncx_tree.write(ncx_file, encoding="utf-8", xml_declaration=True)

    tree.write(opf_file, encoding="utf-8", xml_declaration=True)


def repack_epub(root: Path, output: Path) -> None:
    mimetype = root / "mimetype"
    if mimetype.read_bytes() != b"application/epub+zip":
        fail("invalid EPUB mimetype")
    temporary = output.with_name(f".{output.name}.tmp")
    with zipfile.ZipFile(temporary, "w") as archive:
        archive.write(mimetype, "mimetype", compress_type=zipfile.ZIP_STORED)
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path == mimetype:
                continue
            archive.write(path, path.relative_to(root).as_posix(), compress_type=zipfile.ZIP_DEFLATED)
    os.replace(temporary, output)


def command_build(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    work_dir = Path(args.work_dir).expanduser().resolve()
    metadata = read_json(work_dir / "metadata.json")
    prepared = read_json(work_dir / "prepared.json")
    article = work_dir / "article.md"
    if prepared.get("status") != "PASS" or prepared.get("article_sha256") != sha256_file(article):
        fail("prepared article receipt is missing or stale")

    output_dir = Path(config["output_directory"])
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{slugify(metadata['title'])}.epub"
    if output.exists():
        fail(f"refusing to overwrite existing EPUB: {output}")

    artwork = Path(args.artwork).expanduser().resolve() if args.artwork else None
    approved_cover = Path(args.approved_cover).expanduser().resolve() if args.approved_cover else None
    if artwork and approved_cover:
        fail("use either --artwork or --approved-cover, not both")
    if approved_cover:
        validate_cover(approved_cover, args.magick)
        receipt = read_json(work_dir / "cover.json")
        if receipt.get("cover_sha256") != sha256_file(approved_cover):
            fail("approved cover does not match the generated cover receipt")
        cover = approved_cover
    else:
        if artwork and not artwork.is_file():
            fail(f"cover artwork does not exist: {artwork}")
        cover = create_cover(work_dir, metadata, artwork, args.magick)
    css = work_dir / "kindle.css"
    css.write_text(EPUB_CSS, encoding="utf-8")
    article_build = work_dir / "article-with-attribution.md"
    article_build.write_text(
        repair_internal_links(article.read_text(encoding="utf-8")) + attribution(metadata),
        encoding="utf-8",
    )
    pandoc_metadata = {
        "title": metadata["title"],
        "author": metadata["authors"],
        "lang": metadata["language"],
        "date": normalize_published_date(metadata.get("published_date"))
        or metadata["accessed_at"].split("T", 1)[0],
        "publisher": metadata.get("publisher") or "",
    }
    metadata_file = work_dir / "pandoc-metadata.json"
    write_json(metadata_file, pandoc_metadata)
    raw_epub = work_dir / ".pandoc.epub"
    run_checked(
        [
            args.pandoc,
            str(article_build),
            "--from=markdown+pipe_tables+fenced_code_blocks+link_attributes+header_attributes",
            "--to=epub3",
            "--toc",
            "--toc-depth=2",
            "--split-level=1",
            f"--css={css}",
            f"--epub-cover-image={cover}",
            f"--resource-path={work_dir}",
            f"--metadata-file={metadata_file}",
            f"--output={raw_epub}",
        ],
        "Pandoc EPUB build",
        cwd=work_dir,
    )
    with tempfile.TemporaryDirectory(prefix="kindle-article-epub-") as temporary:
        unpacked = Path(temporary)
        with zipfile.ZipFile(raw_epub) as archive:
            archive.extractall(unpacked)
        patch_navigation(unpacked)
        repack_epub(unpacked, output)
    raw_epub.unlink(missing_ok=True)
    write_json(
        work_dir / "built.json",
        {
            "status": "PASS",
            "epub": str(output),
            "epub_sha256": sha256_file(output),
            "cover": str(cover),
            "cover_sha256": sha256_file(cover),
        },
    )
    print(json.dumps({"status": "built", "epub": str(output), "cover": str(cover)}))


def zip_safety(archive: zipfile.ZipFile) -> None:
    names = archive.namelist()
    if not names or names[0] != "mimetype":
        fail("mimetype must be the first EPUB entry")
    info = archive.getinfo("mimetype")
    if info.compress_type != zipfile.ZIP_STORED:
        fail("EPUB mimetype must be stored without compression")
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            fail(f"unsafe EPUB path: {name}")


def href_target(base: Path, href: str) -> tuple[Path, str | None]:
    parsed = urllib.parse.urlsplit(href)
    return (base / urllib.parse.unquote(parsed.path)).resolve(), parsed.fragment or None


def extract_links(nav_root: ET.Element, nav_file: Path) -> list[tuple[str, Path, str | None]]:
    toc = next(
        (
            nav
            for nav in nav_root.findall(f".//{{{XHTML_NS}}}nav")
            if "toc" in nav.get(f"{{{EPUB_NS}}}type", "").split()
        ),
        None,
    )
    if toc is None:
        fail("navigation document has no epub:type='toc' navigation")
    result = []
    for anchor in toc.findall(f".//{{{XHTML_NS}}}a"):
        href = anchor.get("href")
        if not href:
            fail("logical TOC contains a link without href")
        target, fragment = href_target(nav_file.parent, href)
        result.append((clean_text("".join(anchor.itertext())), target, fragment))
    return result


def verify_epub_structure(epub: Path, maximum_bytes: int) -> dict[str, Any]:
    if not epub.is_file():
        fail(f"EPUB does not exist: {epub}")
    if epub.stat().st_size > maximum_bytes:
        fail(f"EPUB exceeds attachment limit {maximum_bytes}: {epub.stat().st_size}")
    with tempfile.TemporaryDirectory(prefix="kindle-article-verify-") as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(epub) as archive:
            zip_safety(archive)
            archive.extractall(root)
        package_file = opf_path(root)
        package = ET.parse(package_file).getroot()
        if not package.get(f"{{{XML_NS}}}lang"):
            fail("package document is missing xml:lang")
        language = package.find(f".//{{{DC_NS}}}language")
        if language is None or not clean_text(language.text or ""):
            fail("package document is missing dc:language")
        items = package_items(package)
        spine = package.find(f"{{{OPF_NS}}}spine")
        if spine is None:
            fail("EPUB spine is missing")
        spine_ids = [itemref.get("idref", "") for itemref in spine]
        if not spine_ids:
            fail("EPUB spine is empty")

        covers = [item for item in items.values() if "cover-image" in item.get("properties", "").split()]
        if len(covers) != 1:
            fail(f"expected exactly one cover-image manifest item, found {len(covers)}")
        cover_href = covers[0].get("href", "")
        if not (package_file.parent / PurePosixPath(cover_href)).is_file():
            fail("cover image resource is missing")
        visible_covers = [
            item
            for item in items.values()
            if item.get("media-type") == "application/xhtml+xml"
            and "cover" in item.get("href", "").lower()
        ]
        if visible_covers:
            fail("duplicate HTML cover document is present")

        nav_items = [item for item in items.values() if "nav" in item.get("properties", "").split()]
        if len(nav_items) != 1:
            fail(f"expected exactly one nav manifest item, found {len(nav_items)}")
        nav_id = nav_items[0].get("id", "")
        if nav_id in spine_ids:
            fail("navigation document must not appear in the reading spine")
        nav_file = package_file.parent / PurePosixPath(nav_items[0].get("href", ""))
        nav_root = ET.parse(nav_file).getroot()
        links = extract_links(nav_root, nav_file)
        destinations = [(str(path), fragment) for _, path, fragment in links]
        if len(destinations) != len(set(destinations)):
            fail("logical TOC contains duplicate destinations")
        for listing in nav_root.findall(f".//{{{XHTML_NS}}}ol"):
            labels = [
                clean_text("".join(anchor.itertext())).casefold()
                for anchor in listing.findall(f"./{{{XHTML_NS}}}li/{{{XHTML_NS}}}a")
            ]
            if len(labels) != len(set(labels)):
                fail("logical TOC contains duplicate labels within one section")

        heading_order: dict[tuple[str, str], int] = {}
        document_ids: dict[str, set[str]] = {}
        internal_links: list[tuple[str, str]] = []
        html_count = 0
        image_count = 0
        order = 0
        forbidden_body_style = re.compile(
            r"\b(?:font-family|font-size|line-height|text-align|color|background(?:-color)?)\s*:",
            re.IGNORECASE,
        )
        for item in items.values():
            if item.get("media-type") != "text/css":
                continue
            css_file = package_file.parent / PurePosixPath(item.get("href", ""))
            if not css_file.is_file():
                fail(f"stylesheet resource is missing: {item.get('href')}")
            css = re.sub(r"/\*.*?\*/", "", css_file.read_text(encoding="utf-8"), flags=re.DOTALL)
            for selector, declarations in re.findall(r"([^{}]+)\{([^{}]*)}", css):
                selectors = {part.strip().casefold() for part in selector.split(",")}
                if selectors & {"body", "html", "html body"} and forbidden_body_style.search(declarations):
                    fail(f"reader typography or colors are forced by stylesheet selector {selector.strip()!r}")
        for item_id in spine_ids:
            item = items.get(item_id)
            if item is None:
                fail(f"spine references unknown manifest item: {item_id}")
            item_file = package_file.parent / PurePosixPath(item.get("href", ""))
            if not item_file.is_file():
                fail(f"spine resource is missing: {item.get('href')}")
            if item.get("media-type") != "application/xhtml+xml":
                continue
            html_count += 1
            if item_file.stat().st_size >= MAX_HTML_BYTES:
                fail(f"HTML resource exceeds Amazon's 30 MB limit: {item.get('href')}")
            document = ET.parse(item_file).getroot()
            if not document.get("lang") or not document.get(f"{{{XML_NS}}}lang"):
                fail(f"content document is missing lang/xml:lang: {item.get('href')}")
            document_ids[str(item_file.resolve())] = {
                identifier
                for node in document.iter()
                if (identifier := node.get("id"))
            }
            heading_tags = {f"{{{XHTML_NS}}}h{level}" for level in range(1, 7)}
            for heading in document.iter():
                is_heading = heading.tag in heading_tags
                is_heading_section = heading.tag == f"{{{XHTML_NS}}}section" and any(
                    descendant.tag in heading_tags for descendant in heading.iter()
                )
                if not is_heading and not is_heading_section:
                    continue
                identifier = heading.get("id")
                if identifier:
                    heading_order[(str(item_file.resolve()), identifier)] = order
                    order += 1
            for image in document.findall(f".//{{{XHTML_NS}}}img"):
                image_count += 1
                if image.get("alt") is None or not clean_text(image.get("alt", "")):
                    fail(f"content image lacks meaningful alternative text: {item.get('href')}")
                src = image.get("src", "")
                target, _ = href_target(item_file.parent, src)
                if not target.is_file():
                    fail(f"image resource is missing: {src}")
            for node in document.iter():
                for attribute in ("src", "href"):
                    value = node.get(attribute)
                    if not value or value.startswith(("http://", "https://", "mailto:")):
                        continue
                    parsed = urllib.parse.urlsplit(value)
                    target = (
                        item_file.resolve()
                        if not parsed.path
                        else (item_file.parent / urllib.parse.unquote(parsed.path)).resolve()
                    )
                    if not target.exists():
                        fail(f"referenced EPUB resource is missing: {value}")
                    if attribute == "href" and parsed.fragment:
                        internal_links.append((str(target), parsed.fragment))

        for target, fragment in internal_links:
            if fragment not in document_ids.get(target, set()):
                fail(f"internal fragment does not resolve: {target}#{fragment}")

        if html_count > MAX_HTML_FILES:
            fail(f"EPUB contains {html_count} HTML files; Amazon requires fewer than 300")
        logical_order = []
        for label, target, fragment in links:
            if not target.is_file() or not fragment:
                fail(f"logical TOC target is missing or has no section fragment: {label}")
            key = (str(target.resolve()), fragment)
            if key not in heading_order:
                available = ", ".join(f"{path}#{identifier}" for path, identifier in list(heading_order)[:5])
                fail(
                    f"logical TOC target does not resolve to a heading: {label} -> "
                    f"{key[0]}#{key[1]}; available={available}"
                )
            logical_order.append(heading_order[key])
        if logical_order != sorted(logical_order):
            fail("logical TOC entries are not in chronological reading order")

        first_item = items[spine_ids[0]]
        first_href = first_item.get("href", "")
        if first_href.endswith(("cover.xhtml", "title_page.xhtml", "nav.xhtml")):
            fail("reading spine does not begin with article content")
        landmarks = next(
            (
                nav
                for nav in nav_root.findall(f".//{{{XHTML_NS}}}nav")
                if "landmarks" in nav.get(f"{{{EPUB_NS}}}type", "").split()
            ),
            None,
        )
        bodymatter = [] if landmarks is None else [
            anchor
            for anchor in landmarks.findall(f".//{{{XHTML_NS}}}a")
            if "bodymatter" in anchor.get(f"{{{EPUB_NS}}}type", "").split()
        ]
        if len(bodymatter) != 1:
            fail("navigation landmarks must identify exactly one bodymatter start")
        if len(landmarks.findall(f".//{{{XHTML_NS}}}a")) != 1:
            fail("navigation landmarks must contain only the article reading start")
        start_target, _ = href_target(nav_file.parent, bodymatter[0].get("href", ""))
        expected_start = (package_file.parent / PurePosixPath(first_href)).resolve()
        if start_target != expected_start:
            fail("bodymatter start does not point to the first article spine item")

        ncx_id = spine.get("toc")
        ncx_item = items.get(ncx_id or "")
        if ncx_item is None:
            fail("EPUB spine does not retain NCX navigation")
        ncx_file = package_file.parent / PurePosixPath(ncx_item.get("href", ""))
        ncx_root = ET.parse(ncx_file).getroot()
        ncx_targets = []
        for content in ncx_root.findall(f".//{{{NCX_NS}}}navPoint/{{{NCX_NS}}}content"):
            target, fragment = href_target(ncx_file.parent, content.get("src", ""))
            if fragment:
                ncx_targets.append((str(target), fragment))
        if ncx_targets != destinations:
            fail("NCX navigation does not exactly match EPUB3 logical TOC")

        return {
            "epub_sha256": sha256_file(epub),
            "size_bytes": epub.stat().st_size,
            "logical_toc_entries": len(links),
            "spine_documents": html_count,
            "content_images": image_count,
            "cover_items": 1,
            "navigation_in_spine": False,
            "reading_start": first_href,
        }


def run_external_validator(label: str, command: str | None, epub: Path) -> dict[str, Any]:
    if not command:
        fail(f"UNABLE TO VERIFY: {label} command is not configured")
    argv = shlex.split(command)
    if not argv or shutil.which(argv[0]) is None:
        fail(f"UNABLE TO VERIFY: {label} command is unavailable: {command}")
    with tempfile.TemporaryDirectory(prefix=f"kindle-{label.lower()}-") as temporary:
        result = subprocess.run(
            [*argv, str(epub)],
            cwd=temporary,
            capture_output=True,
            text=True,
            check=False,
        )
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if result.returncode != 0:
        fail(f"{label} failed with exit code {result.returncode}:\n{output[-4000:]}")
    warnings = [
        line
        for line in output.splitlines()
        if re.search(r"\bwarn(?:ing)?s?\b", line, re.I)
        and not re.search(r"\b0\s+warnings?\b", line, re.I)
        and not re.search(r"\bno\s+(?:errors?\s+or\s+)?warnings?\b", line, re.I)
    ]
    return {"status": "PASS", "warnings": warnings, "output_tail": output[-2000:]}


def verify_epub(
    epub: Path,
    epubcheck_command: str = "epubcheck",
    maximum_bytes: int = DEFAULT_MAX_ATTACHMENT,
    accept_warnings: str | None = None,
) -> dict[str, Any]:
    structure = verify_epub_structure(epub, maximum_bytes)
    epubcheck = run_external_validator("EPUBCheck", epubcheck_command, epub)
    warnings = epubcheck["warnings"]
    if warnings and not accept_warnings:
        fail("EPUBCheck warnings require resolution or explicit acceptance:\n" + "\n".join(warnings))
    return {
        "status": "PASS",
        "epub": str(epub),
        **structure,
        "epubcheck": epubcheck,
        "epubcheck_warning_acceptance": accept_warnings,
        "visual_inspection": "NOT_RUN",
        "verified_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    }


def command_verify(args: argparse.Namespace) -> None:
    maximum = int(load_config(args.config)["maximum_attachment_bytes"]) if args.config else DEFAULT_MAX_ATTACHMENT
    epub = Path(args.epub).expanduser().resolve()
    report = verify_epub(epub, args.epubcheck_command, maximum, args.accept_epubcheck_warnings)
    output = Path(args.output).expanduser().resolve() if args.output else epub.with_suffix(".verification.json")
    write_json(output, report)
    print(json.dumps({"status": "verified", "report": str(output), "epub_sha256": report["epub_sha256"]}))


def message_payload(config: dict[str, Any], epub: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    size = epub.stat().st_size
    maximum = int(config["maximum_attachment_bytes"])
    if size > maximum:
        fail(f"attachment exceeds {maximum} bytes and must not use Mail Drop: {size}")
    delivery_id = "lectern-" + secrets.token_hex(12)
    return {
        "from": config["sender"],
        "to": config["kindle_recipient"],
        "subject": metadata["title"],
        "body": "Please add the attached " + ("course" if metadata.get("content_kind") == "course" else "article") + " to my Kindle library.\n\nDelivery reference: " + delivery_id + "\n",
        "delivery_id": delivery_id,
        "attachment": str(epub),
        "attachment_name": epub.name,
        "attachment_size_bytes": size,
        "attachment_sha256": sha256_file(epub),
        "mail_drop": "PROHIBITED",
    }


def command_preview_mail(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    epub = Path(args.epub).expanduser().resolve()
    metadata = read_json(Path(args.metadata).expanduser().resolve())
    verification = read_json(Path(args.verification).expanduser().resolve())
    if verification.get("status") != "PASS" or verification.get("epub_sha256") != sha256_file(epub):
        fail("delivery is blocked: verification receipt is missing, failed, or stale")
    payload = message_payload(config, epub, metadata)
    confirmation_file = Path(args.confirmation_file).expanduser().resolve()
    if confirmation_file.exists():
        previous = read_json(confirmation_file)
        if previous.get("status") != "AWAITING_CONFIRMATION":
            fail("do not replace an attempted delivery receipt; verify its Sent status first")
    confirmation_file.parent.mkdir(parents=True, exist_ok=True)
    snapshot_dir = Path(tempfile.mkdtemp(prefix=".lectern-delivery-", dir=confirmation_file.parent))
    snapshot = snapshot_dir / epub.name
    try:
        shutil.copyfile(epub, snapshot)
        if sha256_file(snapshot) != verification["epub_sha256"]:
            fail("attachment changed while preparing delivery; verify and preview again")
        snapshot.chmod(0o400)
    except BaseException:
        shutil.rmtree(snapshot_dir)
        raise
    payload["source_attachment"] = payload["attachment"]
    payload["attachment"] = str(snapshot)
    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    token = secrets.token_urlsafe(24)
    write_json(
        confirmation_file,
        {
            "status": "AWAITING_CONFIRMATION",
            "token": token,
            "payload_sha256": payload_hash,
            "expires_at": (
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=CONFIRMATION_TTL_SECONDS)
            ).replace(microsecond=0).isoformat(),
            "payload": payload,
        },
        mode=0o600,
    )
    print(json.dumps({**payload, "confirmation_token": token, "expires_in_seconds": CONFIRMATION_TTL_SECONDS}, indent=2))


def mail_script() -> Path:
    return Path(__file__).with_name("mail-kindle.applescript")


def command_mail(args: argparse.Namespace) -> None:
    confirmation_file = Path(args.confirmation_file).expanduser().resolve()
    confirmation = read_json(confirmation_file)
    payload = confirmation.get("payload")
    if not isinstance(payload, dict):
        fail("confirmation receipt has no mail payload")
    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    if payload_hash != confirmation.get("payload_sha256"):
        fail("confirmation receipt payload has been modified")
    if args.mode == "send":
        if confirmation.get("status") != "AWAITING_CONFIRMATION" or args.confirmation_token != confirmation.get("token"):
            fail("send requires the exact current confirmation token")
        expires = dt.datetime.fromisoformat(confirmation["expires_at"])
        if dt.datetime.now(dt.timezone.utc) >= expires:
            fail("send confirmation expired; show the exact message and ask again")
        attachment = Path(payload["attachment"])
        original = Path(payload.get("source_attachment", payload["attachment"]))
        if (
            not attachment.is_file()
            or attachment.stat().st_size != payload["attachment_size_bytes"]
            or sha256_file(attachment) != payload.get("attachment_sha256")
            or not original.is_file()
            or sha256_file(original) != payload.get("attachment_sha256")
        ):
            fail("attachment changed after verification; rebuild, verify, and preview again")
        # A persistent claim keeps ambiguous attempts single-use across processes.
        claim = confirmation_file.with_name(confirmation_file.name + ".send-claim")
        try:
            descriptor = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            fail("delivery already claimed; verify Sent status without sending again")
        os.close(descriptor)
        confirmation["status"] = "CONSUMED_BEFORE_SEND"
        confirmation["token"] = None
        write_json(confirmation_file, confirmation, mode=0o600)

    command = [
        args.osascript,
        str(mail_script()),
        args.mode,
        payload["from"],
        payload["to"],
        payload["subject"],
        payload["body"],
        payload["attachment"],
        payload["attachment_name"],
        payload.get("delivery_id", ""),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    receipt = {
        "mode": args.mode,
        "attempted_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "payload_sha256": payload_hash,
        "returncode": result.returncode,
        "result": result.stdout.strip(),
        "error": result.stderr.strip(),
        "status": "DRAFT_CREATED" if args.mode == "draft" and result.returncode == 0 else "SEND_ATTEMPTED",
    }
    receipt_file = Path(args.receipt).expanduser().resolve()
    write_json(receipt_file, receipt, mode=0o600)
    if result.returncode != 0:
        fail(f"Apple Mail {args.mode} failed; do not retry automatically: {result.stderr.strip()}")
    print(json.dumps({"status": receipt["status"], "receipt": str(receipt_file), "result": result.stdout.strip()}))


def command_verify_sent(args: argparse.Namespace) -> None:
    confirmation = read_json(Path(args.confirmation_file).expanduser().resolve())
    payload = confirmation.get("payload")
    if not isinstance(payload, dict):
        fail("confirmation receipt has no mail payload")
    if confirmation.get("status") not in {"CONSUMED_BEFORE_SEND", "SENT_VERIFIED"} or not payload.get("delivery_id"):
        fail("Sent verification requires an attempted delivery with a delivery reference")
    command = [
        args.osascript,
        str(mail_script()),
        "count-sent",
        payload["from"],
        payload["to"],
        payload["subject"],
        payload["body"],
        payload["attachment"],
        payload["attachment_name"],
        payload["delivery_id"],
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        fail(f"UNABLE TO VERIFY iCloud Sent; preserve the EPUB and do not retry: {result.stderr.strip()}")
    try:
        count = int(result.stdout.strip())
    except ValueError:
        fail(f"UNABLE TO VERIFY iCloud Sent count: {result.stdout.strip()!r}")
    if count != 1:
        fail(f"ambiguous iCloud Sent status: expected exactly one message, found {count}; do not retry")
    confirmation["status"] = "SENT_VERIFIED"
    write_json(Path(args.confirmation_file).expanduser().resolve(), confirmation, mode=0o600)
    print(json.dumps({"status": "PASS", "matching_icloud_sent_messages": 1}))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    subparsers = root.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser("configure")
    configure.add_argument("--config")
    configure.add_argument("--kindle-recipient", required=True)
    configure.add_argument("--sender", required=True)
    configure.add_argument("--output-directory", required=True)
    configure.add_argument("--maximum-attachment-bytes", type=int, default=DEFAULT_MAX_ATTACHMENT)
    configure.set_defaults(handler=command_configure)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("url")
    prepare.add_argument("--work-dir", required=True)
    prepare.add_argument("--defuddle", default="defuddle")
    prepare.add_argument("--magick", default="magick")
    prepare.add_argument("--author", action="append")
    prepare.add_argument("--publisher")
    prepare.set_defaults(handler=command_prepare)

    build = subparsers.add_parser("build")
    build.add_argument("--work-dir", required=True)
    build.add_argument("--artwork")
    build.add_argument("--approved-cover")
    build.add_argument("--config")
    build.add_argument("--pandoc", default="pandoc")
    build.add_argument("--magick", default="magick")
    build.set_defaults(handler=command_build)

    cover = subparsers.add_parser("cover")
    cover.add_argument("--work-dir", required=True)
    cover.add_argument("--artwork")
    cover.add_argument("--magick", default="magick")
    cover.set_defaults(handler=command_cover)

    verify = subparsers.add_parser("verify")
    verify.add_argument("epub")
    verify.add_argument("--config")
    verify.add_argument("--epubcheck-command", default=os.environ.get("KINDLE_ARTICLE_EPUBCHECK", "epubcheck"))
    verify.add_argument("--accept-epubcheck-warnings")
    verify.add_argument("--output")
    verify.set_defaults(handler=command_verify)

    preview_mail = subparsers.add_parser("preview-mail")
    preview_mail.add_argument("--epub", required=True)
    preview_mail.add_argument("--metadata", required=True)
    preview_mail.add_argument("--verification", required=True)
    preview_mail.add_argument("--confirmation-file", required=True)
    preview_mail.add_argument("--config")
    preview_mail.set_defaults(handler=command_preview_mail)

    mail = subparsers.add_parser("mail")
    mail.add_argument("--mode", choices=("draft", "send"), required=True)
    mail.add_argument("--confirmation-file", required=True)
    mail.add_argument("--confirmation-token")
    mail.add_argument("--receipt", required=True)
    mail.add_argument("--osascript", default="osascript")
    mail.set_defaults(handler=command_mail)

    verify_sent = subparsers.add_parser("verify-sent")
    verify_sent.add_argument("--confirmation-file", required=True)
    verify_sent.add_argument("--osascript", default="osascript")
    verify_sent.set_defaults(handler=command_verify_sent)
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        args.handler(args)
    except KindleArticleError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
