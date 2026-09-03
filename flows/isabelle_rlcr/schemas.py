"""Typed shapes for the isabelle_rlcr flow.

RLCRConfig is what `-c setup.yaml` sets (the TUI renders its settings menu from
it). TaskSpec is the one source of truth about the problem, parsed from the
problem file's header comment. Readiness is the mechanical gate result, taken
from the gym's REST document report -- never from agent prose.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class RLCRConfig(BaseModel):
    """What a run of the loop can be set up with (v1's flags, Isabelle-adjusted)."""

    model_config = {"extra": "forbid"}

    max_iterations: int = Field(default=42, ge=0, description="--max: rounds before stop")
    full_review_round: int = Field(
        default=5, ge=2, description="rounds between full-alignment reviews"
    )
    drift_stall_replan: int = Field(
        default=2, ge=1, description="consecutive stalls before a replan prompt"
    )
    drift_stall_breaker: int = Field(
        default=3, ge=1, description="consecutive stalls before the circuit breaker"
    )
    arbiter_timeout_s: int = Field(
        default=900, ge=1, description="budget for one isabelle build arbiter call"
    )
    builder_timeout_s: int = Field(
        default=600, ge=0, description="advisory per-turn budget for the builder (M1)"
    )
    reviewer_timeout_s: int = Field(
        default=300, ge=0, description="advisory per-turn budget for the reviewer (M2)"
    )
    readiness_timeout_s: int = Field(
        default=180, ge=1, description="budget for one REST readiness document load"
    )
    bitlesson_required: bool = Field(
        default=True, description="every round summary carries a lesson delta (M3)"
    )
    require_lesson_entry_for_none: bool = Field(
        default=False,
        description="a round may not report Action: none while the KB is empty",
    )
    ask_open_questions: bool = Field(
        default=True, description="reviewer open questions escalate to the human (M2)"
    )
    push_every_round: bool = Field(default=False, description="push after every round")
    privacy: bool = Field(
        default=False, description="no methodology analysis when the loop exits"
    )
    finalize_simplify: bool = Field(
        default=True,
        description="one sorry-free simplification pass before completion is declared",
    )
    drift_source: Literal["reviewer", "objective", "either"] = Field(
        default="reviewer",
        description="which signal drives the drift state machine (ablation flag)",
    )
    solo: bool = Field(
        default=False,
        description="single-agent ablation arm: no reviewer turn, no drift machine, "
        "no escalation -- mechanical gates and the arbiter only",
    )
    reviewer_mcp: bool = Field(
        default=True,
        description="the reviewer attaches the LSP MCP and can query the prover "
        "itself; False for MCP-less backends (dsh): the review prompt drops MCP "
        "instructions and the flow supplies prover state over REST",
    )
    gym_url: str = Field(
        default="http://localhost:8000", description="IsabelleGym server base URL"
    )
    field: str = Field(default="HOL", description="default Isabelle session field")
    problem_file: str = Field(
        default="problem.thy", description="theory file, relative to the workspace"
    )
    model_builder: str = Field(default="", description="recorded only, for the cycle")
    model_reviewer: str = Field(default="", description="recorded only, for the cycle")


class TaskSpec(BaseModel):
    """The problem, parsed from the problem file itself (one source of truth)."""

    theory_name: str
    target_theorem: str
    imports: list[str]
    field: str


class Readiness(BaseModel):
    """The mechanical gate: what the prover says about the file on disk.

    Taken from the gym's REST load_document report (the same report the LSP
    MCP's isabelle_diagnostic_messages wraps), so the flow needs no MCP client
    of its own and no agent is believed about readiness. `goals` is the open
    subgoal list from the same session's /subgoals endpoint -- the flow-side
    objective progress signal; empty when the fetch fails (never fatal).
    """

    success: bool
    proof_open: bool
    used_sorry: bool
    error: Optional[str] = None
    goals: list[str] = Field(default_factory=list)

    @property
    def proof_finished(self) -> bool:
        return self.success and not self.proof_open

    @property
    def sorry_free(self) -> bool:
        return not self.used_sorry


class LessonSelection(BaseModel):
    """The lesson selector's answer: which KB entries matter this round."""

    model_config = {"extra": "forbid"}

    lesson_ids: list[str] = Field(
        default_factory=list,
        description="at most 3 IDs from the offered KB index; empty when none apply",
    )


class Verdict(BaseModel):
    """The reviewer's structured verdict (M2). Declared now so gates/journal
    shapes do not move when the reviewer lands."""

    model_config = {"extra": "forbid"}

    mainline: Literal["ADVANCED", "STALLED", "REGRESSED"]
    issues: list[str] = Field(default_factory=list)
    open_question: Optional[str] = None
    observed_goals_before: list[str] = Field(default_factory=list)
    observed_goals_after: list[str] = Field(default_factory=list)
    readiness: Readiness
