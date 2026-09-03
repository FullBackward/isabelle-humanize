"""Unit tests for the flow's fail-closed reviewer path (_review).

The reviewer is schema-asked; a verdict that will not parse is re-asked once,
then the round counts as STALLED with the "no parseable verdict" issue --
never trusted prose. These tests drive _review with a stand-in reviewer agent
(no live agent, no gym, no Isabelle).

Importing the flow module needs hmz.flows (the @flow decorator and the
Agent/Person types); where hmz is not installed this module skips, like
tests/test_template_render.py.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

pytest.importorskip("hmz.flows", reason="flow module needs hmz.flows")

from schemas import Readiness, RLCRConfig, Verdict  # noqa: E402

FLOW_INIT = (
    Path(__file__).resolve().parent.parent / "flows" / "isabelle_rlcr" / "__init__.py"
)


@pytest.fixture(scope="module")
def flow_mod():
    spec = importlib.util.spec_from_file_location("isabelle_rlcr_flow_review", FLOW_INIT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StandInReviewer:
    """A reviewer agent: callable like hmz's Agent, answers from a queue."""

    def __init__(self, answers: list[Optional[Verdict]]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(
        self, prompt: str, suppress: bool = True, schema: Any = None
    ) -> Optional[Verdict]:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else None


def _review(
    flow_mod,
    tmp_path: Path,
    reviewer: StandInReviewer,
    round_no: int = 0,
    cfg: Optional[RLCRConfig] = None,
    override: Optional[str] = None,
):
    loop_dir = tmp_path / ".rlcr"
    loop_dir.mkdir()
    agents = SimpleNamespace(reviewer=reviewer)  # Roles stand-in
    cfg = cfg or RLCRConfig()
    spec = SimpleNamespace(target_theorem="smoke")
    tracker = tmp_path / "goal-tracker.md"
    ready = Readiness(success=True, proof_open=True, used_sorry=False, goals=["g1"])
    result = flow_mod._review(
        agents, cfg, tmp_path, tracker, spec, round_no, [], loop_dir, ready,
        classification_override=override,
    )
    journal = [
        json.loads(line)
        for line in (loop_dir / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    return result, journal, loop_dir


# ---------------------------------------------------------- malformed verdicts


def test_unparseable_verdict_twice_counts_stalled(flow_mod, tmp_path: Path) -> None:
    reviewer = StandInReviewer([None, None])
    (verdict, classification, agreement), journal, _ = _review(
        flow_mod, tmp_path, reviewer
    )

    # Re-asked exactly once, then fail-closed.
    assert len(reviewer.prompts) == 2
    assert verdict.mainline == "STALLED"
    assert any("no parseable verdict" in issue for issue in verdict.issues)
    # Empty observed goal sets -> no objective signal -> no agreement.
    assert classification == "UNKNOWN"
    assert agreement is None

    kinds = [record["kind"] for record in journal]
    assert kinds.count("verdict_malformed") == 1
    malformed = journal[kinds.index("verdict_malformed")]
    assert malformed["action"] == "counted_stalled"
    assert malformed["round"] == 0
    # The STALLED verdict is still journaled as the round's verdict.
    verdicts = [record for record in journal if record["kind"] == "verdict"]
    assert len(verdicts) == 1
    assert verdicts[0]["mainline"] == "STALLED"


def test_unparseable_then_valid_verdict_passes(flow_mod, tmp_path: Path) -> None:
    good = Verdict(
        mainline="ADVANCED",
        issues=[],
        open_question=None,
        observed_goals_before=["a", "b"],
        observed_goals_after=["a"],
        readiness=Readiness(success=True, proof_open=True, used_sorry=False),
    )
    reviewer = StandInReviewer([None, good])
    (verdict, classification, agreement), journal, _ = _review(
        flow_mod, tmp_path, reviewer
    )

    assert len(reviewer.prompts) == 2
    assert verdict is good
    assert [record["kind"] for record in journal].count("verdict_malformed") == 0


# --------------------------------------------------------------- valid verdict


def test_valid_verdict_returned_through(flow_mod, tmp_path: Path) -> None:
    good = Verdict(
        mainline="ADVANCED",
        issues=["helper lemma still sorry"],
        open_question=None,
        observed_goals_before=["a", "b"],
        observed_goals_after=["a"],
        readiness=Readiness(success=True, proof_open=True, used_sorry=False),
    )
    reviewer = StandInReviewer([good])
    (verdict, classification, agreement), journal, loop_dir = _review(
        flow_mod, tmp_path, reviewer
    )

    # Answered in shape on the first ask: no re-ask.
    assert len(reviewer.prompts) == 1
    assert verdict is good
    assert classification == "PROGRESS"  # one fewer open goal
    assert agreement is True

    verdicts = [record for record in journal if record["kind"] == "verdict"]
    assert len(verdicts) == 1
    assert verdicts[0]["mainline"] == "ADVANCED"
    assert verdicts[0]["issues"] == ["helper lemma still sorry"]
    assert verdicts[0]["objective"] == "PROGRESS"
    assert verdicts[0]["agreement"] is True

    # The round artifacts are written either way.
    assert (loop_dir / "round-0-review-prompt.md").is_file()
    result_text = (loop_dir / "round-0-review-result.md").read_text(encoding="utf-8")
    assert json.loads(result_text)["mainline"] == "ADVANCED"


# ------------------------------------------- flow-side classification & prompts


def test_classification_override_wins_over_reviewer_goals(flow_mod, tmp_path: Path) -> None:
    """The flow-side (REST) classification is the objective signal; the
    reviewer's observed_goals_*, when it reports any, are only the fallback."""
    good = Verdict(
        mainline="ADVANCED",
        issues=[],
        open_question=None,
        observed_goals_before=["a"],  # reviewer says 1 -> 1: would be STAGNATION
        observed_goals_after=["a"],
        readiness=Readiness(success=True, proof_open=True, used_sorry=False),
    )
    reviewer = StandInReviewer([good])
    (verdict, classification, agreement), journal, _ = _review(
        flow_mod, tmp_path, reviewer, override="PROGRESS"
    )
    assert classification == "PROGRESS"  # the override, not the reviewer's read
    assert agreement is True
    verdicts = [record for record in journal if record["kind"] == "verdict"]
    assert verdicts[0]["objective"] == "PROGRESS"


def test_review_prompt_with_mcp_points_at_tools(flow_mod, tmp_path: Path) -> None:
    reviewer = StandInReviewer([None, None])
    _review(flow_mod, tmp_path, reviewer, cfg=RLCRConfig(reviewer_mcp=True))
    prompt = reviewer.prompts[0]
    assert "mcp__isabellegym__isabelle_goal" in prompt
    assert "NO prover tools" not in prompt
    assert "mechanical check" in prompt  # flow-side readiness is always injected


def test_review_prompt_without_mcp_is_git_only(flow_mod, tmp_path: Path) -> None:
    """The dsh (MCP-less) reviewer: no MCP tool names in its prompt."""
    reviewer = StandInReviewer([None, None])
    _review(flow_mod, tmp_path, reviewer, cfg=RLCRConfig(reviewer_mcp=False))
    prompt = reviewer.prompts[0]
    assert "NO prover tools" in prompt
    assert "mcp__isabellegym" not in prompt
    assert "git -C . show" in prompt
