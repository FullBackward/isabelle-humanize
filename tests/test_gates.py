"""Unit tests for flows/isabelle_rlcr/gates.py -- pure functions, tmp git repos.

No live agents, no Isabelle, no network. Git itself comes from PATH (the
pre-gates are git-porcelain parsers, so they are tested against real repos
created in tmp_path).
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

import gates

TRACKER = """# Goal Tracker -- Smoke

## IMMUTABLE

Task: prove the pending theorem

Theory: `Smoke` (file: `problem.thy`)
Target theorem: `smoke`

## MUTABLE

(round 0: nothing yet)
"""


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo with one committed file, on its initial branch."""
    _git(tmp_path, "init")
    (tmp_path / "problem.thy").write_text(
        "theory Smoke\nimports Main\nbegin\n\nlemma smoke: \"True\" by simp\n\nend\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")
    return tmp_path


# ---------------------------------------------------------------- immutable_section


def test_immutable_section_present() -> None:
    held = gates.immutable_section(TRACKER)
    assert held.startswith("## IMMUTABLE")
    assert "Target theorem: `smoke`" in held
    assert "## MUTABLE" not in held


def test_immutable_section_absent() -> None:
    assert gates.immutable_section("# just a doc\n\nno sections here\n") == ""


# ------------------------------------------------------------ goal_tracker_preserved


