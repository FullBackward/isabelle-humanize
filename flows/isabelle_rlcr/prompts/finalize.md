The arbiter has verified the proof: `{{TARGET_THEOREM}}` in `{{PROBLEM_FILE}}`
now builds under `isabelle build` in strict mode, no `sorry`, statement intact.

You get ONE optional finalization pass, under tight constraints:

1. You may simplify the proof ONLY in ways that keep it functionality
   equivalent: replacing a fragile tactic with a robust one (e.g. a
   sledgehammer-generated `smt` call with a `simp`/`auto` that also works),
   removing dead helper lemmas, tidying formatting. No statement changes, no
   new dependencies, no `sorry`/`oops`, no behavior change.
2. After every edit, check `mcp__isabellegym__isabelle_diagnostic_messages`.
3. If nothing is worth simplifying, say so and change nothing.

The arbiter re-checks the file after this pass. If the re-check fails, your
finalization is reverted to the verified version -- the safe move when in
doubt is to change nothing.

Write `finalize-summary.md`: what the final proof is, which approach worked,
and what (if anything) you simplified.
