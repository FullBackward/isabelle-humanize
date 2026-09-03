"""Unit tests for flows/isabelle_rlcr/memory.py -- the proof-lessons KB.

Pure functions against in-memory text and tmp_path KB files. No live agents,
no Isabelle, no network.
"""

from __future__ import annotations

from pathlib import Path

import memory

LESSON_ONE = """### BL-20260901-simp-before-auto
- Scope: equational goals over lists
- Goal Shape: rewrite chains
- Failing Approach: auto loops
- Working Approach: simp first, then auto
- Evidence Round: 3
"""

LESSON_TWO = """### BL-20260902-induction-on-tail
- Scope: recursive list goals
- Goal Shape: fold-like recursion
- Failing Approach: induction on the head
- Working Approach: induction on the tail
- Evidence Round: 7
"""

KB_TEXT = memory.KB_TEMPLATE + "\n" + LESSON_ONE + "\n" + LESSON_TWO


def _kb(tmp_path: Path, text: str = KB_TEXT) -> Path:
    path = tmp_path / "proof-lessons.md"
    path.write_text(text, encoding="utf-8")
    return path


def _summary(body: str) -> str:
    return f"# Round 3 summary\n\nDid the thing.\n\n## Lesson Delta\n{body}"


# ------------------------------------------------------------------ lesson_ids


def test_lesson_ids_empty_template() -> None:
    assert memory.lesson_ids(memory.KB_TEMPLATE) == []


def test_lesson_ids_extracts_in_order() -> None:
    assert memory.lesson_ids(KB_TEXT) == [
        "BL-20260901-simp-before-auto",
        "BL-20260902-induction-on-tail",
    ]


def test_lesson_ids_ignores_non_heading_mentions() -> None:
    text = "See BL-20260901-simp-before-auto for details.\n" + LESSON_ONE
    assert memory.lesson_ids(text) == ["BL-20260901-simp-before-auto"]


def test_lesson_ids_rejects_malformed_headings() -> None:
    text = "### BL-2026-short-date\n- x\n### XX-20260901-wrong-prefix\n- y\n"
    assert memory.lesson_ids(text) == []


# --------------------------------------------------------------------- entries


def test_entries_returns_full_text_by_id() -> None:
    found = memory.entries(KB_TEXT, ["BL-20260901-simp-before-auto"])
    assert list(found) == ["BL-20260901-simp-before-auto"]
    assert found["BL-20260901-simp-before-auto"].startswith(
        "### BL-20260901-simp-before-auto"
    )
    assert "Evidence Round: 3" in found["BL-20260901-simp-before-auto"]
    # The entry stops at the next lesson heading.
    assert "BL-20260902" not in found["BL-20260901-simp-before-auto"]


def test_entries_last_entry_runs_to_eof() -> None:
    found = memory.entries(KB_TEXT, ["BL-20260902-induction-on-tail"])
    assert "Evidence Round: 7" in found["BL-20260902-induction-on-tail"]


def test_entries_unknown_id_omitted() -> None:
    assert memory.entries(KB_TEXT, ["BL-20260903-nope"]) == {}


# ------------------------------------------------------------------ delta_block


def test_delta_block_plain() -> None:
    block = memory.delta_block(_summary("Action: none\n"))
    assert block is not None
    assert "Action: none" in block


def test_delta_block_missing() -> None:
    assert memory.delta_block("# summary\n\nno delta here\n") is None


def test_delta_block_stops_at_next_heading() -> None:
    summary = _summary("Action: none\n") + "\n## Other Section\nnot part of it\n"
    block = memory.delta_block(summary)
    assert block is not None
    assert "Action: none" in block
    assert "Other Section" not in block


def test_delta_block_heading_inside_fence_ignored() -> None:
    summary = (
        "# summary\n\n"
        "```markdown\n"
        "## Lesson Delta\n"
        "Action: add\n"
        "```\n"
        "\nno real block\n"
    )
    assert memory.delta_block(summary) is None


def test_delta_block_real_heading_after_fence() -> None:
    summary = (
        "```\n## Lesson Delta\nAction: add\n```\n\n"
        "## Lesson Delta\nAction: none\n"
    )
    block = memory.delta_block(summary)
    assert block is not None
    assert "Action: none" in block


# -------------------------------------------------------------- validate_delta


def test_validate_delta_missing_block_refuses(tmp_path: Path) -> None:
    refusal = memory.validate_delta("# summary\n", _kb(tmp_path))
    assert refusal is not None
    assert "missing" in refusal.lower()


def test_validate_delta_bad_action_refuses(tmp_path: Path) -> None:
    refusal = memory.validate_delta(_summary("Action: maybe\n"), _kb(tmp_path))
    assert refusal is not None
    assert "none, add, update" in refusal


def test_validate_delta_none_with_lessons_refuses(tmp_path: Path) -> None:
    refusal = memory.validate_delta(
        _summary("Action: none\nLessons: BL-20260901-simp-before-auto\n"),
        _kb(tmp_path),
    )
    assert refusal is not None
    assert "inconsistent" in refusal


