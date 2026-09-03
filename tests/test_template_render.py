"""Unit tests for the flow module's _render: {{VAR}} single-pass substitution.

Importing flows/isabelle_rlcr/__init__.py needs hmz.flows (the @flow
decorator and the Agent/Person types). When hmz is not installed -- e.g. the
plain test venv -- this module skips; the gates/arbiter suites do not depend
on hmz.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("hmz.flows", reason="flow module needs hmz.flows")

FLOW_INIT = (
    Path(__file__).resolve().parent.parent / "flows" / "isabelle_rlcr" / "__init__.py"
)


@pytest.fixture(scope="module")
def flow_mod():
    spec = importlib.util.spec_from_file_location("isabelle_rlcr_flow", FLOW_INIT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_render_substitutes(flow_mod) -> None:
    assert flow_mod._render("a {{X}} b", X="one") == "a one b"


def test_render_injected_content_not_reexpanded(flow_mod) -> None:
    # Single pass: the injected "{{Y}}" is output verbatim, never re-expanded.
    assert flow_mod._render("a {{X}}", X="{{Y}}") == "a {{Y}}"


def test_render_unknown_vars_left_as_is(flow_mod) -> None:
    assert flow_mod._render("a {{MISSING}} b") == "a {{MISSING}} b"
    assert flow_mod._render("{{X}} {{MISSING}}", X="one") == "one {{MISSING}}"


def test_render_multiple_occurrences(flow_mod) -> None:
    assert flow_mod._render("{{X}}/{{X}}", X="v") == "v/v"
