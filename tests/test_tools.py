"""Unit tests for tools/ -- conformance checker, ablation metrics, problem import.

Pure-function tests only: synthetic journals written to tmp_path, no live hmz,
no live agents, no Isabelle. The git path of import_problem uses real git from
PATH (same convention as tests/test_gates.py).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import ablation  # noqa: E402
import conformance_check  # noqa: E402
import import_problem  # noqa: E402

TRACKER = """# Goal Tracker -- Smoke

## IMMUTABLE

Task: prove the pending theorem

## MUTABLE

(round 0: nothing yet)
"""


def _workspace(tmp_path: Path, records: list[dict], terminal: str | None = None) -> Path:
    """A fake finished workspace: .rlcr/journal.jsonl + optional terminal file."""
    loop_dir = tmp_path / ".rlcr"
    loop_dir.mkdir(parents=True)
    journal = "".join(json.dumps(r) + "\n" for r in records)
    (loop_dir / "journal.jsonl").write_text(journal, encoding="utf-8")
    if terminal is not None:
        (loop_dir / f"{terminal}-state.md").write_text(
            f"---\nterminal: {terminal}\n---\n", encoding="utf-8"
        )
    (tmp_path / "goal-tracker.md").write_text(TRACKER, encoding="utf-8")
    return tmp_path


def _by_gate(results: list) -> dict:
    return {r.gate: r for r in results}


# --- conformance_check ------------------------------------------------------

# A full M3-era rlcr-arm journal: setup, two rounds with reviewer, arbiter
# solved, complete terminal.
M3_RECORDS = [
    {"ts": "2026-09-01T00:00:00Z", "kind": "setup", "spec": {"theory_name": "Problem"}},
    {"ts": "2026-09-01T00:01:00Z", "kind": "readiness", "round": 0, "success": True},
    {"ts": "2026-09-01T00:02:00Z", "kind": "verdict", "round": 0, "mainline": "ADVANCED",
     "objective": "PROGRESS", "agreement": True},
    {"ts": "2026-09-01T00:02:01Z", "kind": "drift", "round": 0, "signal": "ADVANCED", "stall": 0},
    {"ts": "2026-09-01T00:03:00Z", "kind": "readiness", "round": 1, "success": True},
    {"ts": "2026-09-01T00:04:00Z", "kind": "verdict", "round": 1, "mainline": "ADVANCED",
     "objective": "PROGRESS", "agreement": True},
    {"ts": "2026-09-01T00:04:01Z", "kind": "drift", "round": 1, "signal": "ADVANCED", "stall": 0},
    {"ts": "2026-09-01T00:05:00Z", "kind": "arbiter", "round": 1, "solved": True},
    {"ts": "2026-09-01T00:05:01Z", "kind": "terminal", "terminal": "complete", "round": 1},
]

# The shape of the real M1 builder-only journal (workspaces predate M2 kinds).
M1_RECORDS = [
    {"ts": "2026-09-01T05:06:39Z", "kind": "setup", "spec": {"theory_name": "Problem"}},
    {"ts": "2026-09-01T05:06:39Z", "kind": "gate_refusal", "round": 0,
     "refusal": "Workspace has unexpected changes outside the problem file"},
    {"ts": "2026-09-01T05:08:28Z", "kind": "readiness", "round": 0, "used_sorry": True},
    {"ts": "2026-09-01T05:09:16Z", "kind": "readiness", "round": 1, "used_sorry": False},
    {"ts": "2026-09-01T05:09:22Z", "kind": "arbiter", "round": 1, "solved": True},
    {"ts": "2026-09-01T05:09:22Z", "kind": "terminal", "terminal": "complete", "round": 1},
]


def _m3_artifacts(ws: Path) -> None:
    """The artifacts a two-round M3 run leaves behind."""
    loop_dir = ws / ".rlcr"
    for r in (0, 1):
        (loop_dir / f"round-{r}-review-prompt.md").write_text("review", encoding="utf-8")
        (loop_dir / f"round-{r}-review-result.md").write_text("{}", encoding="utf-8")
        (loop_dir / f"round-{r}-summary.md").write_text(
            f"# Round {r}\n\n## Lesson Delta\nAction: none\n", encoding="utf-8"
        )
    (loop_dir / "round-0-prompt.md").write_text("next", encoding="utf-8")


def test_conformance_full_m3_journal_passes(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, M3_RECORDS, terminal="complete")
    _m3_artifacts(ws)
    results = conformance_check.run_checks(ws)
    failed = [r for r in results if r.status == "FAIL"]
    assert not failed, f"unexpected FAILs: {failed}"
    gates = _by_gate(results)
    assert gates["terminal-taxonomy"].status == "PASS"
    assert gates["arbiter-before-complete"].status == "PASS"
    assert gates["lesson-delta-gate"].status == "PASS"
    assert gates["round-artifacts"].status == "PASS"
    assert gates["verdict-malformed-policy"].status == "PASS"


def test_conformance_m1_era_journal_no_fail(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, M1_RECORDS, terminal="complete")
    results = conformance_check.run_checks(ws)
    assert not [r for r in results if r.status == "FAIL"]
    gates = _by_gate(results)
    # Pre-M2/M3 evidence is reported N-A, never hard-failed.
    assert gates["lesson-delta-gate"].status == "N-A"
    assert gates["verdict-malformed-policy"].status == "N-A"
    assert gates["round-artifacts"].status == "N-A"
    # The tracker gate still passes: tracker with IMMUTABLE on disk + setup.
    assert gates["goal-tracker-immutable"].status == "PASS"


def test_conformance_tracker_gate_fired(tmp_path: Path) -> None:
    records = M1_RECORDS[:1] + [
        {"ts": "2026-09-01T05:07:00Z", "kind": "gate_refusal", "round": 0,
         "refusal": "Goal-tracker IMMUTABLE section was modified."},
        {"ts": "2026-09-01T05:09:22Z", "kind": "terminal", "terminal": "stop", "round": 0},
    ]
    ws = _workspace(tmp_path, records, terminal="stop")
    gates = _by_gate(conformance_check.run_checks(ws))
    assert gates["goal-tracker-immutable"].status == "PASS"
    assert "fired" in gates["goal-tracker-immutable"].evidence


def test_conformance_missing_journal_fails(tmp_path: Path) -> None:
    results = conformance_check.run_checks(tmp_path)
    assert len(results) == 1
    assert results[0].status == "FAIL"
    assert results[0].gate == "journal"


def test_conformance_missing_terminal_fails(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, M1_RECORDS[:-1])  # drop the terminal record
    gates = _by_gate(conformance_check.run_checks(ws))
    assert gates["terminal-taxonomy"].status == "FAIL"


def test_conformance_bad_terminal_state_fails(tmp_path: Path) -> None:
    records = M1_RECORDS[:-1] + [
        {"ts": "2026-09-01T05:09:22Z", "kind": "terminal", "terminal": "done", "round": 1}
    ]
    ws = _workspace(tmp_path, records)
    gates = _by_gate(conformance_check.run_checks(ws))
    assert gates["terminal-taxonomy"].status == "FAIL"
    assert "done" in gates["terminal-taxonomy"].evidence


def test_conformance_terminal_file_missing_fails(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, M1_RECORDS)  # journal says complete, no state file
    gates = _by_gate(conformance_check.run_checks(ws))
    assert gates["terminal-taxonomy"].status == "FAIL"


def test_conformance_round_past_budget_fails(tmp_path: Path) -> None:
    records = M1_RECORDS + [
        {"ts": "2026-09-01T06:00:00Z", "kind": "readiness", "round": 42}
    ]
    ws = _workspace(tmp_path, records, terminal="complete")
    gates = _by_gate(conformance_check.run_checks(ws))
    assert gates["max-iterations"].status == "FAIL"
    assert "42" in gates["max-iterations"].evidence


def test_conformance_complete_without_arbiter_fails(tmp_path: Path) -> None:
    records = [r for r in M1_RECORDS if r.get("kind") != "arbiter"]
    ws = _workspace(tmp_path, records, terminal="complete")
    gates = _by_gate(conformance_check.run_checks(ws))
    assert gates["arbiter-before-complete"].status == "FAIL"


def test_conformance_arbiter_check_na_when_not_complete(tmp_path: Path) -> None:
    records = M1_RECORDS[:-1] + [
        {"ts": "2026-09-01T05:09:22Z", "kind": "terminal", "terminal": "maxiter", "round": 42}
    ]
    ws = _workspace(tmp_path, records, terminal="maxiter")
    gates = _by_gate(conformance_check.run_checks(ws))
    assert gates["arbiter-before-complete"].status == "N-A"
    assert gates["max-iterations"].status == "PASS"  # maxiter at the budget


def test_conformance_verdict_malformed_twice_one_round_fails(tmp_path: Path) -> None:
    records = M3_RECORDS + [
        {"ts": "2026-09-01T00:06:00Z", "kind": "verdict_malformed", "round": 2},
        {"ts": "2026-09-01T00:06:01Z", "kind": "verdict_malformed", "round": 2},
    ]
    ws = _workspace(tmp_path, records, terminal="complete")
    _m3_artifacts(ws)
    gates = _by_gate(conformance_check.run_checks(ws))
    assert gates["verdict-malformed-policy"].status == "FAIL"


def test_conformance_review_artifact_missing_fails(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, M3_RECORDS, terminal="complete")
    _m3_artifacts(ws)
    (ws / ".rlcr" / "round-1-review-result.md").unlink()
    gates = _by_gate(conformance_check.run_checks(ws))
    assert gates["round-artifacts"].status == "FAIL"
    assert "round 1" in gates["round-artifacts"].evidence


def test_conformance_summary_without_delta_fails(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, M3_RECORDS, terminal="complete")
    _m3_artifacts(ws)
    (ws / ".rlcr" / "round-0-summary.md").write_text("# Round 0\nno delta here\n")
    gates = _by_gate(conformance_check.run_checks(ws))
    assert gates["lesson-delta-gate"].status == "FAIL"


def test_conformance_cli_json_and_exit_codes(tmp_path: Path, capsys) -> None:
    ws = _workspace(tmp_path, M1_RECORDS, terminal="complete")
    assert conformance_check.main([str(ws), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert {one["gate"] for one in out} >= {"terminal-taxonomy", "max-iterations"}
    # A failing workspace exits 1; plain output prints one line per check.
    bad = _workspace(tmp_path / "bad", M1_RECORDS[:-1])
    assert conformance_check.main([str(bad)]) == 1
    lines = capsys.readouterr().out.strip().splitlines()
    assert any(line.startswith("FAIL terminal-taxonomy") for line in lines)


# --- ablation metrics -------------------------------------------------------

ABLATION_RECORDS = [
    {"ts": "2026-09-01T00:00:00Z", "kind": "setup"},
    {"ts": "2026-09-01T00:01:00Z", "kind": "verdict", "round": 0, "agreement": True},
    {"ts": "2026-09-01T00:01:01Z", "kind": "drift", "round": 0, "signal": "STALLED", "stall": 1},
    {"ts": "2026-09-01T00:02:00Z", "kind": "verdict", "round": 1, "agreement": False},
    {"ts": "2026-09-01T00:02:01Z", "kind": "drift", "round": 1, "signal": "STALLED", "stall": 2},
    {"ts": "2026-09-01T00:03:00Z", "kind": "verdict", "round": 2, "agreement": None},
    {"ts": "2026-09-01T00:04:00Z", "kind": "arbiter", "round": 3, "solved": True},
    {"ts": "2026-09-01T00:04:10Z", "kind": "terminal", "terminal": "complete", "round": 3},
]

KB_TEXT = """# Proof Lessons

