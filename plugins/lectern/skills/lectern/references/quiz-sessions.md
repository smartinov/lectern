# Conversational practice

Read the selected course's brief, outline, manuscripts, questions, and private
learner history before teaching or scoring. If material is unavailable, state
what is missing rather than inventing the course's content. Do not import or
rewrite progress from another course merely because the topics overlap.

## Session

1. Resolve the requested course and lessons. Start with the chapter the learner
   says they read. Questions about untaught material require an explicit diagnostic
   request and remain labelled diagnostic.
2. Offer up to eight cards by default, one question per message. Wait for the
   answer. Mix free text, single-choice, and multiple-choice using the source bank.
3. Before new cards, revisit earlier incorrect or hint-assisted answers within
   the selected taught scope. Include an earlier-session objective when available;
   otherwise explain that delayed-practice evidence does not yet exist.
4. Give specific corrective feedback and a short explanation. For free text use
   the question's rubric: 0 = missing/incorrect, 1 = partial with a major gap,
   2 = substantially correct with a minor gap, 3 = correct and adequately explained.
   For choices record exact-set correctness and explain wrong selections; do not
   inflate a partial multiple-choice answer into a correct response.
5. Record hints and answer exposure. If the learner asks to see an answer, show
   it and record exposure, not successful recall. An unanswered card is not a failure.
6. At the end, state demonstrated strengths, remaining gaps, and useful next
   practice. Suggest a delayed revisit in the next study session; no background
   scheduler or automatic reminder is created.

## Private event history

Append to `<workspace>/learner/history.jsonl`; never overwrite earlier events.
Use one object per event with `at` (ISO timestamp), `event`, and `course` (slug).
Question attempts also identify `lesson`, `question_id`, `objective_ids`, and
`questions_sha256`, plus the learner's response, score, scoring scale, and hints.
This ties evidence to the assessed version if questions change later.

Supported event meanings:

- `lesson_reported_read`: learner statement; no mastery implied.
- `question_attempt`: actual response and rubric-based feedback.
- `answer_exposed`: hint or answer shown; no invented successful attempt.
- `preference_feedback`: explicitly stated enjoyment, clarity, pace, or voice feedback.
- `delivery_sent_verified`: exact final attachment and Sent evidence; no receipt implied.
- `receipt_reported`: learner confirms receiving the book.

During an interrupted session, keep the pending question and requested scope in
`learner/session.yaml`. Resume only if the saved question-bank hash still matches;
otherwise explain the changed material and start a new session. Completed
sessions clear pending state but retain history. Never infer a durable preference
from a quiz score. History is evidence for future practice, not a mastery score
computed from chapter completion.
