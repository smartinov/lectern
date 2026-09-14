# Lectern

Lectern authors personal offline courses and faithfully converts articles to
EPUB. This repository is the canonical source for its portable plugin.

## Start here

- Read README.md and the applicable skill in `plugins/lectern/skills/`.
- Use `lectern` for course scope, research, authoring, personalization, and quizzes.
- Use `kindle-article` for article extraction, metadata, covers, and delivery.
- Read only the references needed for the current stage. Keep detailed teaching
  and editorial rules in the skill, not in this file.

## Ownership and privacy

- `plugins/lectern/` is installable source: skills, references, templates, and helpers.
- `courses/`, `articles/`, `learner/`, and `build/` are private workspace state,
  ignored by Git. Never place them inside the plugin or commit their contents.
- `tests/fixtures/` contains synthetic, shareable test content only.
- Packaged course commands require `--workspace`; the repository build wrapper
  supplies this checkout. Article commands use explicitly selected private
  work directories or artifact paths. Never infer a personal workspace from an
  unrelated current directory or write into an installed plugin cache.
- Keep existing delivery configuration outside the repo. Do not copy addresses,
  tokens, personal examples, quiz answers, or progress into fixtures or logs.
- Existing courses elsewhere remain in place unless the user requests migration.

## Working agreement

For a new course, resolve its brief, research the topic, and present scope,
outline, and a short writing sample together. After calibration, finish the
course and its reviews without per-lesson approval unless requested. Respect
authorization already given. Article conversion preserves the source voice;
personal writing presets apply to original course material.

Default to English, Blunt Mentor, and a 30-minute session including practice.
Saved preferences and course instructions may override these. Keep factual
claims and explained answers accurate regardless of style. Report uncertain
sources and missing evidence instead of inventing them.

## Implementation and checks

Retain the migrated Python helpers; keep shell to thin entrypoints. Prefer the
existing Pandoc pipeline and small shared functions over another packaging
framework. Add tests for observable failures, including renderer seams.

Run from the repository root:

```sh
python3 -m pip install -r requirements.txt
scripts/check.sh
scripts/build.sh courses/<slug>
```

The check command runs dependency checks, ShellCheck, unit/integration tests,
manifest/resource validation, and synthetic EPUB builds with EPUBCheck. CI runs
the same command. Do not invoke real Mail from tests or require learner files.

## Done means

Source files rebuild offline without model calls. Assessments trace to outcome
IDs and share one source of truth. EPUBs pass structural and EPUBCheck validation;
visual inspection names the reader and settings actually used. A generic render
does not prove Kindle behavior. An unrun device check remains explicitly unrun.

Generation never implies permission to send. Before authorized delivery, verify
the exact final attachment and mail preview. On an ambiguous send, investigate
Sent status without resending. Distinguish send requested, Sent verified, and
learner-reported receipt.
