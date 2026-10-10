# TODO

Planned work that is not scheduled yet. When an entry is picked up, write its design in
`docs/design/`, update `REQUIREMENTS.md` / `ARCHITECTURE.md`, and remove it from here.

## Requested features

- [ ] **Show the original sentence with the grading.** The feedback for a written exercise
      should repeat the exercise prompt (the sentence to translate or the task) next to the
      learner's answer and the corrected sentence, so the result can be read without scrolling
      back. Applies to session feedback and to the read-only feedback in Progress → History.
- [ ] **Practice right after a new grammar point is introduced.** After the intro card of a new
      grammar point, give 3 short, easy exercises that use it immediately. All three can be
      shown together, filled in, and graded in a single LLM call if that is faster. Open
      points: how these attempts count for scheduling (they come right after the explanation,
      so a correct answer is weaker evidence than a later recall); how they fit the session
      length and the production-slot budget; one batch grading call vs. three.
- [ ] **Ask-a-question sidebar during grading review.** While reviewing the grading, a sidebar
      (a bottom sheet on the phone) where the learner can ask free questions about grammar or
      vocabulary. The exercise, the answer, the grading and the reference text of the items
      involved go to the LLM as context. Needs a new task in the typed `LLMClient` interface
      with a versioned prompt, and every call logged. Open point: whether a question can
      raise a contest or record an event (by default it does neither).

## Smaller follow-ups

- [ ] Item-level explain endpoint (explain a grammar point or word from its Progress page
      without an error as context).
- [ ] Combined grammar + constructions list in the Grammar view.
- [ ] List suspended items somewhere (Progress or Settings).
