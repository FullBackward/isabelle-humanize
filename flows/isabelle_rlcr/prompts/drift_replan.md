Round {{CURRENT_ROUND}}. The task is unchanged: prove `{{TARGET_THEOREM}}` in
`{{PROBLEM_FILE}}` -- no `sorry`, no `oops`, statement unchanged.

WARNING: the mainline has not moved for {{STALL_COUNT}} consecutive rounds
(last verdict: {{LAST_VERDICT}}). Repeating the same approach is now a
documented failure mode. This round MUST change strategy:

1. Read the reviewer's findings below, then inspect the actual open goals with
   `mcp__isabellegym__isabelle_goal` -- do not trust your memory of them.
2. Pick a DIFFERENT tactic than the rounds that stalled:
   - decompose the failing goal into helper `lemma`s above the target;
   - run `mcp__isabellegym__isabelle_multi_attempt` with 3-4 genuinely
     different method candidates at the failing line;
   - escalate to `mcp__isabellegym__isabelle_sledgehammer` and paste a
     suggestion verbatim;
   - or restructure the proof (induction on a different variable, a different
     rule, a case split).
3. Small steps, diagnostics after every edit.

The review of the last round:

{{REVIEW}}

Update the MUTABLE section of `{{GOAL_TRACKER_FILE}}` with the new strategy.

{{LESSON_NOTE}}

Before you stop, write `{{SUMMARY_FILE}}` (what changed, what the prover
reported, what next) closing with a `## Lesson Delta` block (Action:
none|add|update; Lessons: IDs existing in `proof-lessons.md`; Notes: one
concrete sentence). A round whose summary or delta does not validate is
refused. A replan round that learned nothing is suspicious -- strongly
consider Action: add.

When diagnostics report `success=true` and `proof_open=false`, stop editing
and say the round is done.
