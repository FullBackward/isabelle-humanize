"""isabelle_rlcr: the RLCR loop over IsabelleGym (M2: full builder/reviewer).

    hmz exec -f <path>/flows/isabelle_rlcr:rlcr \
        -a kimi/moonshot-cn/kimi-k3:high -a kimi/moonshot-cn/kimi-k3:high \
        "prove the pending theorem in ./workspaces/<task>/"

One round (implementation-plan.md §5.2): pre-gates (fail-closed) -> builder turn
-> mechanical readiness (REST, no agent is believed) -> reviewer schema-verdict
-> drift state machine -> arbiter gate on claimed completion (the loop's only
termination authority) -> commit -> journal. The reviewer steers; the prover
decides. v1's five terminal states throughout.

M1 was the builder-only slice; M3 adds proof memory, finalize/methodology, and
the conformance/ablation tools.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, NamedTuple, Optional

import arbiter
import gates
import memory
import progress
from hmz.flows import Agent, Person, flow
from hmz.flows import Question as Asking
from schemas import LessonSelection, RLCRConfig, TaskSpec, Verdict

#: The loop's own directory inside the workspace (untracked, git-clean-except).
LOOP_DIR = ".rlcr"

#: v1's five terminal states.
TERMINAL = ("complete", "cancel", "maxiter", "stop", "unexpected")

#: What the objective classifier's verdicts map to for the drift machine.
_OBJECTIVE_AS_VERDICT = {
    "PROGRESS": "ADVANCED",
    "STAGNATION": "STALLED",
    "REGRESSION": "REGRESSED",
    "UNKNOWN": "STALLED",  # fail-closed: no signal is not progress
}

_TRACKER = """# Goal Tracker -- {{THEORY_NAME}}

## IMMUTABLE

Task: {{TASK}}

Theory: `{{THEORY_NAME}}` (file: `{{PROBLEM_FILE}}`)
Target theorem: `{{TARGET_THEOREM}}`
Imports: {{IMPORTS}}
Field: {{FIELD}}

Acceptance: `isabelle build` of the problem file passes in strict mode with no
`sorry`/`oops` and the target theorem present, decided by the flow's arbiter.

## MUTABLE

