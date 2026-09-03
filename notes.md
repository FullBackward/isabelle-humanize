## Humanfia/humanize flow used for PutnamBench
```
while turn ≤ 50:
    write worker prompt file (previous review spliced in as feedback)
    run Codex worker (sandboxed, no network, no prior solutions)
    git-commit the candidate
    local_checks()  ← runner-side deterministic gates
    if gates fail: feedback = gate output; continue   # reviewer never sees failures
    render review prompt ({{VAR}} single-pass) → run Codex reviewer
    done ⟺ review's last line == "COMPLETE" ∧ external verifier (AXLE) okays everything
```