### BL-20260901-simp-before-auto
- Scope: HOL goals

### BL-20260901-induction-first
- Scope: recursive datatypes
"""


def test_ablation_metrics_from_records() -> None:
    metrics = ablation.metrics_from_records(ABLATION_RECORDS, KB_TEXT)
    assert metrics["solved"] is True
    assert metrics["terminal"] == "complete"
    assert metrics["rounds"] == 3
    assert metrics["wall_s"] == 250.0  # 00:00:00 -> 00:04:10
    assert metrics["stalls"] == 2
    assert metrics["verdicts"] == 3
    # agreement over non-null verdicts only: 1 of 2.
    assert metrics["agreement_rate"] == 0.5
    assert metrics["lessons"] == 2


def test_ablation_metrics_unsolved_no_terminal() -> None:
    records = [
        {"ts": "2026-09-01T00:00:00Z", "kind": "setup"},
        {"ts": "2026-09-01T00:01:00Z", "kind": "readiness", "round": 5},
        {"ts": "2026-09-01T00:02:00Z", "kind": "arbiter", "round": 5, "solved": False},
    ]
    metrics = ablation.metrics_from_records(records)
    assert metrics["solved"] is False
    assert metrics["terminal"] is None
    assert metrics["rounds"] == 5  # max journaled round without a terminal record
    assert metrics["agreement_rate"] is None
    assert metrics["lessons"] == 0


def test_ablation_summarize_run(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, ABLATION_RECORDS, terminal="complete")
    (ws / "proof-lessons.md").write_text(KB_TEXT, encoding="utf-8")
    summary = ablation.summarize_run(ws)
    assert summary["status"] == "ok"
    assert summary["solved"] is True
    assert summary["lessons"] == 2
    missing = ablation.summarize_run(tmp_path / "nothing")
    assert missing["status"] == "no-journal"


def test_ablation_build_command_arms() -> None:
    flow = Path("/x/flows/isabelle_rlcr")
    rlcr = ablation.build_command(flow, "rlcr", "kimi/k3:high", "task")
    solo = ablation.build_command(flow, "solo", "kimi/k3:high", "task")
    assert rlcr[1:4] == ["exec", "-f", f"{flow}:rlcr"]
    assert rlcr.count("-a") == 2  # builder + reviewer
    assert solo[3] == f"{flow}:solo"
    assert solo.count("-a") == 1  # the single-agent arm
    assert rlcr[-1] == solo[-1] == "task"


def test_ablation_build_command_reviewer_and_config() -> None:
    flow = Path("/x/flows/isabelle_rlcr")
    cmd = ablation.build_command(
        flow, "rlcr", "kimi/k3:high", "task",
        reviewer="dsh/deepseek-v4-flash:high", config=Path("/tmp/c.yaml"),
    )
    agents = [cmd[i + 1] for i, part in enumerate(cmd) if part == "-a"]
    assert agents == ["kimi/k3:high", "dsh/deepseek-v4-flash:high"]
    assert cmd[cmd.index("-c") + 1] == "/tmp/c.yaml"
    # solo arm ignores the reviewer but keeps the config
    solo = ablation.build_command(
        flow, "solo", "kimi/k3:high", "task",
        reviewer="dsh/deepseek-v4-flash:high", config=Path("/tmp/c.yaml"),
    )
    assert solo.count("-a") == 1
    assert "-c" in solo


def test_ablation_resolve_flow_dir() -> None:
    flow = ablation.resolve_flow_dir(REPO_ROOT / "flows" / "isabelle_rlcr")
    assert flow.name == "isabelle_rlcr"
    assert ablation.resolve_flow_dir(REPO_ROOT) == flow  # repo root also accepted
    with pytest.raises(FileNotFoundError):
        ablation.resolve_flow_dir(REPO_ROOT / "tests")


def test_ablation_list_mode_runs_nothing(tmp_path: Path, capsys) -> None:
    src = tmp_path / "prob"
    src.mkdir()
    rc = ablation.main([
        "--flow", str(REPO_ROOT / "flows" / "isabelle_rlcr"),
        "--agent", "kimi/moonshot-cn/kimi-k3:high",
        "--workspace", str(src),
        "--list",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.count("hmz exec") == 2  # one command per arm
    assert ":rlcr" in out and ":solo" in out
    assert "excluding .rlcr/" in out
    # --list planned only: no run dirs were created.
    assert not list(tmp_path.glob("prob-*"))


def test_ablation_prepare_run_dir_inits_git(tmp_path: Path) -> None:
    # A source committed inside another repository carries no .git of its own;
    # the run copy must still be a git repository (the flow refuses otherwise).
    src = tmp_path / "prob"
    src.mkdir()
    (src / "problem.thy").write_text(
        "theory Problem\nimports Main\nbegin\n\nlemma foo: True by sorry\n\nend\n"
    )
    (src / ".rlcr").mkdir()  # leftover loop state: excluded from the copy
    dst = tmp_path / "run"
    ablation.prepare_run_dir(src, dst)
    assert (dst / ".git").is_dir()
    assert not (dst / ".rlcr").exists()
    log = subprocess.run(
        ["git", "-C", str(dst), "log", "--oneline"],
        capture_output=True, text=True, check=False,
    )
    assert "ablation run copy of prob" in log.stdout
    status = subprocess.run(
        ["git", "-C", str(dst), "status", "--porcelain"],
        capture_output=True, text=True, check=False,
    )
    assert status.stdout.strip() == ""  # everything committed

    # A source that DOES carry a .git keeps its own history instead.
    src2 = tmp_path / "prob2"
    import_problem.create_workspace(src / "problem.thy", src2, git=True)
    dst2 = tmp_path / "run2"
    ablation.prepare_run_dir(src2, dst2)
    log2 = subprocess.run(
        ["git", "-C", str(dst2), "log", "--oneline"],
        capture_output=True, text=True, check=False,
    )
    assert "import problem" in log2.stdout
    assert "ablation run copy" not in log2.stdout


# --- import_problem ---------------------------------------------------------

SOURCE_WITH_TASK = """(* TASK: theorem=smoke_true imports=Main field=HOL *)
theory Smoke
imports Main
begin

