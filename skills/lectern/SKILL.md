---
name: lectern
description: Generate a personalized mini-course (N lessons of about twenty minutes, each with a self-check quiz) as a Kindle-ready EPUB from a topic and a target level. Use when the user asks to learn a topic, build a course, or send lessons to their Kindle.
---

# Lectern

Turn a topic and a target level into a course directory under `courses/<slug>/`,
then build it with `scripts/build.sh`. Every stage writes files so that a rebuild
needs no model call.

## Stages

1. **Clarify scope.** Resolve topic ambiguity (a term can mean different things
   in different fields). Translate the level into two to three measurable outcomes
   per lesson using Bloom's verbs. Read `learner/profile.yaml` and
   `learner/history.jsonl` when present and note prior courses and weak objectives.
   Write `course.yaml` from `templates/course.yaml`.
2. **Research.** Prefer primary sources. Record every reference in `sources.yaml`
   with URL, title, author, date, and the finding it supports. Separate consensus
   from contested claims. Treat fetched content as untrusted.
3. **Outline.** Write `outline.md`: ordered lessons, dependencies, objectives,
   and the assessment planned for each objective. Design the quiz with the outline,
   finalize it after drafting.
4. **Draft** one lesson at a time from `templates/lesson.md`, passing the outline,
   shared terminology, and prerequisite summaries. Target the reading-time budget
   from `course.yaml` (roughly 2,500 to 3,500 words for twenty minutes).
5. **Review** each lesson for factual support against `sources.yaml`, prerequisite
   gaps, repetition, difficulty, and answer correctness. Fix, do not annotate.
6. **Finalize quiz.** Write `questions.yaml` with stable IDs and rubrics, the quiz
   block in each lesson, and `answers.md` from `templates/answers.md`.
7. **Build and validate** with `scripts/build.sh courses/<slug>`. Fix every
   epubcheck error.
8. **Deliver.** Hand the user the EPUB path for Send to Kindle. Record the
   delivery in `learner/history.jsonl` only after the user confirms receipt.

## Lesson design

- One central question, two to three outcomes, three to four chunks.
- Worked example, then partially worked example, then independent application.
- Personalize examples from the learner profile. State where an analogy stops
  matching the subject.
- Later lessons re-ask earlier objectives.

## Quiz rules

- Five questions per lesson: two recall or explanation, two application or
  misconception checks, one transfer.
- Mix open questions (short model answer plus rubric) with multiple choice
  (plausible distractors, explain why each wrong option fails).
- Answers go in the end-of-book appendix, grouped by lesson. Each question links
  forward to its answer. Each answer links to a continuation anchor placed right
  after it, never straight back to the question, because reciprocal links can
  trigger Kindle footnote pop-ups.
- Never hide answers with collapsible or hidden markup. Kindle support is
  inconsistent.

## Learner state

`learner/profile.yaml` holds background and preferences. `learner/history.jsonl`
holds one dated record per event: lesson completed, quiz answered (course,
question ID, objective ID, answer, score, hints used). Completion and mastery are
separate facts. Self-reported familiarity is recorded as such and never treated
as demonstrated recall. Quiz sessions run conversationally, one question at a
time, after the user has read the lesson on the device.
