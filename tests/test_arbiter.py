"""Unit tests for flows/isabelle_rlcr/arbiter.py -- parse_spec, check, readiness.

No live gym server: every HTTP path is tested by monkeypatching
arbiter._request, and the bigstep request/response shapes are pinned against
the M0-recorded fixtures in tests/fixtures/arbiter/.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pytest

import arbiter
from schemas import RLCRConfig, TaskSpec

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "arbiter"

TASK_FILE = """(* TASK: theorem=smoke imports=Main field=HOL *)
theory Smoke
imports Main
begin

lemma smoke: "True" by simp

end
"""


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _workspace(tmp_path: Path, text: str) -> Path:
    (tmp_path / "problem.thy").write_text(text, encoding="utf-8")
    return tmp_path


# ------------------------------------------------------------------- parse_spec


def test_parse_spec_task_comment() -> None:
    spec = arbiter.parse_spec(TASK_FILE, fallback_field="HOL-Analysis")
    assert spec.theory_name == "Smoke"
    assert spec.target_theorem == "smoke"
    assert spec.imports == ["Main"]
    assert spec.field == "HOL"


def test_parse_spec_task_comment_comma_imports() -> None:
    text = TASK_FILE.replace("imports=Main", "imports=Main,HOL-Library.Multiset")
    spec = arbiter.parse_spec(text, fallback_field="HOL")
    assert spec.imports == ["Main", "HOL-Library.Multiset"]


def test_parse_spec_fallback_imports_line_and_first_lemma() -> None:
    text = """theory Smoke
imports Main "HOL-Library.Multiset"
begin

lemma first_one: "True" by simp
lemma second_one: "True" by simp

