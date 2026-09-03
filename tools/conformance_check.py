#!/usr/bin/env python3
"""Conformance checker: does a finished RLCR workspace show the v1 gates?

Usage: python tools/conformance_check.py <workspace-dir> [--json]

Reads `<workspace>/.rlcr/journal.jsonl` plus the round artifacts and maps every
v1 gate of the merge-plan.md §3.2 table to an observed flow behavior (PASS), a
missing one (FAIL), or a documented non-observation (N-A -- e.g. a gate that
never fired, or a journal era that predates the gate). One line per check:

    PASS|FAIL|N-A <gate> — <evidence>

Exit code is 0 iff no check FAILs. The checker is deliberately evidence-based:
it judges from the journal and artifacts alone, never from the flow's code, so
it can also grade journals written by older milestones (M1 journals carry no
verdict/drift kinds; the lesson-delta gate is M3).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

#: v1's five terminal states (flows/isabelle_rlcr/__init__.py TERMINAL).
TERMINAL = ("complete", "cancel", "maxiter", "stop", "unexpected")

#: v1's default round budget (RLCRConfig.max_iterations).
DEFAULT_MAX_ITERATIONS = 42

#: Round artifact stems the loop writes into .rlcr/ (round-{N}-<stem>.md).
ARTIFACT_STEMS = ("prompt", "review-prompt", "review-result", "summary")

#: What a goal-tracker refusal looks like in the journal (gates.py wording).
_TRACKER_REFUSAL = re.compile(r"IMMUTABLE|Goal tracker", re.IGNORECASE)

#: The lesson-delta heading every M3 round summary must close with. Presence
#: is all the conformance check needs; the full fence-aware validation lives
#: in flows/isabelle_rlcr/memory.py and runs inside the loop itself.
_DELTA_HEADING = re.compile(r"^## Lesson Delta\s*$", re.MULTILINE)


@dataclass
class CheckResult:
    """One gate's verdict: PASS / FAIL / N-A plus the evidence for it."""

    gate: str
    status: str
    evidence: str