lemma smoke_true: "True"
  sorry

end
"""

SOURCE_NO_TASK = """theory Scratch
imports Main "HOL-Library.Multiset"
begin

lemma multiset_id: "A + B = B + (A :: 'a multiset)"
  sorry

end
"""


def test_import_with_task_comment(tmp_path: Path) -> None:
    src = tmp_path / "src.thy"
    src.write_text(SOURCE_WITH_TASK, encoding="utf-8")
    ws = tmp_path / "ws"
    spec = import_problem.create_workspace(src, ws, git=False)
    text = (ws / "problem.thy").read_text(encoding="utf-8")
    assert text.startswith("(* TASK: theorem=smoke_true imports=Main field=HOL *)")
    assert text.count("TASK:") == 1  # the old comment was replaced, not duplicated
    assert "theory Problem" in text
    assert "theory Smoke" not in text  # renamed to match the file name
    assert (ws / ".mcp.json").is_file()
    assert (ws / ".gitignore").is_file()
    assert spec.target_theorem == "smoke_true"
    assert spec.theory_name == "Problem"


def test_import_without_task_comment_uses_fallback(tmp_path: Path) -> None:
    src = tmp_path / "src.thy"
    src.write_text(SOURCE_NO_TASK, encoding="utf-8")
    ws = tmp_path / "ws"
    spec = import_problem.create_workspace(src, ws, field="HOL-Analysis", git=False)
    text = (ws / "problem.thy").read_text(encoding="utf-8")
    # Fallback: first lemma name, imports line, the --field argument.
    assert (
        "(* TASK: theorem=multiset_id imports=Main,HOL-Library.Multiset "
        "field=HOL-Analysis *)" in text
    )
    assert spec.field == "HOL-Analysis"
    assert spec.target_theorem == "multiset_id"


def test_import_refuses_existing_workspace(tmp_path: Path) -> None:
    src = tmp_path / "src.thy"
    src.write_text(SOURCE_WITH_TASK, encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    with pytest.raises(FileExistsError):
        import_problem.create_workspace(src, ws, git=False)


def test_import_rejects_garbage_theory(tmp_path: Path) -> None:
    src = tmp_path / "src.thy"
    src.write_text("no theory here\n", encoding="utf-8")
    import arbiter

    with pytest.raises(arbiter.ArbiterError):
        import_problem.create_workspace(src, tmp_path / "ws", git=False)


def test_import_git_repo_and_initial_commit(tmp_path: Path) -> None:
    src = tmp_path / "src.thy"
    src.write_text(SOURCE_WITH_TASK, encoding="utf-8")
    ws = tmp_path / "ws"
    import_problem.create_workspace(src, ws, git=True)
    assert (ws / ".git").is_dir()
    log = subprocess.run(
        ["git", "-C", str(ws), "log", "--oneline"],
        capture_output=True, text=True, check=True, timeout=10,
    ).stdout.strip().splitlines()
    assert len(log) == 1
    assert "import problem smoke_true" in log[0]
    # The initial commit left a clean tree (the workspace-clean gate's premise).
    status = subprocess.run(
        ["git", "-C", str(ws), "status", "--porcelain"],
        capture_output=True, text=True, check=True, timeout=10,
    ).stdout
    assert status == ""