end
"""
    spec = arbiter.parse_spec(text, fallback_field="HOL-Analysis")
    assert spec.theory_name == "Smoke"
    assert spec.target_theorem == "first_one"
    assert spec.imports == ["Main", "HOL-Library.Multiset"]  # quotes stripped
    assert spec.field == "HOL-Analysis"


def test_parse_spec_missing_theory_line_raises() -> None:
    with pytest.raises(arbiter.ArbiterError):
        arbiter.parse_spec("lemma smoke: \"True\" by simp\n", fallback_field="HOL")


def test_parse_spec_no_task_comment_and_no_lemma_raises() -> None:
    with pytest.raises(arbiter.ArbiterError):
        arbiter.parse_spec("theory Smoke\nimports Main\nbegin\nend\n", "HOL")


# ------------------------------------------------------------------------ check


def _spec() -> TaskSpec:
    return TaskSpec(
        theory_name="Smoke", target_theorem="smoke", imports=["Main"], field="HOL"
    )


def _fail_if_http(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("_request must not be called on this path")


def test_check_sorry_regex_no_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(arbiter, "_request", _fail_if_http)
    workspace = _workspace(tmp_path, TASK_FILE.replace("by simp", "sorry"))
    verdict = arbiter.check(workspace, RLCRConfig(), _spec())
    assert verdict["solved"] is False
    assert verdict["stage"] == "regex"


def test_check_theorem_absent_no_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(arbiter, "_request", _fail_if_http)
    workspace = _workspace(tmp_path, TASK_FILE)
    spec = TaskSpec(
        theory_name="Smoke", target_theorem="not_there", imports=["Main"], field="HOL"
    )
    verdict = arbiter.check(workspace, RLCRConfig(), spec)
    assert verdict["solved"] is False
    assert verdict["stage"] == "presence"
    assert "not_there" in verdict["reason"]


def _capture_request(
    monkeypatch: pytest.MonkeyPatch, response: Any
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake(
        method: str,
        url: str,
        payload: Optional[dict[str, Any]],
        timeout: float,
        lease_id: Optional[str] = None,
    ) -> Any:
        calls.append(
            {
                "method": method,
                "url": url,
                "payload": payload,
                "timeout": timeout,
                "lease_id": lease_id,
            }
        )
        return response

    monkeypatch.setattr(arbiter, "_request", fake)
    return calls


def test_check_build_solved_matches_recorded_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded_request = _fixture("request_good.json")
    workspace = _workspace(tmp_path, recorded_request["theory"])
    calls = _capture_request(monkeypatch, _fixture("response_good.json"))
    config = RLCRConfig(arbiter_timeout_s=recorded_request["timeout"])
    spec = TaskSpec(
        theory_name=recorded_request["theory_name"],
        target_theorem="arbiter_smoke",
        imports=recorded_request["dependencies"],
        field=recorded_request["field"],
    )
    verdict = arbiter.check(workspace, config, spec)
    assert verdict["solved"] is True
    assert verdict["stage"] == "build"
    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/v1/sessions/bigstep")
    assert set(call["payload"]) == {
        "theory_name",
        "theory",
        "dependencies",
        "field",
        "timeout",
    }
    assert call["payload"] == recorded_request


def test_check_build_unsolved_carries_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded_request = _fixture("request_good.json")
    workspace = _workspace(tmp_path, recorded_request["theory"])
    _capture_request(monkeypatch, _fixture("response_sorry.json"))
    spec = TaskSpec(
        theory_name=recorded_request["theory_name"],
        target_theorem="arbiter_smoke",
        imports=recorded_request["dependencies"],
        field=recorded_request["field"],
    )
    verdict = arbiter.check(workspace, RLCRConfig(), spec)
    assert verdict["solved"] is False
    assert verdict["stage"] == "build"
    assert verdict["theory_verified"] is False
    assert "reason" in verdict
    assert "Cheating" in verdict["reason"]


def test_check_transport_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise arbiter.ArbiterError("connection refused")

    monkeypatch.setattr(arbiter, "_request", boom)
    workspace = _workspace(tmp_path, TASK_FILE)
    verdict = arbiter.check(workspace, RLCRConfig(), _spec())
    assert verdict["solved"] is False
    assert verdict["stage"] == "transport"
    assert "connection refused" in verdict["reason"]


# -------------------------------------------------------------------- readiness


def test_readiness_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _workspace(tmp_path, TASK_FILE)
    calls: list[dict[str, Any]] = []

    def fake(
        method: str,
        url: str,
        payload: Optional[dict[str, Any]],
        timeout: float,
        lease_id: Optional[str] = None,
    ) -> Any:
        calls.append(
            {
                "method": method,
                "url": url,
                "payload": payload,
                "timeout": timeout,
                "lease_id": lease_id,
            }
        )
        if url.endswith("/sessions/acquire"):
            return {"session_id": "s1", "lease_id": "l1"}
        if url.endswith("/s1/document"):
            return {"report": {"success": True, "proof_open": False, "used_sorry": True}}
        if url.endswith("/s1/subgoals"):
            return {"subgoals": ["x + 0 = x"], "count": 1, "proof_finished": False}
        if url.endswith("/s1/release"):
            return {}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(arbiter, "_request", fake)
    ready = arbiter.readiness(workspace, RLCRConfig(), _spec())

    assert ready.success is True
    assert ready.proof_open is False
    assert ready.used_sorry is True
    assert ready.proof_finished is True
    assert ready.sorry_free is False
    assert ready.goals == ["x + 0 = x"]

    acquire, document, subgoals, release = calls
    assert subgoals["method"] == "GET"
    assert subgoals["lease_id"] == "l1"
    assert acquire["method"] == "POST"
    assert acquire["payload"] == {"reuse_dirty": True, "field": "HOL"}

    assert document["method"] == "PUT"
    assert document["payload"]["text"] == TASK_FILE  # full source, header included
    assert "thy_name" not in document["payload"]
    assert "imports" not in document["payload"]
    assert document["payload"]["report"] is True
    assert document["lease_id"] == "l1"  # the lease header was passed

    assert release["method"] == "POST"
    assert release["url"].endswith("/api/v1/sessions/s1/release")
    assert release["lease_id"] == "l1"


def test_readiness_subgoals_failure_is_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A /subgoals failure must not break the gate: goals=[] and carry on."""
    workspace = _workspace(tmp_path, TASK_FILE)

    def fake(
        method: str,
        url: str,
        payload: Optional[dict[str, Any]],
        timeout: float,
        lease_id: Optional[str] = None,
    ) -> Any:
        if url.endswith("/sessions/acquire"):
            return {"session_id": "s1", "lease_id": "l1"}
        if url.endswith("/s1/document"):
            return {"report": {"success": True, "proof_open": True, "used_sorry": False}}
        if url.endswith("/s1/subgoals"):
            raise arbiter.ArbiterError("HTTP 500: pool exhausted")
        if url.endswith("/s1/release"):
            return {}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(arbiter, "_request", fake)
    ready = arbiter.readiness(workspace, RLCRConfig(), _spec())
    assert ready.success is True
    assert ready.goals == []