def load_journal(workspace: Path) -> list[dict]:
    """The journal records, oldest first. Raises FileNotFoundError/ValueError."""
    journal = workspace / ".rlcr" / "journal.jsonl"
    if not journal.is_file():
        raise FileNotFoundError(f"no journal: {journal}")
    records = []
    for lineno, line in enumerate(journal.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as failed:
            raise ValueError(f"{journal}:{lineno}: malformed JSON: {failed}") from failed
    return records


def _rounds(records: list[dict]) -> list[int]:
    """Every journaled round number, sorted."""
    return sorted({int(r["round"]) for r in records if isinstance(r.get("round"), int)})


def _reviewer_enabled(records: list[dict]) -> bool:
    """Verdict/drift kinds exist only when the reviewer turn runs (M2+, rlcr arm)."""
    return any(r.get("kind") in ("verdict", "drift") for r in records)


def check_tracker_immutable(records: list[dict], workspace: Path) -> CheckResult:
    """v1 plan-integrity: the goal tracker's IMMUTABLE section is byte-preserved."""
    gate = "goal-tracker-immutable"
    setups = [r for r in records if r.get("kind") == "setup"]
    if not setups:
        return CheckResult(gate, "FAIL", "no setup record in the journal")
    fired = [
        r
        for r in records
        if r.get("kind") == "gate_refusal" and _TRACKER_REFUSAL.search(str(r.get("refusal", "")))
    ]
    if fired:
        return CheckResult(
            gate, "PASS",
            f"gate fired {len(fired)}x (round {fired[0].get('round')}): "
            f"{str(fired[0].get('refusal', ''))[:80]}",
        )
    if "immutable_sha" in setups[0]:
        return CheckResult(
            gate, "PASS",
            f"immutable_sha journaled at setup: {str(setups[0]['immutable_sha'])[:16]}...",
        )
    tracker = workspace / "goal-tracker.md"
    if tracker.is_file() and "## IMMUTABLE" in tracker.read_text(encoding="utf-8"):
        return CheckResult(
            gate, "PASS",
            "gate never fired; goal-tracker.md carries an IMMUTABLE section and the "
            "setup record exists (the sha is held in flow state, not journaled -- "
            "documented journal gap)",
        )
    return CheckResult(
        gate, "N-A",
        "setup record present but no refusal fired, no journaled immutable_sha, "
        "and no goal-tracker.md on disk to inspect",
    )


def check_round_artifacts(records: list[dict], workspace: Path) -> CheckResult:
    """v1 round-summary/contract presence: artifacts exist for journaled rounds.

    Strict where the journal proves an artifact must exist: every round with a
    `verdict` record went through the reviewer turn, which writes the review
    prompt and result before journaling. Prompts/summaries are reported as
    counts (pre-M2 loops and failed turns legitimately lack them).
    """
    gate = "round-artifacts"
    rounds = _rounds(records)
    if not rounds:
        return CheckResult(gate, "N-A", "no rounds journaled")
    loop_dir = workspace / ".rlcr"
    present = {
        stem: [r for r in rounds if (loop_dir / f"round-{r}-{stem}.md").is_file()]
        for stem in ARTIFACT_STEMS
    }
    verdict_rounds = sorted(
        {int(r["round"]) for r in records if r.get("kind") == "verdict" and isinstance(r.get("round"), int)}
    )
    missing = [
        f"round {r}: {stem}"
        for r in verdict_rounds
        for stem in ("review-prompt", "review-result")
        if r not in present[stem]
    ]
    if missing:
        return CheckResult(
            gate, "FAIL",
            "verdict journaled but review artifact missing: " + "; ".join(missing[:6]),
        )
    counts = ", ".join(f"{stem} {len(hits)}/{len(rounds)}" for stem, hits in present.items())
    if not any(present.values()):
        return CheckResult(
            gate, "N-A",
            f"rounds {rounds[0]}..{rounds[-1]} journaled but no round artifacts on "
            "disk (pre-M2 journal era)",
        )
    return CheckResult(gate, "PASS", f"rounds {rounds[0]}..{rounds[-1]}: {counts}")


def check_lesson_delta(records: list[dict], workspace: Path) -> CheckResult:
    """v1 BitLesson delta validation: the per-round lesson gate (M3).

    The gate journals only its refusals, so a silent pass is observed through
    the summaries themselves: every round summary on disk must carry a
    `## Lesson Delta` heading.
    """
    gate = "lesson-delta-gate"
    refusals = [r for r in records if r.get("kind") == "lesson_refusal"]
    loop_dir = workspace / ".rlcr"
    summaries = sorted(loop_dir.glob("round-*-summary.md")) if loop_dir.is_dir() else []
    missing = [p.name for p in summaries if not _DELTA_HEADING.search(p.read_text(encoding="utf-8"))]
    if missing:
        return CheckResult(
            gate, "FAIL",
            f"summary without a `## Lesson Delta` block: {', '.join(missing[:6])}",
        )
    if refusals:
        rounds = sorted({r.get("round") for r in refusals})
        return CheckResult(
            gate, "PASS",
            f"gate fired {len(refusals)}x (rounds {rounds}); "
            f"{len(summaries)} summaries on disk all carry a Lesson Delta block",
        )
    if summaries:
        return CheckResult(
            gate, "PASS",
            f"no refusals; all {len(summaries)} round summaries carry a Lesson Delta block",
        )
    return CheckResult(
        gate, "N-A",
        "no lesson_refusal records and no summaries on disk (pre-M3 journal, or "
        "bitlesson gate never engaged)",
    )


def check_max_iterations(records: list[dict], max_iterations: int) -> CheckResult:
    """v1 max-iteration gate: no round at or past the budget may be journaled."""
    gate = "max-iterations"
    # Executed rounds only: the terminal record's `round` is the final counter
    # value (for maxiter, one past the last executed round), not a round that ran.
    rounds = sorted(
        {int(r["round"]) for r in records
         if isinstance(r.get("round"), int) and r.get("kind") != "terminal"}
    )
    over = [r for r in rounds if r >= max_iterations]
    if over:
        return CheckResult(
            gate, "FAIL",
            f"rounds past the budget ({max_iterations}) journaled: {over[:6]}",
        )
    terminals = [r for r in records if r.get("kind") == "terminal"]
    note = ""
    if terminals and terminals[0].get("terminal") == "maxiter":
        hit = terminals[0].get("round")
        note = f"; maxiter terminal at round {hit}"
        if hit != max_iterations:
            return CheckResult(
                gate, "FAIL",
                f"maxiter terminal at round {hit}, expected {max_iterations}",
            )
    seen = f"max round journaled: {rounds[-1]}" if rounds else "no rounds journaled"
    return CheckResult(gate, "PASS", f"{seen} (budget {max_iterations}){note}")


def check_terminal(records: list[dict], workspace: Path) -> CheckResult:
    """v1 terminal taxonomy: exactly one terminal record, from the five states."""
    gate = "terminal-taxonomy"
    terminals = [r for r in records if r.get("kind") == "terminal"]
    if len(terminals) != 1:
        return CheckResult(gate, "FAIL", f"{len(terminals)} terminal records, expected exactly 1")
    reason = terminals[0].get("terminal")
    if reason not in TERMINAL:
        return CheckResult(gate, "FAIL", f"terminal state {reason!r} not in {TERMINAL}")
    state_file = workspace / ".rlcr" / f"{reason}-state.md"
    if not state_file.is_file():
        return CheckResult(
            gate, "FAIL", f"terminal={reason} journaled but {state_file.name} missing"
        )
    return CheckResult(
        gate, "PASS",
        f"exactly one terminal record: {reason} at round {terminals[0].get('round')}; "
        f"{state_file.name} on disk",
    )


def check_arbiter_before_complete(records: list[dict]) -> CheckResult:
    """The arbiter is the only termination authority: no `complete` without it."""
    gate = "arbiter-before-complete"
    terminals = [r for r in records if r.get("kind") == "terminal"]
    if not terminals or terminals[0].get("terminal") != "complete":
        reason = terminals[0].get("terminal") if terminals else "(none)"
        return CheckResult(gate, "N-A", f"terminal is {reason}, not complete")
    solved = [r for r in records if r.get("kind") == "arbiter" and r.get("solved") is True]
    if not solved:
        return CheckResult(
            gate, "FAIL", "terminal=complete but no arbiter record with solved: true"
        )
    return CheckResult(
        gate, "PASS",
        f"arbiter solved at round {solved[-1].get('round')}, "
        f"terminal=complete at round {terminals[0].get('round')}",
    )


def check_verdict_malformed(records: list[dict]) -> CheckResult:
    """v1 malformed-output rule: re-ask once, then count STALLED -- so at most
    one verdict_malformed record per round may ever appear."""
    gate = "verdict-malformed-policy"
    if not _reviewer_enabled(records):
        return CheckResult(
            gate, "N-A", "no verdict/drift records (solo arm or pre-M2 journal)"
        )
    per_round: dict[int, int] = {}
    for r in records:
        if r.get("kind") == "verdict_malformed" and isinstance(r.get("round"), int):
            per_round[int(r["round"])] = per_round.get(int(r["round"]), 0) + 1
    over = {r: n for r, n in per_round.items() if n > 1}
    if over:
        return CheckResult(
            gate, "FAIL",
            f"verdict_malformed beyond the re-ask-once policy: {over}",
        )
    total = sum(per_round.values())
    return CheckResult(
        gate, "PASS", f"{total} verdict_malformed record(s), at most 1 per round"
    )


def run_checks(workspace: Path, max_iterations: int = DEFAULT_MAX_ITERATIONS) -> list[CheckResult]:
    """All gate checks against one workspace, in merge-plan §3.2 order."""
    try:
        records = load_journal(workspace)
    except (FileNotFoundError, ValueError) as failed:
        return [CheckResult("journal", "FAIL", str(failed))]
    return [
        check_tracker_immutable(records, workspace),
        check_round_artifacts(records, workspace),
        check_lesson_delta(records, workspace),
        check_max_iterations(records, max_iterations),
        check_terminal(records, workspace),
        check_arbiter_before_complete(records),
        check_verdict_malformed(records),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a finished RLCR workspace against the v1 gate table "
        "(merge-plan.md §3.2). Exit 0 iff no check FAILs."
    )
    parser.add_argument("workspace", type=Path, help="workspace dir holding .rlcr/")
    parser.add_argument("--json", action="store_true", help="emit the checks as JSON")
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=DEFAULT_MAX_ITERATIONS,
        help="configured round budget (default %(default)s, RLCRConfig.max_iterations)",
    )
    args = parser.parse_args(argv)

    results = run_checks(args.workspace, max_iterations=args.max_iterations)
    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        for r in results:
            print(f"{r.status} {r.gate} — {r.evidence}")
    failed = [r for r in results if r.status == "FAIL"]
    if failed and not args.json:
        print(f"\n{len(failed)} check(s) FAILed", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