(round 0: proof state not yet recorded)
"""


class Roles(NamedTuple):
    """The three the flow drives: the builder, the reviewer that reads its work
    (never the termination authority), and the person at the prompt."""

    builder: Agent
    reviewer: Agent
    human: Person


def _render(template: str, **fields: object) -> str:
    """{{VAR}} single-pass substitution: injected content is never re-expanded."""
    return re.sub(
        r"\{\{(\w+)\}\}", lambda m: str(fields.get(m.group(1), m.group(0))), template
    )


def _template(name: str) -> str:
    return (Path(__file__).parent / "prompts" / name).read_text(encoding="utf-8")


def _journal(loop_dir: Path, kind: str, **data: object) -> None:
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "kind": kind}
    record.update(data)
    with (loop_dir / "journal.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(record, default=str) + "\n")


def _commit(root: Path, round_no: int, note: str) -> None:
    """One commit per round: per-round diffs are the reviewable history.

    A workspace may have no git identity configured; the loop brings its own
    rather than depending on (or mutating) the repo's config.
    """
    gates.git("add", "-A", at=root)
    status, _ = gates.git("diff", "--cached", "--quiet", at=root)
    if status == 1:  # something staged
        gates.git(
            "-c",
            "user.email=rlcr@local",
            "-c",
            "user.name=rlcr",
            "commit",
            "-m",
            f"rlcr: round {round_no} ({note})",
            at=root,
        )


def _terminal(root: Path, loop_dir: Path, kept: dict[str, Any], reason: str) -> None:
    assert reason in TERMINAL, f"not a terminal state: {reason}"
    kept["terminal"] = reason
    (loop_dir / f"{reason}-state.md").write_text(
        f"---\nterminal: {reason}\nround: {kept.get('current_round')}\n"
        f"at: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n---\n",
        encoding="utf-8",
    )
    _journal(loop_dir, "terminal", terminal=reason, round=kept.get("current_round"))
    _commit(root, int(kept.get("current_round", 0)), f"terminal {reason}")
    print(f"rlcr: terminal={reason} after round {kept.get('current_round')}")


def _builder_turn(building: Any, prompt: str, loop_dir: Path, round_no: int) -> bool:
    """One builder turn with one retry on transport failure.

    Returns False when the round failed twice -- a flow catches turns rather
    than transports, and a twice-failed round counts as a failed round.
    """
    for attempt in (1, 2):
        try:
            building(prompt, suppress=True)
            return True
        except subprocess.CalledProcessError as why:
            _journal(
                loop_dir, "turn_failure", round=round_no, attempt=attempt, error=str(why)
            )
    return False


def _review(
    agents: Roles,
    cfg: RLCRConfig,
    root: Path,
    tracker: Path,
    spec: Any,
    round_no: int,
    recent_verdicts: list[str],
    loop_dir: Path,
    ready: Any,
    classification_override: Optional[str] = None,
) -> tuple[Optional[Verdict], str, Optional[bool]]:
    """The reviewer turn: schema-asked, fail-closed.

    Extraction failure -> re-ask once -> the round counts as STALLED with no
    issues, exactly v1's malformed-output rule. Returns (verdict, objective
    classification, agreement). The objective classification is flow-side
    (REST goal deltas) when classification_override is given -- the reviewer's
    own observed_goals_*, when it has any, are the fallback, never the source
    of truth.
    """
    aligning = round_no % cfg.full_review_round == cfg.full_review_round - 1
    asked = _render(
        _template("review.md"),
        TARGET_THEOREM=spec.target_theorem,
        PROBLEM_FILE=cfg.problem_file,
        CURRENT_ROUND=round_no,
        GOAL_TRACKER_FILE=tracker.name,
        PROVER_ACCESS=(
            "2. Check the current state of the proof with your MCP tools (they\n"
            "   re-sync from disk; you cannot modify anything through them):\n"
            "   - `mcp__isabellegym__isabelle_diagnostic_messages` on the problem file\n"
            "   - `mcp__isabellegym__isabelle_proof_state`\n"
            "   - `mcp__isabellegym__isabelle_goal` at lines that changed\n"
            "   - `mcp__isabellegym__isabelle_query` for read-only library lookups\n"
            "     (find_theorems, thm, print_*) -- never the internet\n"
            "   - `mcp__isabellegym__isabelle_sledgehammer` at a stuck line, to see\n"
            "     what automation the builder could have used"
            if cfg.reviewer_mcp
            else
            "2. You have NO prover tools in this run (no MCP attached). Review from\n"
            "   the git diff, the goal tracker, and the mechanical readiness below."
        ),
        MECHANICAL_READINESS=(
            f"success={ready.success} proof_open={ready.proof_open} "
            f"used_sorry={ready.used_sorry}; open subgoals: {len(ready.goals)}"
            + (
                "\n"
                + "\n".join(f"  {i + 1}. {goal}" for i, goal in enumerate(ready.goals[:10]))
                if ready.goals
                else ""
            )
        ),
        FULL_REVIEW_NOTE=(
            "4. FULL-ALIGNMENT ROUND: re-audit the ENTIRE goal tracker against the "
            "current theory, not just this round's increment -- has the MUTABLE "
            "section drifted from where the proof actually is?"
            if aligning
            else ""
        ),
        RECENT_VERDICTS="; ".join(recent_verdicts[-3:]) or "(none yet)",
    )
    (loop_dir / f"round-{round_no}-review-prompt.md").write_text(asked, encoding="utf-8")
    verdict = agents.reviewer(asked, suppress=True, schema=Verdict)
    if verdict is None:
        verdict = agents.reviewer(asked, suppress=True, schema=Verdict)
    if verdict is None:
        _journal(loop_dir, "verdict_malformed", round=round_no, action="counted_stalled")
        verdict = Verdict(
            mainline="STALLED",
            issues=["reviewer produced no parseable verdict twice; counted STALLED"],
            open_question=None,
            observed_goals_before=[],
            observed_goals_after=[],
            readiness=_UNKNOWN_READINESS,
        )
    (loop_dir / f"round-{round_no}-review-result.md").write_text(
        verdict.model_dump_json(indent=2), encoding="utf-8"
    )
    classification = classification_override or progress.classify(
        verdict.observed_goals_before or None, verdict.observed_goals_after or None
    )
    agreement = progress.agrees(classification, verdict.mainline)
    _journal(
        loop_dir,
        "verdict",
        round=round_no,
        mainline=verdict.mainline,
        issues=verdict.issues,
        open_question=verdict.open_question,
        objective=classification,
        agreement=agreement,
        aligning=aligning,
    )
    return verdict, classification, agreement


#: What a verdict reports when the reviewer could not read readiness (never
#: trusted for gating -- the flow's own REST readiness is the gate).
_UNKNOWN_READINESS = {"success": False, "proof_open": True, "used_sorry": False}


def _drift_signal(cfg: RLCRConfig, verdict: Verdict, classification: str) -> str:
    """Which signal drives the drift machine (the ablation flag)."""
    if cfg.drift_source == "objective":
        return _OBJECTIVE_AS_VERDICT[classification]
    if cfg.drift_source == "either":
        objective = _OBJECTIVE_AS_VERDICT[classification]
        if verdict.mainline == "ADVANCED" and objective == "ADVANCED":
            return "ADVANCED"
        if "REGRESSED" in (verdict.mainline, objective):
            return "REGRESSED"
        return "STALLED"
    return verdict.mainline  # default: v1 parity, the reviewer's verdict


def _escalate(
    agents: Roles, cfg: RLCRConfig, question: str, loop_dir: Path, round_no: int
) -> Optional[str]:
    """An open question goes to the person, as a typed question with options.

    Nobody at the prompt answers with nothing, and the loop carries on -- a run
    left going overnight is not stopped by a question it cannot hear. The one
    answer that ends the loop is an explicit cancel.
    """
    if not (question and cfg.ask_open_questions):
        return None
    said = agents.human.asked(
        Asking(
            text=(
                f"Round {round_no}: the reviewer raises an open question:\n\n"
                f"{question}\n\nHow should the loop proceed?"
            ),
            options=(
                "Continue: tell the builder what to assume",
                "Continue: let the builder decide",
                "Cancel the loop",
            ),
        )
    )
    _journal(loop_dir, "escalation", round=round_no, question=question, answer=said)
    if said is None:
        return None
    if said.strip().lower().startswith("cancel"):
        return "cancel"
    return said


def _lesson_note(
    agents: Roles, cfg: RLCRConfig, kb: Path, spec: Any, situation: str, loop_dir: Path
) -> str:
    """The lesson selector: a cheap schema-ask, short-circuited on an empty KB.

    Returns the LESSON_NOTE text for the builder prompt ("" when there is
    nothing to say). A selector that fails to answer in shape is skipped, not
    retried: lessons are an aid, not a gate.
    """
    if cfg.solo or not cfg.bitlesson_required or memory.select_relevant(kb) is None:
        return ""
    kb_text = kb.read_text(encoding="utf-8")
    index = "\n".join(
        f"- {one}" for one in memory.lesson_ids(kb_text)
    )
    asked = _render(
        _template("select_lessons.md"),
        TARGET_THEOREM=spec.target_theorem,
        SITUATION=situation,
        KB_INDEX=index,
    )
    picked = agents.reviewer(asked, suppress=True, schema=LessonSelection)
    if picked is None or not picked.lesson_ids:
        return ""
    found = memory.entries(kb_text, picked.lesson_ids)
    if not found:
        return ""
    _journal(loop_dir, "lessons_selected", ids=list(found))
    return (
        "Relevant proof lessons from earlier rounds -- read them before acting:\n\n"
        + "\n\n".join(found.values())
    )


def _finalize(
    agents: Roles,
    cfg: RLCRConfig,
    root: Path,
    loop_dir: Path,
    building: Any,
    spec: Any,
    round_no: int,
) -> None:
    """The finalize pass: one optional simplification, arbiter re-checked.

    The verified bytes are committed before the pass; a pass whose re-check
    fails is reverted to them outright (those exact bytes passed the build).
    """
    if not cfg.finalize_simplify:
        return
    _commit(root, round_no, "arbiter solved")
    asked = _render(
        _template("finalize.md"),
        TARGET_THEOREM=spec.target_theorem,
        PROBLEM_FILE=cfg.problem_file,
    )
    _builder_turn(building, asked, loop_dir, round_no)
    verdict = arbiter.check(root, cfg, spec)
    if verdict["solved"]:
        _journal(loop_dir, "finalize", round=round_no, kept=True)
        _commit(root, round_no, "finalize")
        return
    _journal(
        loop_dir,
        "finalize",
        round=round_no,
        kept=False,
        reason=verdict.get("reason", "")[:500],
    )
    gates.git("checkout", "--", cfg.problem_file, at=root)
    _commit(root, round_no, "finalize reverted to verified")


def _methodology(
    agents: Roles, cfg: RLCRConfig, root: Path, loop_dir: Path, terminal: str, round_no: int
) -> None:
    """The sanitized methodology retrospective (skipped under privacy)."""
    if cfg.privacy:
        return
    asked = _render(
        _template("methodology.md"),
        TERMINAL=terminal,
        CURRENT_ROUND=round_no,
        JOURNAL_FILE=loop_dir / "journal.jsonl",
    )
    try:
        agents.reviewer(asked, suppress=True)
        _commit(root, round_no, "methodology analysis")
    except subprocess.CalledProcessError as why:
        _journal(loop_dir, "methodology_error", error=str(why))


@flow(name="rlcr", resumable=True)
def run(
    agents: Roles,
    task: str,
    config: RLCRConfig | None = None,
    state: dict[str, Any] | None = None,
) -> None:
    """The RLCR loop: build, review, steer, and let the prover decide."""
    cfg = config or RLCRConfig()
    root = Path.cwd()
    loop_dir = root / LOOP_DIR
    loop_dir.mkdir(exist_ok=True)
    kept = state if state is not None else {}
    if kept.get("terminal"):
        print(f"rlcr: already terminal ({kept['terminal']}); nothing to do.")
        return

    problem = root / cfg.problem_file
    if not problem.is_file():
        raise ValueError(f"{problem}: no problem file -- nothing to prove")
    spec = arbiter.parse_spec(problem.read_text(encoding="utf-8"), cfg.field)
    gates.require_git_repo(root)

    tracker = root / "goal-tracker.md"
    kb = root / "proof-lessons.md"
    if gates.stale_resume(kept, loop_dir, tracker):
        # hmz resume is keyed on the workspace path: this state belongs to a
        # previous incarnation of the workspace (deleted and recreated at the
        # same path). Reset and set up fresh rather than skip setup.
        kept.clear()
        _journal(
            loop_dir, "resume_reset",
            note="state from a previous workspace incarnation; re-running setup",
        )
    if "immutable_sha" not in kept:
        if not kb.exists():
            kb.write_text(memory.KB_TEMPLATE, encoding="utf-8")
        tracker.write_text(
            _render(
                _TRACKER,
                TASK=task,
                THEORY_NAME=spec.theory_name,
                PROBLEM_FILE=cfg.problem_file,
                TARGET_THEOREM=spec.target_theorem,
                IMPORTS=", ".join(spec.imports),
                FIELD=spec.field,
            ),
            encoding="utf-8",
        )
        held = gates.immutable_section(tracker.read_text(encoding="utf-8"))
        kept["immutable_sha"] = hashlib.sha256(held.encode()).hexdigest()
        kept["spec"] = spec.model_dump()  # setup-time spec: resume re-reads
        # THIS, never a builder-edited TASK comment
        _, kept["start_branch"] = gates.git("rev-parse", "--abbrev-ref", "HEAD", at=root)
        kept["start_branch"] = kept["start_branch"].strip()
        kept["current_round"] = 0
        kept["stall"] = 0
        kept["last_verdict"] = ""
        kept["verdicts_history"] = []
        _journal(
            loop_dir,
            "setup",
            spec=spec.model_dump(),
            start_branch=kept["start_branch"],
            immutable_sha=kept["immutable_sha"],
            model_builder=cfg.model_builder or "unpinned",
            model_reviewer=cfg.model_reviewer or "unpinned",
            drift_source=cfg.drift_source,
        )
        _commit(root, 0, "setup")
    kept.setdefault("stall", 0)
    kept.setdefault("last_verdict", "")
    kept.setdefault("verdicts_history", [])
    kept.setdefault("last_goals", [])
    if isinstance(kept.get("spec"), dict):
        # The setup-time spec wins over whatever the TASK comment says now:
        # the comment is agent-editable, the snapshot is not.
        spec = TaskSpec(**kept["spec"])

    building = agents.builder.new()
    prompt = _render(
        _template("round0.md"),
        TASK=task,
        PROBLEM_FILE=cfg.problem_file,
        THEORY_NAME=spec.theory_name,
        TARGET_THEOREM=spec.target_theorem,
        GOAL_TRACKER_FILE=tracker.name,
        SUMMARY_FILE=f"{LOOP_DIR}/round-0-summary.md",
        LESSON_NOTE=_lesson_note(
            agents, cfg, kb, spec, "round 0, nothing attempted yet", loop_dir
        ),
    )
    (loop_dir / "round-0-prompt.md").write_text(prompt, encoding="utf-8")

    while gates.under_max_iterations(int(kept["current_round"]), cfg.max_iterations):
        round_no = int(kept["current_round"])
        print(f"rlcr: round {round_no}")

        # 1. Pre-gates, fail-closed, cheapest first. Corruption (state that
        # cannot be trusted, e.g. resume state without its artifacts) ends the
        # loop `unexpected`, v1's taxonomy -- recorded, not raised through.
        try:
            refusal = (
                gates.branch_consistency(root, str(kept.get("start_branch", "")))
                or gates.goal_tracker_preserved(tracker, str(kept["immutable_sha"]))
                or gates.workspace_clean(root)
                or gates.large_file(problem)
            )
        except gates.GateCorruption as corrupted:
            _journal(loop_dir, "gate_corruption", round=round_no, error=str(corrupted))
            _terminal(root, loop_dir, kept, "unexpected")
            return
        if refusal is not None:
            _journal(loop_dir, "gate_refusal", round=round_no, refusal=refusal)
            prompt = (
                "A loop gate refused this round. Fix exactly this, then stop:\n\n"
                + refusal
            )
            _builder_turn(building, prompt, loop_dir, round_no)
            continue

        # 2. The builder turn.
        if not _builder_turn(building, prompt, loop_dir, round_no):
            verdict_text = (
                "Your previous turn failed twice at the transport level. "
                "Pick up from the file as it stands."
            )
            prompt = _render(
                _template("next_round.md"),
                CURRENT_ROUND=round_no + 1,
                TARGET_THEOREM=spec.target_theorem,
                PROBLEM_FILE=cfg.problem_file,
                GOAL_TRACKER_FILE=tracker.name,
                SUMMARY_FILE=f"{LOOP_DIR}/round-{round_no + 1}-summary.md",
                LESSON_NOTE="",
                REASON=verdict_text,
            )
            kept["stall"] = int(kept["stall"]) + 1
            kept["current_round"] = round_no + 1
            _commit(root, round_no, "turn failure")
            continue

        # 2b. The round is not complete without its summary's lesson delta.
        if cfg.bitlesson_required:
            summary_path = loop_dir / f"round-{round_no}-summary.md"
            summary = (
                summary_path.read_text(encoding="utf-8")
                if summary_path.is_file()
                else ""
            )
            delta_refusal = memory.validate_delta(
                summary, kb, allow_empty_none=not cfg.require_lesson_entry_for_none
            )
            if delta_refusal is not None:
                _journal(
                    loop_dir, "lesson_refusal", round=round_no, refusal=delta_refusal
                )
                prompt = (
                    "Your round summary is not in order. Fix exactly this "
                    f"(write `{summary_path}` and any KB entries it needs), "
                    "then stop:\n\n" + delta_refusal
                )
                _builder_turn(building, prompt, loop_dir, round_no)
                continue

        # 3. Mechanical readiness: the prover over REST, never an agent's claim.
        #    One backoff retry before burning a builder round on it: a full
        #    session pool recovers on its own (leases are reaped), tokens do not.
        ready = None
        readiness_error = ""  # the except's `as` name is deleted with its
        # block (PEP 3110) -- capture the text, not the exception
        for attempt, wait in ((1, 0), (2, 30)):
            if wait:
                time.sleep(wait)
            try:
                ready = arbiter.readiness(root, cfg, spec)
                _journal(
                    loop_dir, "readiness", round=round_no, attempt=attempt,
                    **ready.model_dump(),
                )
                break
            except arbiter.ArbiterError as failed:
                readiness_error = str(failed)
                _journal(
                    loop_dir, "readiness_error", round=round_no, attempt=attempt,
                    error=readiness_error,
                )
        if ready is None:
            prompt = _render(
                _template("next_round.md"),
                CURRENT_ROUND=round_no + 1,
                TARGET_THEOREM=spec.target_theorem,
                PROBLEM_FILE=cfg.problem_file,
                GOAL_TRACKER_FILE=tracker.name,
                SUMMARY_FILE=f"{LOOP_DIR}/round-{round_no + 1}-summary.md",
                LESSON_NOTE="",
                REASON=(
                    "The mechanical readiness check could not run "
                    f"({readiness_error}). Keep editing and checking with the MCP tools."
                ),
            )
            kept["current_round"] = round_no + 1
            _commit(root, round_no, "readiness error")
            continue

        # 3b. Flow-side objective progress: goal deltas from the REST
        #     readiness, never from an agent. Round 0 has no previous
        #     observation; a reviewer without MCP reports none either.
        prev_goals = list(kept.get("last_goals") or [])
        flow_classification = (
            progress.classify(prev_goals, ready.goals)
            if prev_goals and ready.goals
            else None
        )
        kept["last_goals"] = ready.goals

        # 4. The reviewer turn (steering only; never the termination authority).
        #    The solo ablation arm skips it entirely: identical budget, no critique.
        if cfg.solo:
            verdict, classification = None, (flow_classification or "UNKNOWN")
        else:
            verdict, classification, _ = _review(
                agents, cfg, root, tracker, spec, round_no,
                list(kept.get("verdicts_history", [])), loop_dir,
                ready, classification_override=flow_classification,
            )
            kept["verdicts_history"] = (list(kept.get("verdicts_history", [])) + [verdict.mainline])[-10:]
            kept["last_verdict"] = verdict.mainline

            # 5. The drift state machine (v1's counters, verdict source configurable).
            signal = _drift_signal(cfg, verdict, classification)
            if signal == "ADVANCED":
                kept["stall"] = 0
            else:
                kept["stall"] = int(kept["stall"]) + 1
            _journal(
                loop_dir, "drift", round=round_no, signal=signal, stall=kept["stall"]
            )
            if int(kept["stall"]) >= cfg.drift_stall_breaker:
                _methodology(agents, cfg, root, loop_dir, "stop", round_no)
                _terminal(root, loop_dir, kept, "stop")
                return

            # 6. Open-question escalation to the person.
            escalation = _escalate(agents, cfg, verdict.open_question or "", loop_dir, round_no)
            if escalation == "cancel":
                _methodology(agents, cfg, root, loop_dir, "cancel", round_no)
                _terminal(root, loop_dir, kept, "cancel")
                return

        # 7. The arbiter gate: the only termination authority.
        if ready.proof_finished and ready.sorry_free:
            verdict_build = arbiter.check(root, cfg, spec)
            _journal(loop_dir, "arbiter", round=round_no, **verdict_build)
            if verdict_build["solved"]:
                _finalize(agents, cfg, root, loop_dir, building, spec, round_no)
                _methodology(agents, cfg, root, loop_dir, "complete", round_no)
                _terminal(root, loop_dir, kept, "complete")
                return
            reason = (
                "Completion was claimed, but the arbiter rejected it "
                f"({verdict_build.get('stage')}):\n{verdict_build.get('reason', '')}\n"
                "The proof is NOT done. Continue."
            )
            template = "next_round.md"
        else:
            readiness_text = (
                f"Mechanical readiness: success={ready.success} "
                f"proof_open={ready.proof_open} used_sorry={ready.used_sorry}"
            )
            reason = (
                readiness_text
                if cfg.solo
                else f"Reviewer verdict: {verdict.mainline}\n"
                + ("\n".join(f"- {one}" for one in verdict.issues) or "- (none)")
                + f"\n\n{readiness_text}"
            )
            template = (
                "next_round.md"
                if cfg.solo
                else (
                    "drift_replan.md"
                    if int(kept["stall"]) >= cfg.drift_stall_replan
                    else "next_round.md"
                )
            )

        if not cfg.solo and escalation:
            reason += f"\n\nThe person answered the open question: {escalation}"

        render_args = dict(
            CURRENT_ROUND=round_no + 1,
            TARGET_THEOREM=spec.target_theorem,
            PROBLEM_FILE=cfg.problem_file,
            GOAL_TRACKER_FILE=tracker.name,
            SUMMARY_FILE=f"{LOOP_DIR}/round-{round_no + 1}-summary.md",
            LESSON_NOTE=_lesson_note(
                agents, cfg, kb, spec,
                f"round {round_no} "
                + ("solo arm, " if cfg.solo else f"verdict {verdict.mainline}, ")
                + f"readiness proof_open={ready.proof_open}",
                loop_dir,
            ),
            REASON=reason,
            REVIEW=reason,
            STALL_COUNT=kept["stall"],
            LAST_VERDICT=kept["last_verdict"],
        )
        prompt = _render(_template(template), **render_args)
        # The prompt is persisted under the round it is FOR, not the round just
        # ended: a picked-up run reads back what the builder was last told.
        (loop_dir / f"round-{round_no + 1}-prompt.md").write_text(
            prompt, encoding="utf-8"
        )
        kept["current_round"] = round_no + 1
        _commit(root, round_no, f"verdict {kept['last_verdict']}")

    _methodology(agents, cfg, root, loop_dir, "maxiter", int(kept["current_round"]))
    _terminal(root, loop_dir, kept, "maxiter")


class SoloRoles(NamedTuple):
    """The solo ablation arm's two: a builder, and the person at the prompt."""

    builder: Agent
    human: Person


@flow(name="solo", resumable=True)
def solo(
    agents: SoloRoles,
    task: str,
    config: RLCRConfig | None = None,
    state: dict[str, Any] | None = None,
) -> None:
    """The single-agent control arm: the same loop with the reviewer removed.

    Identical problems, budgets and gates -- but no critique, no drift machine,
    no escalation: mechanical gates and the arbiter only. This is the control
    group that makes the RLCR claim measurable (implementation-plan.md §7).
    """
    cfg = config or RLCRConfig()
    cfg.solo = True
    run(
        Roles(agents.builder, agents.builder, agents.human),  # reviewer unused
        task,
        cfg,
        state,
    )