@pytest.fixture
def tracker(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "goal-tracker.md"
    path.write_text(TRACKER, encoding="utf-8")
    sha = hashlib.sha256(gates.immutable_section(TRACKER).encode()).hexdigest()
    return path, sha


def test_goal_tracker_preserved_pass(tracker: tuple[Path, str]) -> None:
    path, sha = tracker
    assert gates.goal_tracker_preserved(path, sha) is None


def test_goal_tracker_preserved_mutable_edit_passes(tracker: tuple[Path, str]) -> None:
    path, sha = tracker
    path.write_text(TRACKER + "\nround 1: tried simp\n", encoding="utf-8")
    assert gates.goal_tracker_preserved(path, sha) is None


def test_goal_tracker_preserved_tampered_refuses(tracker: tuple[Path, str]) -> None:
    path, sha = tracker
    path.write_text(TRACKER.replace("smoke", "other_theorem"), encoding="utf-8")
    refusal = gates.goal_tracker_preserved(path, sha)
    assert refusal is not None
    assert "IMMUTABLE" in refusal


def test_goal_tracker_preserved_missing_refuses(tmp_path: Path) -> None:
    refusal = gates.goal_tracker_preserved(tmp_path / "goal-tracker.md", "0" * 64)
    assert refusal is not None
    assert "missing" in refusal.lower()


def test_goal_tracker_preserved_no_immutable_raises(tmp_path: Path) -> None:
    path = tmp_path / "goal-tracker.md"
    path.write_text("# no immutable section\n", encoding="utf-8")
    with pytest.raises(gates.GateCorruption):
        gates.goal_tracker_preserved(path, "0" * 64)


# -------------------------------------------------------------- branch_consistency


def test_branch_consistency_same_passes(repo: Path) -> None:
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    assert gates.branch_consistency(repo, branch) is None


def test_branch_consistency_different_refuses(repo: Path) -> None:
    start = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _git(repo, "switch", "-c", "other")
    refusal = gates.branch_consistency(repo, start)
    assert refusal is not None
    assert "other" in refusal
    assert start in refusal


# ----------------------------------------------------------------- workspace_clean


def test_workspace_clean_passes(repo: Path) -> None:
    assert gates.workspace_clean(repo) is None


def test_workspace_clean_dirty_problem_file_passes(repo: Path) -> None:
    # Unstaged modification as the FIRST porcelain line (" M problem.thy"):
    # guards the regression where git() stripped the leading status column.
    (repo / "problem.thy").write_text("theory Smoke\nimports Main\nbegin\nend\n", encoding="utf-8")
    assert gates.workspace_clean(repo) is None


def test_workspace_clean_staged_problem_file_passes(repo: Path) -> None:
    # Staged modification ("M  problem.thy"): no leading space, no strip bug.
    (repo / "problem.thy").write_text("theory Smoke\nimports Main\nbegin\nend\n", encoding="utf-8")
    _git(repo, "add", "problem.thy")
    assert gates.workspace_clean(repo) is None


def test_workspace_clean_dirty_other_file_refuses(repo: Path) -> None:
    (repo / "other.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "other.txt")
    _git(repo, "commit", "-m", "add other")
    (repo / "other.txt").write_text("changed\n", encoding="utf-8")
    refusal = gates.workspace_clean(repo)
    assert refusal is not None
    assert "other.txt" in refusal


def test_workspace_clean_loop_artifacts_pass(repo: Path) -> None:
    loop_dir = repo / ".rlcr"
    loop_dir.mkdir()
    (loop_dir / "journal.jsonl").write_text("{}\n", encoding="utf-8")
    assert gates.workspace_clean(repo) is None


# --------------------------------------------------------------------- large_file


def test_large_file_under_limit(tmp_path: Path) -> None:
    path = tmp_path / "problem.thy"
    path.write_text("\n".join(f"line {i}" for i in range(5)), encoding="utf-8")
    assert gates.large_file(path, max_lines=5) is None


def test_large_file_over_limit(tmp_path: Path) -> None:
    path = tmp_path / "problem.thy"
    path.write_text("\n".join(f"line {i}" for i in range(6)), encoding="utf-8")
    refusal = gates.large_file(path, max_lines=5)
    assert refusal is not None
    assert "6 lines" in refusal


# ----------------------------------------------------------- under_max_iterations


def test_under_max_iterations_boundary() -> None:
    assert gates.under_max_iterations(0, 1) is True
    assert gates.under_max_iterations(1, 1) is False
    assert gates.under_max_iterations(2, 1) is False


# -------------------------------------------------------------- require_git_repo


def test_require_git_repo_passes(repo: Path) -> None:
    gates.require_git_repo(repo)  # must not raise


def test_require_git_repo_raises_outside_repo(tmp_path: Path) -> None:
    with pytest.raises(gates.GateCorruption):
        gates.require_git_repo(tmp_path)


# -------------------------------------------------------------- stale_resume


def test_stale_resume_fresh_run_is_not_stale(tmp_path: Path) -> None:
    # No setup state at all: a genuinely fresh run.
    assert gates.stale_resume({}, tmp_path / ".rlcr", tmp_path / "goal-tracker.md") is False


def test_stale_resume_detects_recreated_workspace(tmp_path: Path) -> None:
    # State from a previous incarnation, workspace recreated at the same path:
    # no tracker, no journal -- setup must be re-run.
    loop_dir = tmp_path / ".rlcr"
    loop_dir.mkdir()
    kept = {"immutable_sha": "abc", "current_round": 3}
    assert gates.stale_resume(kept, loop_dir, tmp_path / "goal-tracker.md") is True


def test_stale_resume_legit_resume_is_not_stale(tmp_path: Path) -> None:
    # A real crash-resume: tracker and journal both exist.
    loop_dir = tmp_path / ".rlcr"
    loop_dir.mkdir()
    (loop_dir / "journal.jsonl").write_text("{}\n", encoding="utf-8")
    tracker = tmp_path / "goal-tracker.md"
    tracker.write_text(TRACKER, encoding="utf-8")
    kept = {"immutable_sha": "abc", "current_round": 3}
    assert gates.stale_resume(kept, loop_dir, tracker) is False


def test_stale_resume_tampered_workspace_is_not_stale(tmp_path: Path) -> None:
    # Tracker deleted mid-run while the journal survives: that is builder
    # tampering for the pre-gates to refuse, not a stale resume to reset.
    loop_dir = tmp_path / ".rlcr"
    loop_dir.mkdir()
    (loop_dir / "journal.jsonl").write_text("{}\n", encoding="utf-8")
    kept = {"immutable_sha": "abc", "current_round": 3}
    assert gates.stale_resume(kept, loop_dir, tmp_path / "goal-tracker.md") is False
