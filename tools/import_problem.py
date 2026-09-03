#!/usr/bin/env python3
"""Import a single-theorem .thy file as an isabelle_rlcr workspace.

Usage: python tools/import_problem.py <source.thy> <workspace-dir> [--field HOL]

The source is a MCP-comparison-style single-theorem theory whose statement ends
in `sorry`. The workspace it becomes holds:

- problem.thy     -- the theory, normalized: the theory is renamed to `Problem`
                     (Isabelle requires theory name == file name) and a
                     canonical task-spec header comment is injected as the
                     first line, parsed/written with the regex conventions of
                     flows/isabelle_rlcr/arbiter.py:parse_spec:
                         (* TASK: theorem=<name> imports=<imports> field=<field> *)
- .mcp.json       -- copied from workspaces/template/
- .gitignore      -- copied from workspaces/template/
- a fresh git repo with one initial commit (the loop anchors rounds to commits;
  the commit carries its own -c user.email/user.name, never the repo's config)
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FLOW_DIR = REPO_ROOT / "flows" / "isabelle_rlcr"
TEMPLATE_DIR = REPO_ROOT / "workspaces" / "template"

# The flow dir on sys.path, exactly as hmz's runpy loading and tests/conftest.py
# do it, so the task-spec regexes come from arbiter.py rather than a copy.
if str(FLOW_DIR) not in sys.path:
    sys.path.insert(0, str(FLOW_DIR))

import arbiter  # noqa: E402

#: The theory line, for the rename to `Problem` (capture the keyword, swap the name).
_THEORY_NAME = re.compile(r"^(\s*theory\s+)[A-Za-z][A-Za-z0-9_']*", re.MULTILINE)

#: Files copied verbatim from the template into every imported workspace.
_TEMPLATE_FILES = (".mcp.json", ".gitignore")


def normalize_problem(text: str, default_field: str) -> tuple[str, "arbiter.TaskSpec"]:
    """Canonical problem.thy text + the parsed spec.

    The task spec is read with arbiter.parse_spec (TASK comment when present,
    theory/imports/first-lemma fallback otherwise), any existing TASK comment is
    stripped, the theory is renamed to `Problem`, and a canonical TASK comment
    is written back as the first line.
    """
    spec = arbiter.parse_spec(text, default_field)
    body = arbiter._TASK_COMMENT.sub("", text, count=1)
    body = _THEORY_NAME.sub(r"\g<1>Problem", body, count=1)
    imports = ",".join(spec.imports)
    header = (
        f"(* TASK: theorem={spec.target_theorem} "
        f"imports={imports} field={spec.field} *)"
    )
    normalized = header + "\n" + body.lstrip("\n")
    return normalized, spec.model_copy(update={"theory_name": "Problem"})


def init_git(workspace: Path, message: str) -> None:
    """git init + one initial commit, with the loop's own identity."""
    subprocess.run(
        ["git", "init"], cwd=workspace, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "add", "-A"], cwd=workspace, check=True, capture_output=True, text=True
    )
    subprocess.run(
        [
            "git", "-c", "user.email=rlcr@local", "-c", "user.name=rlcr",
            "commit", "-m", message,
        ],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )


def create_workspace(
    source: Path, workspace: Path, field: str = "HOL", git: bool = True
) -> "arbiter.TaskSpec":
    """Build the workspace from the source theory. Returns the parsed spec."""
    if workspace.exists():
        raise FileExistsError(f"{workspace}: already exists -- refusing to overwrite")
    text = source.read_text(encoding="utf-8")
    normalized, spec = normalize_problem(text, field)
    workspace.mkdir(parents=True)
    (workspace / "problem.thy").write_text(normalized, encoding="utf-8")
    for name in _TEMPLATE_FILES:
        shutil.copyfile(TEMPLATE_DIR / name, workspace / name)
    if git:
        init_git(workspace, f"import problem {spec.target_theorem}")
    return spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert a single-theorem .thy file (statement ending in "
        "`sorry`) into an isabelle_rlcr workspace: normalized problem.thy with a "
        "TASK header comment, template .mcp.json/.gitignore, git repo + initial commit."
    )
    parser.add_argument("source", type=Path, help="the source .thy file")
    parser.add_argument("workspace", type=Path, help="workspace dir to create (must not exist)")
    parser.add_argument("--field", default="HOL",
                        help="fallback Isabelle session field when the source has no "
                        "TASK comment (default %(default)s)")
    parser.add_argument("--no-git", action="store_true",
                        help="skip git init + initial commit")
    args = parser.parse_args(argv)

    if not args.source.is_file():
        print(f"error: source not found: {args.source}", file=sys.stderr)
        return 2
    try:
        spec = create_workspace(
            args.source, args.workspace, field=args.field, git=not args.no_git
        )
    except (FileExistsError, arbiter.ArbiterError, subprocess.CalledProcessError) as failed:
        print(f"error: {failed}", file=sys.stderr)
        return 2
    print(
        f"imported {args.source} -> {args.workspace}: theory Problem, "
        f"theorem {spec.target_theorem}, imports {','.join(spec.imports)}, "
        f"field {spec.field}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
