"""Proof memory: the proof-lessons KB and its per-round delta gate (§5.6).

The KB (`proof-lessons.md` in the workspace) holds what the loop has learned
about proving things in this project, one entry per lesson:

    ### BL-<yyyymmdd>-<name>
    - Scope: ...
    - Goal Shape: ...
    - Failing Approach: ...
    - Working Approach: ...
    - Evidence Round: N

Every round summary must close with a `## Lesson Delta` block -- the direct
port of v1's bitlesson-validate-delta.sh:

    ## Lesson Delta
    Action: none|add|update
    Lessons: BL-20260901-simp-before-auto, ...   (required for add/update)
    Notes: <what changed and why>                (required for add/update)

The gate is fail-closed: a summary with no block, a bad action, missing IDs,
IDs not in the KB, or placeholder notes refuses the round. The selector
short-circuits an empty KB to NONE without an LLM call, exactly v1's
bitlesson-select.sh.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

#: The delta block heading, and its three fields.
_DELTA = re.compile(r"^## Lesson Delta\s*$", re.MULTILINE)
_ACTION = re.compile(r"^Action:\s*(\S+)", re.MULTILINE)
_LESSONS = re.compile(r"^Lessons:\s*(.+)$", re.MULTILINE)
_NOTES = re.compile(r"^Notes:\s*(.+)$", re.MULTILINE)

#: A lesson ID as the KB declares one (`### BL-...`), and as a delta names one.
_LESSON_ID = re.compile(r"^### (BL-\d{8}-[A-Za-z0-9_-]+)\s*$", re.MULTILINE)
_ID_SHAPE = re.compile(r"^BL-\d{8}-[A-Za-z0-9_-]+$")

#: What a placeholder Notes line looks like (v1's unwritten markers).
_UNWRITTEN = re.compile(
    r"^\s*(none|n/?a|todo|tbd|\.|-|--|\(none\))\s*$", re.IGNORECASE
)

#: The KB file starts as this; the empty-KB short-circuit reads it too.
KB_TEMPLATE = """# Proof Lessons

Lessons this project has learned about what works and what fails in its
proofs. One entry per lesson:

    ### BL-<yyyymmdd>-<name>
    - Scope: where this applies (theory, goal shape, tactic family)
    - Goal Shape: what the goal looked like
    - Failing Approach: what did NOT work, with evidence
    - Working Approach: what worked, with evidence
    - Evidence Round: the round that taught it
"""


def lesson_ids(kb_text: str) -> list[str]:
    """Every lesson ID the KB declares."""
    return _LESSON_ID.findall(kb_text)


def entries(kb_text: str, ids: list[str]) -> dict[str, str]:
    """The full text of the named lessons, by ID (heading to next heading)."""
    wanted: dict[str, str] = {}
    marks = list(_LESSON_ID.finditer(kb_text))
    for at, mark in enumerate(marks):
        if mark.group(1) not in ids:
            continue
        end = marks[at + 1].start() if at + 1 < len(marks) else len(kb_text)
        wanted[mark.group(1)] = kb_text[mark.start() : end].strip()
    return wanted


def delta_block(summary: str) -> Optional[str]:
    """The `## Lesson Delta` section of a summary, fence/comment-aware.

    The block runs from its heading to the next `## ` heading. A heading that
    appears inside a fenced code block does not count -- builders quote their
    summaries, and a quoted heading is not the section.
    """
    text = re.sub(r"```.*?```", lambda m: "\n" * m.group(0).count("\n"), summary, flags=re.DOTALL)
    found = _DELTA.search(text)
    if found is None:
        return None
    rest = text[found.end():]
    end = re.search(r"^## ", rest, re.MULTILINE)
    return rest[: end.start()] if end else rest


def validate_delta(summary: str, kb: Path, *, allow_empty_none: bool = True) -> Optional[str]:
    """Validate a round summary's lesson delta against the KB.

    Returns None for a delta in order, or a refusal string (fail-closed).
    """
    block = delta_block(summary)
    if block is None:
        return (
            "Round summary is missing its `## Lesson Delta` section. Every round "
            "closes with Action: none|add|update (+ Lessons/Notes for add|update)."
        )
    action = _ACTION.search(block)
    named = action.group(1).lower() if action else ""
    if named not in ("none", "add", "update"):
        return "Lesson Delta Action must be one of: none, add, update."
    lessons = _LESSONS.search(block)
    said = (lessons.group(1) if lessons else "").strip()
    known = lesson_ids(kb.read_text(encoding="utf-8")) if kb.is_file() else []

    if named == "none":
        if said and said.upper() != "NONE":
            return "Lesson Delta says Action: none but names lessons -- inconsistent."
        if not known and not allow_empty_none:
            return (
                "Lesson Delta says none, but the KB is empty and this loop requires "
                "a first lesson before none is allowed (require_bitlesson_entry_for_none)."
            )
        return None

    if not said or said.upper() == "NONE":
        return f"Lesson Delta Action: {named} names no lesson IDs."
    notes = _NOTES.search(block)
    wrote = (notes.group(1) if notes else "").strip()
    if not wrote or _UNWRITTEN.match(wrote):
        return f"Lesson Delta Action: {named} has no concrete Notes."
    if not kb.is_file():
        return f"Lesson Delta Action: {named} but the KB file does not exist: {kb}"
    wanted = [one.strip() for one in said.split(",") if one.strip()]
    malformed = [one for one in wanted if not _ID_SHAPE.match(one)]
    if malformed:
        return f"Lesson IDs are shaped BL-<yyyymmdd>-<name>; malformed: {', '.join(malformed)}"
    bad = [one for one in wanted if one not in known]
    if bad:
        return (
            f"Lesson Delta names lesson IDs not present in the KB: {', '.join(bad)}. "
            "Add the entries to proof-lessons.md first (IDs must match `### BL-...` "
            "headings exactly)."
        )
    return None


def select_relevant(kb: Path) -> Optional[list[str]]:
    """The selector's empty-KB short-circuit: None means 'make no LLM call'.

    Returns [] for 'KB exists but nothing selected here' -- the actual selection
    is a schema-ask to a cheap model done by the flow (§5.6); this helper only
    owns the short-circuit, exactly v1's bitlesson-select.sh.
    """
    if not kb.is_file():
        return None
    if not lesson_ids(kb.read_text(encoding="utf-8")):
        return None
    return []
