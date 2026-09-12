"""The arbiter and the readiness gate, stdlib-only.

Two calls to the IsabelleGym REST server, both over urllib and nothing else:

- readiness(): the cheap per-round mechanical gate. Acquire a session, load the
  problem file as a document with report=True, read the report
  (success / proof_open / used_sorry), release. This is the same report the LSP
  MCP's isabelle_diagnostic_messages wraps -- the flow never asks an agent
  whether the proof is finished.

- check(): the arbiter, the loop's only termination authority. sorry/oops
  regex, target-theorem presence, then one stateless POST to the bigstep
  endpoint (isabelle build, strict mode). solved = theory_verified.

Request/response shapes are pinned by tests/fixtures/arbiter/ (recorded in M0).
Exceptions never crash the loop: they come back as unsolved with a typed error.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from schemas import Readiness, RLCRConfig, TaskSpec

#: The task-spec header comment the problem file carries:
#:   (* TASK: theorem=problem_smoke imports=Main field=HOL *)
_TASK_COMMENT = re.compile(
    r"\(\*\s*TASK:\s*theorem=(?P<theorem>\S+)\s+imports=(?P<imports>\S+)\s+"
    r"field=(?P<field>\S+)\s*\*\)"
)
#: A looser match that also catches degenerate TASK comments (e.g. an empty
#: imports= written by an older importer). Used to STRIP stale comments on
#: re-import, never to parse them.
_TASK_COMMENT_LOOSE = re.compile(
    r"\(\*\s*TASK:\s*theorem=(?P<theorem>\S+)\s+imports=(?P<imports>\S*)\s*"
    r"field=(?P<field>\S+)\s*\*\)"
)
_THEORY_LINE = re.compile(r"^\s*theory\s+(?P<name>[A-Za-z][A-Za-z0-9_']*)", re.MULTILINE)
#: The theory header, from the theory line to `begin`: imports may follow the
#: theory name on the same line and may span several lines.
_HEADER = re.compile(
    r"^\s*theory\s+[A-Za-z][A-Za-z0-9_']*(?P<body>.*?)^\s*begin\b",
    re.MULTILINE | re.DOTALL,
)
_HEADER_IMPORTS = re.compile(r"\bimports\b(?P<imports>.*?)(?:\bkeywords\b|\Z)", re.DOTALL)
_SORRY = re.compile(r"\b(sorry|oops)\b")


class ArbiterError(RuntimeError):
    """A typed arbiter/readiness failure -- journaled, never raised through the loop."""


def _parse_imports(text: str) -> list[str]:
    """The theory's imports, from its header: one-line or multi-line lists,
    quoted or bare session-qualified names. "" when there is no header."""
    header = _HEADER.search(text)
    if header is None:
        return []
    found = _HEADER_IMPORTS.search(header.group("body"))
    if found is None:
        return []
    return [tok.strip('"') for tok in found.group("imports").split()]


def parse_spec(text: str, fallback_field: str) -> TaskSpec:
    """Reads the task spec out of the problem file's header comment.

    The theory name comes from the `theory` line (it must match the file name,
    which Isabelle enforces anyway); theorem/imports/field come from the TASK
    comment, with the theory header's imports and the config field as fallbacks.
    """
    theory = _THEORY_LINE.search(text)
    if theory is None:
        raise ArbiterError("no `theory <Name>` line in the problem file")
    comment = _TASK_COMMENT.search(text)
    if comment is not None:
        imports = [one for one in comment.group("imports").split(",") if one]
        return TaskSpec(
            theory_name=theory.group("name"),
            target_theorem=comment.group("theorem"),
            imports=imports,
            field=comment.group("field"),
        )
    imports = _parse_imports(text)
    # Without a TASK comment the target theorem is the first lemma/theorem name.
    named = re.search(r"^\s*(?:lemma|theorem)\s+([A-Za-z][A-Za-z0-9_']*)", text, re.MULTILINE)
    if named is None:
        raise ArbiterError("no TASK comment and no lemma/theorem in the problem file")
    return TaskSpec(
        theory_name=theory.group("name"),
        target_theorem=named.group(1),
        imports=imports,
        field=fallback_field,
    )


def _request(
    method: str,
    url: str,
    payload: Optional[dict[str, Any]],
    timeout: float,
    lease_id: Optional[str] = None,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if lease_id:
        headers["X-Lease-Id"] = lease_id
    request = urllib.request.Request(  # noqa: S310 -- loopback gym server, by design
        url, data=body, method=method, headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return json.load(response)
    except urllib.error.HTTPError as refused:
        detail = refused.read().decode(errors="replace")[:500]
        raise ArbiterError(f"{method} {url}: HTTP {refused.code}: {detail}") from refused
    except (OSError, json.JSONDecodeError) as failed:
        raise ArbiterError(f"{method} {url}: {failed}") from failed


def readiness(workspace: Path, config: RLCRConfig, spec: TaskSpec) -> Readiness:
    """The mechanical gate: does the file on disk compile, is the proof open,
    did it use sorry -- answered by the prover, over REST, in one document load."""
    base = config.gym_url.rstrip("/")
    text = (workspace / config.problem_file).read_text(encoding="utf-8")
    # Theories come from the file AS IT STANDS: acquiring a session pays
    # parent resolution for whatever it is asked for, and a stale spec import
    # (e.g. HOL-Analysis.* the builder has since dropped) costs minutes and
    # can time the acquire out. Only the current header is truthful.
    current_imports = _parse_imports(text) or spec.imports
    session_id: Optional[str] = None
    lease_id: Optional[str] = None
    try:
        acquired = _request(
            "POST",
            f"{base}/api/v1/sessions/acquire",
            # theories: this gym's REPL sessions only see theories from their
            # own heap-ancestor chain -- a session acquired WITHOUT theories
            # cannot load anything beyond Main (probe: "Undefined type name
            # real" on Complex_Main imports, 2026-09-03). Passing the file's
            # imports makes the server enter them properly (and reuses warm
            # theory-keyed sessions).
            {"reuse_dirty": True, "field": spec.field, "theories": current_imports},
            config.readiness_timeout_s,
        )
        session_id = acquired.get("session_id") or acquired.get("id")
        lease_id = acquired.get("lease_id")
        if not session_id:
            raise ArbiterError(f"acquire returned no session id: {acquired}")
        loaded = _request(
            "PUT",
            f"{base}/api/v1/sessions/{session_id}/document",
            {
                # Full .thy source including its own header: the server derives
                # the theory name from it. Passing imports/thy_name too would
                # prepend a second header ("Bad context for command theory").
                "text": text,
                "timeout": config.readiness_timeout_s,
                "report": True,
            },
            config.readiness_timeout_s + 60.0,
            lease_id=lease_id,
        )
        report = loaded.get("report") or {}
        goals: list[str] = []
        try:
            sub = _request(
                "GET",
                f"{base}/api/v1/sessions/{session_id}/subgoals",
                None,
                60.0,
                lease_id=lease_id,
            )
            goals = [str(one) for one in (sub.get("subgoals") or [])]
        except ArbiterError:
            pass  # goals are the progress signal, not the gate; never fatal
        return Readiness(
            success=bool(report.get("success", loaded.get("success"))),
            proof_open=bool(report.get("proof_open", False)),
            used_sorry=bool(report.get("used_sorry", False)),
            error=loaded.get("error"),
            goals=goals,
        )
    finally:
        if session_id:
            try:
                _request(
                    "POST",
                    f"{base}/api/v1/sessions/{session_id}/release",
                    {},
                    30.0,
                    lease_id=lease_id,
                )
            except ArbiterError:
                pass  # a session left behind is reaped by the server's lease reaper


def parent_session(spec: TaskSpec) -> str:
    """The build's parent session, derived from dotted imports.

    A dotted import (`HOL-Analysis.Derivative`) needs its session prefix as
    the build's parent (`HOL-Analysis`): with parent HOL the build server
    cannot resolve it ("Cannot load theory", observed live 2026-09-04).
    The first dotted import wins; otherwise the spec's field stands.
    """
    for one in spec.imports:
        if "." in one:
            return one.split(".", 1)[0]
    return spec.field


def check(workspace: Path, config: RLCRConfig, spec: TaskSpec) -> dict[str, Any]:
    """The arbiter: isabelle build, strict mode, target theorem present, no sorry.

    Returns a verdict dict; `solved` is the only field the loop reads. Nothing
    here raises: every failure is an unsolved verdict with a typed reason.
    """
    text = (workspace / config.problem_file).read_text(encoding="utf-8")
    if _SORRY.search(text):
        return {"solved": False, "stage": "regex", "reason": "sorry/oops in problem file"}
    if not re.search(rf"\b(?:lemma|theorem)\s+{re.escape(spec.target_theorem)}\b", text):
        return {
            "solved": False,
            "stage": "presence",
            "reason": f"target theorem {spec.target_theorem} not present",
        }
    # The build's parent session comes from the file's own imports (parsed
    # locally); `dependencies` stays EMPTY so the server extracts them from
    # the theory text itself (build_verify.py:120 -- also the M0-recorded
    # request shape). The builder may legitimately change imports while
    # proving; only the file's own header can keep the build aligned with
    # the text being built. The target theorem still comes from the spec,
    # so renaming/weakening it is caught above.
    theory = _THEORY_LINE.search(text)
    theory_name = theory.group("name") if theory else spec.theory_name
    current_imports = _parse_imports(text)
    # The original imports are part of the problem: a builder may ADD
    # libraries but never REMOVE one -- dropping an import and re-implementing
    # the machinery locally is dodging the problem, not solving it (observed
    # live on putnam_2023_a1, which shed HOL-Analysis.Derivative).
    removed = [one for one in spec.imports if one not in current_imports]
    if removed:
        return {
            "solved": False,
            "stage": "imports",
            "reason": (
                f"original imports removed from the theory: {', '.join(removed)}. "
                "The statement's imports are part of the problem -- restore them "
                "(you may add libraries, never remove them)."
            ),
        }
    build_spec = TaskSpec(
        theory_name=theory_name,
        target_theorem=spec.target_theorem,
        imports=current_imports,
        field=spec.field,
    )
    base = config.gym_url.rstrip("/")
    try:
        answered = _request(
            "POST",
            f"{base}/api/v1/sessions/bigstep",
            {
                "theory_name": theory_name,
                "theory": text,
                "dependencies": [],
                "field": parent_session(build_spec),
                "timeout": config.arbiter_timeout_s,
            },
            config.arbiter_timeout_s + 60.0,
        )
    except ArbiterError as failed:
        return {"solved": False, "stage": "transport", "reason": str(failed)}
    solved = bool(answered.get("theory_verified"))
    verdict: dict[str, Any] = {
        "solved": solved,
        "stage": "build",
        "theory_verified": solved,
        "mode": answered.get("mode"),
        "execution_time": answered.get("execution_time"),
    }
    if not solved:
        verdict["reason"] = (answered.get("error") or answered.get("output") or "")[:2000]
    return verdict
