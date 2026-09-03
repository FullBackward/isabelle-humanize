"""The pre-round gates: v1's Stop-hook gate pipeline, re-expressed in Python.

Every gate is fail-closed and answers one of two things: None (pass) or a
refusal string (what the builder hears). A gate that cannot tell fails closed
-- it refuses, or reports corruption, which the loop ends `unexpected`.

M1 subset (per implementation-plan.md §5.2, with the deferrals noted):
- git repo present (the loop anchors rounds to commits)
- branch consistency (v1 start_branch)
- goal-tracker IMMUTABLE section byte-preserved (sha256, recorded at setup)
- workspace clean except the problem file and the loop's own artifacts
- large-file block (theory files rot too)
- current_round < max_iterations

Deferred: round-contract presence (M2), lesson-delta validation (M3,
bitlesson-validate-delta.sh port), goal-tracker placeholder scan (M2).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Optional

#: Untracked/modified paths that do not make the tree dirty: the loop's own
#: artifacts, the problem file, the goal tracker (its MUTABLE section), the
#: lessons KB (M3), and CLI config dirs.
_ALLOWED = re.compile(
    r"^(.. )?(\"?)(\.rlcr/|problem\.thy|goal-tracker\.md|proof-lessons\.md|"
    r"\.mcp\.json|\.kimi-code/|\.qwen/|\.claude/|\.gitignore)"
)

#: The IMMUTABLE section of the goal tracker: from its heading to the next
#: same-level heading. Byte-preserved: any change fails the gate.
_IMMUTABLE = re.compile(r"(^## IMMUTABLE\b.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)

#: How long a theory file may get before the loop makes the builder split it.
MAX_LINES = 2000

#: Git commands are given this long, in seconds (v1's GIT_TIMEOUT).
_GIT = 10


class GateCorruption(RuntimeError):
    """A gate found state it cannot trust -- the loop ends `unexpected`."""


def git(*args: str, at: Path) -> tuple[int, str]:
    """One git command in the workspace: (status, output), 124 if it could not run.

    The output is NOT stripped: porcelain formats are column-aligned, and
    stripping eats the leading status column of the first line. Callers that
    read a value (a branch name) strip; callers that read rows (porcelain)
    must see the raw columns.
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(at), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT,
        )
    except (OSError, subprocess.SubprocessError):
        return 124, ""
    return done.returncode, done.stdout


def require_git_repo(root: Path) -> None:
    """The loop commits every round; outside a git repository there is nothing
    to anchor a review to. Raises GateCorruption, not a refusal -- there is no
    builder fix for this."""
    status, _ = git("rev-parse", "--is-inside-work-tree", at=root)
    if status:
        raise GateCorruption(f"{root} is not a git repository")


def branch_consistency(root: Path, start_branch: str) -> Optional[str]:
    """v1: the work stays on the branch the loop started on."""
    status, branch = git("rev-parse", "--abbrev-ref", "HEAD", at=root)
    branch = branch.strip()
    if status or not branch:
        return (
            "Git operation failed or timed out. Cannot verify branch consistency. "
            "Check git status manually and try again."
        )
    if start_branch and branch != start_branch:
        return (
            f"Branch changed: the loop started on {start_branch}, now on {branch}. "
            f"Switch back to {start_branch} and continue."
        )
    return None


def immutable_section(tracker_text: str) -> str:
    """The IMMUTABLE section of the goal tracker, or "" if there is none."""
    found = _IMMUTABLE.search(tracker_text)
    return found.group(1) if found else ""


def goal_tracker_preserved(tracker: Path, expected_sha256: str) -> Optional[str]:
    """v1 plan-integrity, on the goal tracker's IMMUTABLE section: the builder
    may edit the MUTABLE section freely and the IMMUTABLE section never."""
    if not tracker.is_file():
        return f"Goal tracker missing: {tracker}. Restore it and continue."
    held = immutable_section(tracker.read_text(encoding="utf-8"))
    if not held:
        raise GateCorruption(f"{tracker} has no IMMUTABLE section to preserve")
    if hashlib.sha256(held.encode()).hexdigest() != expected_sha256:
        return (
            "Goal-tracker IMMUTABLE section was modified. It is byte-preserved: "
            "restore the exact original text (task spec, theorem name, acceptance "
            "criteria) and keep your edits to the MUTABLE section."
        )
    return None


def workspace_clean(root: Path) -> Optional[str]:
    """v1 git cleanliness: nothing dirty but the problem file and the loop's
    own artifacts (the per-round commit leaves the tree clean by construction)."""
    status, said = git("status", "--porcelain", at=root)
    if status:
        return "Git operation failed or timed out. Cannot verify workspace cleanliness."
    left = [line for line in said.splitlines() if line and not _ALLOWED.match(line[3:])]
    if left:
        return (
            "Workspace has unexpected changes outside the problem file and loop "
            "artifacts:\n" + "\n".join(left[:20]) + "\nCommit or revert them and continue."
        )
    return None


def large_file(path: Path, max_lines: int = MAX_LINES) -> Optional[str]:
    """v1's large-file block: a theory file past the limit gets split, not grown."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return f"Problem file unreadable: {path}"
    if len(lines) > max_lines:
        return (
            f"{path.name} is {len(lines)} lines (limit {max_lines}). Split the "
            "development into helper lemmas or a second theory before continuing."
        )
    return None


def under_max_iterations(current_round: int, max_iterations: int) -> bool:
    """The one gate that ends the loop rather than refusing a round."""
    return current_round < max_iterations


def stale_resume(kept: dict, loop_dir: Path, tracker: Path) -> bool:
    """Resume state claims setup ran, but the workspace has no trace of it.

    hmz keys resumable state on the workspace PATH, so a workspace deleted and
    recreated at the same path (e.g. ablation run copies) resurrects state from
    the previous incarnation: setup would be skipped in a workspace with no
    goal tracker and no journal. Detected exactly when state carries
    immutable_sha but neither the tracker nor the journal exists. A tracker
    missing while the journal DOES exist is mid-run tampering -- the pre-gates
    refuse that round; this helper is not about it.
    """
    if "immutable_sha" not in kept:
        return False
    return not tracker.is_file() and not (loop_dir / "journal.jsonl").is_file()
