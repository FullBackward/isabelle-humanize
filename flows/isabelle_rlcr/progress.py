"""Objective proof-progress classification (implementation-plan.md §5.5).

Every round is classified from prover-observable signals -- the goal sets the
reviewer reports from its MCP queries -- and the classification is logged
alongside the reviewer's verdict. Their agreement rate is the research metric:
what does LLM review add when the prover is ground truth?

The classifier never trusts prose: empty observations are UNKNOWN, not
PROGRESS. The drift state machine uses the reviewer verdict by default (v1
parity); RLCRConfig.drift_source switches it as a one-line independent variable.
"""

from __future__ import annotations

import re
from typing import Literal, Optional

Classification = Literal["PROGRESS", "STAGNATION", "REGRESSION", "UNKNOWN"]

#: Connectives that make a goal text harder: more of them, more work left.
_HARDER = re.compile(r"[⋀⟹∧⟶∀∃λ]")


def _shape(goals: list[str]) -> int:
    """How hard a goal set looks: total connective weight across the goals."""
    return sum(len(_HARDER.findall(one)) for one in goals)


def classify(
    before: Optional[list[str]],
    after: Optional[list[str]],
    *,
    new_helper_lemmas: int = 0,
) -> Classification:
    """Classify one round from the goal sets before and after it.

    PROGRESS: fewer open goals, or the same count strictly simpler, or new
    discharged helper lemmas. STAGNATION: an identical goal set. REGRESSION:
    more open goals than before. UNKNOWN: no trustworthy observation.
    """
    if before is None or after is None or not before:
        return "UNKNOWN"
    if len(after) < len(before):
        return "PROGRESS"
    if len(after) > len(before):
        return "REGRESSION"
    if [one.strip() for one in after] == [one.strip() for one in before]:
        return "PROGRESS" if new_helper_lemmas > 0 else "STAGNATION"
    if _shape(after) < _shape(before):
        return "PROGRESS"
    if _shape(after) > _shape(before):
        return "REGRESSION"
    return "PROGRESS" if new_helper_lemmas > 0 else "STAGNATION"


def agrees(classification: Classification, verdict: str) -> Optional[bool]:
    """Whether the objective classification and the reviewer verdict agree.

    None where the classification says nothing (UNKNOWN): the agreement rate is
    computed over rounds where both spoke.
    """
    if classification == "UNKNOWN":
        return None
    return {
        ("PROGRESS", "ADVANCED"): True,
        ("STAGNATION", "STALLED"): True,
        ("REGRESSION", "REGRESSED"): True,
    }.get((classification, verdict), False)