def test_validate_delta_empty_kb_none_disallowed(tmp_path: Path) -> None:
    refusal = memory.validate_delta(
        _summary("Action: none\n"),
        _kb(tmp_path, memory.KB_TEMPLATE),
        allow_empty_none=False,
    )
    assert refusal is not None
    assert "empty" in refusal.lower()


def test_validate_delta_add_without_ids_refuses(tmp_path: Path) -> None:
    refusal = memory.validate_delta(
        _summary("Action: add\nNotes: added the simp lesson\n"), _kb(tmp_path)
    )
    assert refusal is not None
    assert "names no lesson IDs" in refusal


def test_validate_delta_add_with_none_ids_refuses(tmp_path: Path) -> None:
    refusal = memory.validate_delta(
        _summary("Action: add\nLessons: NONE\nNotes: added the simp lesson\n"),
        _kb(tmp_path),
    )
    assert refusal is not None
    assert "names no lesson IDs" in refusal


def test_validate_delta_add_without_notes_refuses(tmp_path: Path) -> None:
    refusal = memory.validate_delta(
        _summary("Action: add\nLessons: BL-20260901-simp-before-auto\n"),
        _kb(tmp_path),
    )
    assert refusal is not None
    assert "no concrete Notes" in refusal


def test_validate_delta_add_placeholder_notes_refuses(tmp_path: Path) -> None:
    for placeholder in ("TODO", "n/a", "none", "N/A", "(none)", "-"):
        refusal = memory.validate_delta(
            _summary(
                "Action: add\n"
                "Lessons: BL-20260901-simp-before-auto\n"
                f"Notes: {placeholder}\n"
            ),
            _kb(tmp_path),
        )
        assert refusal is not None, placeholder
        assert "no concrete Notes" in refusal


def test_validate_delta_ids_not_in_kb_refuses(tmp_path: Path) -> None:
    refusal = memory.validate_delta(
        _summary(
            "Action: update\n"
            "Lessons: BL-20260903-not-there\n"
            "Notes: updated the evidence round\n"
        ),
        _kb(tmp_path),
    )
    assert refusal is not None
    assert "not present in the KB" in refusal
    assert "BL-20260903-not-there" in refusal


def test_validate_delta_malformed_id_refuses(tmp_path: Path) -> None:
    # A malformed ID can never be one the KB declares (lesson_ids only
    # extracts well-shaped IDs), so it is refused by the not-in-KB gate.
    refusal = memory.validate_delta(
        _summary(
            "Action: add\nLessons: BL-2026-short\nNotes: added a lesson\n"
        ),
        _kb(tmp_path),
    )
    assert refusal is not None
    assert "BL-2026-short" in refusal


def test_validate_delta_add_missing_kb_file_refuses(tmp_path: Path) -> None:
    refusal = memory.validate_delta(
        _summary(
            "Action: add\n"
            "Lessons: BL-20260901-simp-before-auto\n"
            "Notes: added the simp lesson\n"
        ),
        tmp_path / "proof-lessons.md",  # never written
    )
    assert refusal is not None
    assert "does not exist" in refusal


def test_validate_delta_none_passes_empty_kb(tmp_path: Path) -> None:
    assert (
        memory.validate_delta(_summary("Action: none\n"), _kb(tmp_path, memory.KB_TEMPLATE))
        is None
    )


def test_validate_delta_none_passes_with_kb(tmp_path: Path) -> None:
    assert memory.validate_delta(_summary("Action: none\n"), _kb(tmp_path)) is None


def test_validate_delta_none_explicit_lessons_none_passes(tmp_path: Path) -> None:
    assert (
        memory.validate_delta(_summary("Action: none\nLessons: NONE\n"), _kb(tmp_path))
        is None
    )


def test_validate_delta_add_valid_passes(tmp_path: Path) -> None:
    assert (
        memory.validate_delta(
            _summary(
                "Action: add\n"
                "Lessons: BL-20260901-simp-before-auto\n"
                "Notes: simp before auto discharged the list goal\n"
            ),
            _kb(tmp_path),
        )
        is None
    )


def test_validate_delta_update_valid_multiple_ids_passes(tmp_path: Path) -> None:
    assert (
        memory.validate_delta(
            _summary(
                "Action: update\n"
                "Lessons: BL-20260901-simp-before-auto, BL-20260902-induction-on-tail\n"
                "Notes: refreshed both evidence rounds\n"
            ),
            _kb(tmp_path),
        )
        is None
    )


# -------------------------------------------------------------- select_relevant


def test_select_relevant_missing_kb_short_circuits(tmp_path: Path) -> None:
    assert memory.select_relevant(tmp_path / "proof-lessons.md") is None


def test_select_relevant_empty_kb_short_circuits(tmp_path: Path) -> None:
    assert memory.select_relevant(_kb(tmp_path, memory.KB_TEMPLATE)) is None


def test_select_relevant_non_empty_kb_selects(tmp_path: Path) -> None:
    assert memory.select_relevant(_kb(tmp_path)) == []
