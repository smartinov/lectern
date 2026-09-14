# 0001: Author in Pandoc Markdown, deliver EPUB 3 through Send to Kindle

## Context

Lessons are generated as text and must reach a Kindle with working navigation,
footnotes, and self-check answers that are not visible while reading the lesson.

## Decision

- Canonical content is Pandoc Markdown, one file per lesson, built with pandoc
  into a single reflowable EPUB 3 per course and validated with epubcheck.
- Delivery is Send to Kindle (email or web uploader) with the EPUB as-is. Amazon
  converts server-side. No MOBI, AZW3, or KFX is produced. KindleGen is
  deprecated and excluded. Calibre is optional for USB sideloading only.
- Footnotes use pandoc's native syntax, which renders as Kindle pop-ups.
- Quiz answers live in an appendix. Questions link forward to answers; answers
  link to a continuation anchor rather than back to the question.

## Alternatives considered

- Custom EPUB packaging: more code, no benefit over pandoc.
- Collapsible or hidden answer markup: reading-system support is inconsistent
  and paginated readers can push expanded content off screen.
- Per-lesson EPUBs: possible later from the same sources; one book per course
  keeps the answer appendix and cross-lesson links simple.
- Unofficial Send to Kindle API clients: only needed if email limits bite.

## Consequences

- Rebuilding a course never needs a model call.
- Reading progress and quiz results cannot be read from the device; the learner
  reports them and the agent records them.
