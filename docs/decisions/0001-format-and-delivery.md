# 0001: Own EPUB workflows in a portable Lectern plugin

## Context

Lectern creates personal offline courses and converts existing web articles.
The article workflow already had Pandoc packaging, deterministic covers,
EPUBCheck, rendering regressions, and Apple Mail delivery. Reusing those behaviors
avoids replacing tested conversion logic with another EPUB implementation.

## Decision

- Lectern is the source for both skills. One `plugins/lectern/` package contains
  skill instructions, templates, and the migrated Python/AppleScript helpers.
  Existing root skill/template directories now live in that package because
  they share its installation lifecycle. The root build script remains a wrapper.
- The article helper is recovered from `smartinov/Codex-skills` commit
  `301f12461209fad214a856ebcc08e5fbd67c7f93`, including its fixtures and tests.
  Its Git history is preserved in the source repository; installed copies are
  replaced only after both new client installations have been verified.
- The packaged course CLI requires `--workspace`. The repository wrapper selects
  this checkout. Assets/templates resolve from the package; private courses,
  learner state, article work directories, and builds resolve from the workspace.
  Plugin installation or upgrade never owns personal state.
- Existing `learner/profile.yaml` owns cross-course preferences. Course-specific
  overrides stay in `course.yaml`; `brief.yaml` freezes effective authoring settings
  and the specification hash. Builds consume that snapshot rather than a changing
  profile. History records demonstrated responses separately from preferences.
- Reusable writing and teaching guidance lives in skill references. Start with
  Blunt Mentor, Case-Study Storyteller, and Socratic Guide. Use original traits and
  examples, not instructions to imitate a named author. Default to English and
  20 minutes reading plus 10 minutes practice; calibrate once before drafting.
- Canonical lessons remain Pandoc Markdown. `questions.yaml` owns assessments;
  the compiler generates question blocks, the answer appendix, and continuation
  links. Source findings and caveats are available offline in source notes.
- Deliver EPUB 3 with logical navigation outside the reading spine, matching NCX,
  one cover, localized images, and reader-controlled typography. Ordinary footnotes
  may use Pandoc syntax; quiz answers use separate appendix/continuation links.
- A course is one EPUB. Building and verifying it needs no mail configuration.
  Source material rebuilds without model calls or live research. Verification
  records source hashes and separates conformance from visual inspection.
- Mail delivery preserves the private configuration location and its attachment
  limit. The exact preview binds a unique delivery reference and a private,
  read-only snapshot of the verified EPUB. An exclusive claim permits one send
  attempt; ambiguous attempts remain claimed. Sent verification requires an
  attempted receipt and matches the delivery reference in the message body.
- Personal courses, profiles, session state, receipts, and builds remain ignored.
  Synthetic fixtures and reusable instructions are tracked. Existing courses
  outside Lectern are not migrated by this change.

## Alternatives considered

- Independent course EPUB engine: repeats navigation, cover, and validation logic.
- Installing the repository root as the plugin: combines distributable resources
  with private workspace state. The package directory makes that boundary explicit.
- General preferences database or multiple learner service: the existing private
  profile and history have the right ownership; no service is required.
- Fixed learning-style diagnoses: adapt to stated preferences and demonstrated
  prior knowledge instead; research guidance records the evidence boundary.
- Separate editable answer manuscripts: allows questions and explanations to drift.
- Hidden or collapsible quiz answers: unreliable in paginated readers.
- Automatic mail retries: duplicate delivery is worse than an honest uncertain state.
- Passing a mutable build path to Mail: a rebuild can replace approved bytes while
  the mail client reads them. Delivery snapshots share the receipt lifecycle.

## Consequences

The plugin is portable while authoring stays private and document-driven. Reading
completion and device receipt remain learner reports; neither can be inferred
from a local build. A passing compiler cannot certify writing quality, factual
correctness, or learning outcomes. Inspect a sample EPUB in an actual reader and
report any unavailable checks. Retain delivery snapshots with their receipts;
cleanup follows the owning private article/course workspace lifecycle.
