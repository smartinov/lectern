# Lectern

Personalized offline courses and faithful article conversions, packaged as
Kindle EPUBs. The repository owns one portable plugin for Codex and Claude.

Courses use an original writing voice, saved learner preferences, research-backed
teaching guidance, and questions with explained answers. The default is a blunt,
witty mentor and a 30-minute session: about 20 minutes reading and 10 practicing.
Review the scope, outline, and a writing sample once; the agent then completes the
course, editorial review, and build checks.

## Start with an agent

Read [AGENTS.md](AGENTS.md), then ask:

- “Create a six-lesson course on distributed systems for an experienced engineer.”
- “Make the examples more practical and tone down the profanity.”
- “I finished chapter one of this course. Quiz me one question at a time.”
- “Convert this article to a Kindle EPUB, preserving its wording.”

The [course skill](plugins/lectern/skills/lectern/SKILL.md) routes authoring and
study. The [article skill](plugins/lectern/skills/kindle-article/SKILL.md) handles
extraction and email delivery. Read the
[three writing presets and samples](plugins/lectern/skills/lectern/references/writing-styles.md)
and the [learning evidence](plugins/lectern/skills/lectern/references/learning-design.md).

## Requirements

- Python 3.12 or newer and the pinned Python requirements
- Pandoc 3.9 or newer, ImageMagick 7 (`magick`), and EPUBCheck 5.3 or newer
- ShellCheck for repository checks
- Defuddle for fetching articles; Apple Mail on macOS for optional iCloud delivery
- Kindle Previewer for device-specific visual inspection when available

On macOS:

```sh
brew install pandoc imagemagick epubcheck shellcheck
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
scripts/check.sh
```

Defuddle is used through `defuddle parse URL --md -o FILE`; install it separately
when using the article workflow. Local course building and validation require
neither mail configuration nor network access after sources/assets are saved.

## Create and build a course

Run from this checkout, with the Python environment active:

```sh
python3 plugins/lectern/scripts/course.py init --workspace . my-topic
```

Fill the generated specification, sources, and outline. The agent reads
`learner/profile.yaml`, then resolves a private authoring snapshot:

```sh
python3 plugins/lectern/scripts/course.py resolve --workspace . courses/my-topic
```

After calibration and writing the lesson manuscripts and question bank:

```sh
scripts/build.sh courses/my-topic
```

The output is `build/my-topic.epub`, accompanied by metadata and verification
JSON. A successful rebuild replaces that local EPUB only after validation. It
never sends email or updates learner progress. Changing the specification requires
resolving the brief again; a profile change does not silently change old courses.

The installed helper takes `--workspace /absolute/path/to/lectern` explicitly.
A relative course path is relative to that workspace, not the process directory.
The repository wrapper selects its own checkout, so it also works from elsewhere.

## Files and privacy

| Location | Purpose | Git |
|---|---|---|
| `plugins/lectern/` | Installable skills, research, styles, templates, helpers | Tracked |
| `courses/<slug>/` | Specification, brief, outline, sources, lessons, question bank, assets | Private |
| `articles/<slug>/` | Extracted article, assets, cover, verification and delivery receipts | Private |
| `learner/` | Profile, observed progress, optional active quiz session | Private |
| `build/` | Generated EPUBs and verification sidecars | Private |
| `tests/fixtures/` | Synthetic examples for repeatable checks | Tracked |
| `docs/decisions/` | Durable architecture and workflow decisions | Tracked |

The blank profile template is shareable. Private profiles store only stated or
confirmed preferences. Quiz results are evidence of attempts, not a personality
profile. The existing Claude Architect Foundations course remains where it is.

`questions.yaml` is the assessment source of truth. The compiler creates question
blocks, an answer appendix, and return links to the lesson. Do not hand-maintain
separate answers. Source notes include both findings and limitations.

## Install for Codex and Claude

From this checkout, after `scripts/check.sh` passes:

```sh
codex plugin marketplace add .
codex plugin add lectern@lectern
claude plugin marketplace add .
claude plugin install lectern@lectern
```

Start a new session to load the installed skills. Confirm the resolved package
contains both skills, scripts, and templates. Runtime data belongs in the selected
workspace, never in an installed cache. The package's Python requirement file is
also included for installation outside this checkout.

Refresh local changes through the client plugin CLI, then inspect the resolved
version and files. Edit the repository source rather than an installed cache.
Retire old standalone `kindle-article` links only after both clients resolve the
new package. Existing private email configuration remains in its original location.

## Verification and delivery

`scripts/check.sh` checks manifests and packaged references, privacy boundaries,
ShellCheck, unit tests, and synthetic course/article EPUB builds with EPUBCheck.
CI runs this same command. Tests use synthetic addresses and mock sending.

Structural checks cover navigation, embedded resources, answer links, metadata,
and reader-controlled typography. Visual inspection is recorded separately with
the actual reader/settings. EPUBCheck success does not prove Kindle appearance or
Amazon delivery. The synthetic course is intentionally shorter than a real lesson.

Email delivery needs explicit authorization for the final attachment and recipient.
The exact preview includes a unique delivery reference. The helper binds verified
bytes to a private snapshot, atomically claims the confirmation, and matches that
reference when checking Sent. Preserve snapshots and receipts while delivery is
uncertain; investigate Sent without retrying. A separate, explicitly requested
resend needs a fresh confirmation path. Sent verification is not device receipt.

## License

MIT. Migration provenance is recorded in the [format decision](docs/decisions/0001-format-and-delivery.md).
