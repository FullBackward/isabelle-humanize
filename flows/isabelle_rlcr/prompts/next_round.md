Round {{CURRENT_ROUND}}. The task is unchanged: prove `{{TARGET_THEOREM}}` in
`{{PROBLEM_FILE}}` -- no `sorry`, no `oops`, statement unchanged.

The mechanical check of your last round said:

{{REASON}}

Continue from the file as it stands. Work in small steps, check every step
with `mcp__isabellegym__isabelle_diagnostic_messages`, and follow the
escalation ladder (goal inspection -> sledgehammer -> multi_attempt ->
decompose into helper lemmas). Update the MUTABLE section of
`{{GOAL_TRACKER_FILE}}` with where the proof stands and what you try next.

{{LESSON_NOTE}}

Before you stop, write `{{SUMMARY_FILE}}`: what you changed, what the prover
reported, what you try next -- closing with a `## Lesson Delta` block
(Action: none|add|update; Lessons: IDs existing in `proof-lessons.md`;
Notes: one concrete sentence; the last two required for add/update). A round
whose summary or delta does not validate is refused.

When diagnostics report `success=true` and `proof_open=false`, stop editing
and say the round is done.
