"""Unit tests for flows/isabelle_rlcr/progress.py -- the objective classifier.

Pure functions, no fixtures. The classifier never trusts prose: empty
observations are UNKNOWN, and agreement is only defined where both the
classifier and the reviewer spoke.
"""

from __future__ import annotations

import progress

# -------------------------------------------------------------------- classify


def test_classify_fewer_goals_progress() -> None:
    assert progress.classify(["a", "b"], ["a"]) == "PROGRESS"
    assert progress.classify(["a"], []) == "PROGRESS"


def test_classify_more_goals_regression() -> None:
    assert progress.classify(["a"], ["a", "b"]) == "REGRESSION"


def test_classify_identical_stagnation() -> None:
    assert progress.classify(["a", "b"], ["a", "b"]) == "STAGNATION"


def test_classify_identical_ignores_whitespace() -> None:
    assert progress.classify(["  a  "], ["a"]) == "STAGNATION"


def test_classify_same_count_simpler_progress() -> None:
    before = ["∀x. P x ∧ Q x ⟹ R x"]
    after = ["P x"]
    assert progress.classify(before, after) == "PROGRESS"


def test_classify_same_count_harder_regression() -> None:
    before = ["P x"]
    after = ["∀x. P x ∧ Q x ⟹ R x"]
    assert progress.classify(before, after) == "REGRESSION"


def test_classify_same_count_same_shape_stagnation() -> None:
    before = ["P x ∧ Q x"]
    after = ["R y ∧ S y"]
    assert progress.classify(before, after) == "STAGNATION"


def test_classify_none_before_unknown() -> None:
    assert progress.classify(None, ["a"]) == "UNKNOWN"


def test_classify_none_after_unknown() -> None:
    assert progress.classify(["a"], None) == "UNKNOWN"


def test_classify_empty_before_unknown() -> None:
    # No trustworthy observation of where the round started.
    assert progress.classify([], ["a"]) == "UNKNOWN"


def test_classify_helper_lemmas_flip_stagnation_to_progress() -> None:
    assert progress.classify(["a"], ["a"], new_helper_lemmas=1) == "PROGRESS"
    assert progress.classify(["a"], ["a"], new_helper_lemmas=0) == "STAGNATION"


def test_classify_helper_lemmas_do_not_mask_regression() -> None:
    assert progress.classify(["a"], ["a", "b"], new_helper_lemmas=2) == "REGRESSION"


# ---------------------------------------------------------------------- agrees


def test_agrees_mapped_pairs() -> None:
    assert progress.agrees("PROGRESS", "ADVANCED") is True
    assert progress.agrees("STAGNATION", "STALLED") is True
    assert progress.agrees("REGRESSION", "REGRESSED") is True


def test_agrees_divergence_false() -> None:
    assert progress.agrees("PROGRESS", "STALLED") is False
    assert progress.agrees("PROGRESS", "REGRESSED") is False
    assert progress.agrees("STAGNATION", "ADVANCED") is False
    assert progress.agrees("REGRESSION", "ADVANCED") is False


def test_agrees_unknown_none() -> None:
    assert progress.agrees("UNKNOWN", "ADVANCED") is None
    assert progress.agrees("UNKNOWN", "STALLED") is None
    assert progress.agrees("UNKNOWN", "REGRESSED") is None
