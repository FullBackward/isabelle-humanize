#!/usr/bin/env python3
"""Ablation driver: RLCR arm vs solo arm over problem workspaces.

Usage:
    python tools/ablation.py --flow <path-to-flow-dir> --agent <model> \
        --workspace DIR [DIR ...] [--jobs 1] [--out table.json] [--list]

For each workspace the tool runs two arms of the loop via hmz:

- rlcr: `hmz exec -f <flow>:rlcr -a <agent> -a <agent> "<task>"` (builder+reviewer)
- solo: `hmz exec -f <flow>:solo -a <agent> "<task>"` (the no-critique control)

Each source workspace is COPIED to a fresh run dir per arm -- sources are never
mutated (leftover `.rlcr/` state is excluded from the copy so every run starts
clean). A copy whose source carries no `.git` of its own (e.g. a workspace
committed inside another repository) gets git init + one initial commit: the
flow refuses to run outside a git repository. Runs whose `hmz exec` exits
nonzero are skipped and reported as errors.
Metrics are read back from each run's journal: solved (arbiter), terminal
state, rounds, wall time, stall count, reviewer-agreement rate, lesson count.

REAL RUNS COST TOKENS. `--list` prints exactly what would run without running
anything; the tool is otherwise a manual, token-gated step (implementation-plan
§7).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from import_problem import init_git

#: The two arms of the experiment, as hmz flow entry points.
ARMS = ("rlcr", "solo")

#: The default task text handed to the flow (cwd is the run's workspace copy).
DEFAULT_TASK = "prove the pending theorem in problem.thy in the current workspace"

#: Lesson IDs as the KB declares them -- kept in parity with
#: flows/isabelle_rlcr/memory.py _LESSON_ID (counting entries needs no import).
_LESSON_ID = re.compile(r"^### (BL-\d{8}-[A-Za-z0-9_-]+)\s*$", re.MULTILINE)

#: Journal timestamp format (flows/isabelle_rlcr/_journal).
_TS = "%Y-%m-%dT%H:%M:%SZ"


def hmz_path() -> str:
    """The hmz executable: $HMZ, else the known venv location."""
    return os.environ.get("HMZ") or str(Path("~/.venvs/hmz/bin/hmz").expanduser())


def resolve_flow_dir(flow: Path) -> Path:
    """Accept the flow dir itself, or a repo root holding flows/isabelle_rlcr.

    Always returns an absolute path: hmz exec runs with cwd=<run dir>, so a
    relative -f path would not resolve there.
    """
    flow = flow.resolve()
    if (flow / "__init__.py").is_file() and (flow / "schemas.py").is_file():
        return flow
    nested = flow / "flows" / "isabelle_rlcr"
    if (nested / "__init__.py").is_file():
        return nested
    raise FileNotFoundError(f"{flow}: not the isabelle_rlcr flow dir (nor a repo root holding one)")


def parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, _TS).replace(tzinfo=timezone.utc)


def count_lessons(kb_text: str) -> int:
    """Entries in proof-lessons.md."""
    return len(_LESSON_ID.findall(kb_text))


def metrics_from_records(records: list[dict], kb_text: str = "") -> dict:
    """The ablation metrics for one run, read from its journal records.

    - solved: any arbiter record with solved: true (the prover decided)
    - terminal: the terminal record's state (None if the run never ended)
    - rounds: the terminal record's round, else the max journaled round
    - wall_s: first-to-last journal timestamp, seconds
    - stalls: drift records (the drift state machine counted a non-ADVANCED
      signal each time one was written)
    - agreement_rate: over verdict records whose agreement is non-null, the
      fraction where reviewer and objective classifier agree (None when the
      reviewer never ran -- the solo arm)
    - lessons: entries in proof-lessons.md
    """
    solved = any(r.get("kind") == "arbiter" and r.get("solved") is True for r in records)
    terminals = [r for r in records if r.get("kind") == "terminal"]
    terminal = terminals[-1].get("terminal") if terminals else None
    if terminals and isinstance(terminals[-1].get("round"), int):
        rounds = int(terminals[-1]["round"])
    else:
        rounds = max((int(r["round"]) for r in records if isinstance(r.get("round"), int)), default=0)
    stamps = [parse_ts(r["ts"]) for r in records if isinstance(r.get("ts"), str)]
    wall_s = (max(stamps) - min(stamps)).total_seconds() if stamps else None
    stalls = sum(1 for r in records if r.get("kind") == "drift")
    graded = [
        r for r in records
        if r.get("kind") == "verdict" and r.get("agreement") is not None
    ]
    agreement_rate = (
        sum(1 for r in graded if r["agreement"]) / len(graded) if graded else None
    )
    return {
        "solved": solved,
        "terminal": terminal,
        "rounds": rounds,
        "wall_s": wall_s,
        "stalls": stalls,
        "verdicts": sum(1 for r in records if r.get("kind") == "verdict"),
        "agreement_rate": agreement_rate,
        "lessons": count_lessons(kb_text),
    }


def summarize_run(run_dir: Path) -> dict:
    """Metrics for one finished run dir; status=no-journal when there is none."""
    journal = run_dir / ".rlcr" / "journal.jsonl"
    if not journal.is_file():
        return {"status": "no-journal"}
    records = [
        json.loads(line)
        for line in journal.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    kb = run_dir / "proof-lessons.md"
    kb_text = kb.read_text(encoding="utf-8") if kb.is_file() else ""
    return {"status": "ok", **metrics_from_records(records, kb_text)}


def build_command(
    flow_dir: Path,
    arm: str,
    agent: str,
    task: str,
    reviewer: Optional[str] = None,
    config: Optional[Path] = None,
) -> list[str]:
    """The hmz exec command line for one arm of one problem.

    rlcr takes two -a specs (builder, reviewer -- the reviewer defaults to the
    builder's spec); solo takes one. --config passes a flow setup YAML (-c),
    e.g. `reviewer_mcp: false` for an MCP-less reviewer such as dsh.
    """
    cmd = [hmz_path(), "exec", "-f", f"{flow_dir}:{arm}"]
    if config is not None:
        cmd += ["-c", str(config)]
    cmd += ["-a", agent]
    if arm == "rlcr":
        cmd += ["-a", reviewer or agent]
    cmd.append(task)
    return cmd


def plan_runs(workspaces: list[Path], runs_dir: Path) -> list[dict]:
    """One planned run per (workspace, arm): src, dst, and the arm name."""
    planned = []
    for src in workspaces:
        for arm in ARMS:
            planned.append(
                {
                    "problem": src.name,
                    "arm": arm,
                    "src": src,
                    "dst": runs_dir / f"{src.name}-{arm}",
                }
            )
    return planned


def prepare_run_dir(src: Path, dst: Path) -> None:
    """Copy the source workspace to its run dir; ensure the copy is a git repo.

    Sources committed inside another repository carry no `.git` of their own
    (a nested repository is not committed), and the flow refuses to run outside
    one -- so the copy gets git init + one initial commit, exactly as
    import_problem.create_workspace does. A source that DOES carry a `.git`
    keeps its history.
    """
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".rlcr", "__pycache__"))
    if not (dst / ".git").is_dir():
        init_git(dst, f"ablation run copy of {src.name}")


def execute_run(
    planned: dict,
    flow_dir: Path,
    agent: str,
    task: str,
    reviewer: Optional[str] = None,
    config: Optional[Path] = None,
) -> dict:
    """Copy the workspace, run one arm, read the metrics back.

    The source is never mutated; a nonzero hmz exit skips the run and is
    reported as an error row rather than aborting the sweep.
    """
    src, dst = planned["src"], planned["dst"]
    row = {"problem": planned["problem"], "arm": planned["arm"], "run_dir": str(dst)}
    try:
        prepare_run_dir(src, dst)
    except (OSError, subprocess.CalledProcessError) as failed:
        return {**row, "status": "error", "error": f"copy failed: {failed}"}
    cmd = build_command(flow_dir, planned["arm"], agent, task, reviewer, config)
    try:
        done = subprocess.run(cmd, cwd=dst, capture_output=True, text=True, check=False)
    except OSError as failed:
        return {**row, "status": "error", "error": f"hmz exec could not start: {failed}"}
    if done.returncode != 0:
        return {
            **row,
            "status": "error",
            "error": f"hmz exec exited {done.returncode}: {done.stderr.strip()[-300:]}",
        }
    return {**row, **summarize_run(dst)}


def print_table(rows: list[dict]) -> None:
    """The results as one aligned row per (problem, arm)."""
    header = ("problem", "arm", "status", "solved", "terminal", "rounds",
              "wall_s", "stalls", "verdicts", "agree", "lessons")
    print("{:<24} {:<5} {:<10} {:<6} {:<10} {:>6} {:>8} {:>6} {:>8} {:>6} {:>7}".format(*header))
    for row in rows:
        agree = row.get("agreement_rate")
        print(
            "{:<24} {:<5} {:<10} {:<6} {:<10} {:>6} {:>8} {:>6} {:>8} {:>6} {:>7}".format(
                row.get("problem", "?"),
                row.get("arm", "?"),
                row.get("status", "?"),
                str(row.get("solved", "-")),
                str(row.get("terminal", "-")),
                str(row.get("rounds", "-")),
                f"{row['wall_s']:.0f}" if isinstance(row.get("wall_s"), float) else "-",
                str(row.get("stalls", "-")),
                str(row.get("verdicts", "-")),
                f"{agree:.2f}" if isinstance(agree, float) else "-",
                str(row.get("lessons", "-")),
            )
        )
    errors = [r for r in rows if r.get("status") == "error"]
    for row in errors:
        print(f"ERROR {row['problem']} ({row['arm']}): {row.get('error', '')}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the RLCR arm and the solo (no-reviewer) arm over problem "
        "workspaces and tabulate the metrics from their journals. Real runs cost "
        "tokens: use --list to see exactly what would run first."
    )
    parser.add_argument("--flow", type=Path, required=True,
                        help="path to the isabelle_rlcr flow dir (or the repo root)")
    parser.add_argument("--agent", required=True,
                        help="hmz agent spec for the builder, e.g. kimi/moonshot-cn/kimi-k3:high")
    parser.add_argument("--reviewer",
                        help="hmz agent spec for the reviewer (rlcr arm only; "
                        "defaults to --agent), e.g. dsh/deepseek-v4-flash:high")
    parser.add_argument("--config", type=Path,
                        help="flow setup YAML passed as -c, e.g. reviewer_mcp: false "
                        "for an MCP-less reviewer such as dsh")
    parser.add_argument("--workspace", type=Path, nargs="+", required=True,
                        help="source problem workspace(s); never mutated")
    parser.add_argument("--jobs", type=int, default=1,
                        help="concurrent hmz runs (default %(default)s)")
    parser.add_argument("--out", type=Path, help="also write the table as JSON here")
    parser.add_argument("--runs-dir", type=Path,
                        help="where run copies go (default ./ablation-runs/<timestamp>)")
    parser.add_argument("--task", default=DEFAULT_TASK, help="task text for the flow")
    parser.add_argument("--list", action="store_true",
                        help="dry run: print the planned copies and commands, run nothing")
    args = parser.parse_args(argv)

    try:
        flow_dir = resolve_flow_dir(args.flow)
    except FileNotFoundError as failed:
        print(f"error: {failed}", file=sys.stderr)
        return 2
    for src in args.workspace:
        if not src.is_dir():
            print(f"error: workspace not found: {src}", file=sys.stderr)
            return 2

    runs_dir = args.runs_dir or (
        Path.cwd() / "ablation-runs" / time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    )
    planned = plan_runs(args.workspace, runs_dir)

    if args.list:
        for one in planned:
            cmd = build_command(
                flow_dir, one["arm"], args.agent, args.task, args.reviewer, args.config
            )
            print(f"copy {one['src']} -> {one['dst']}  (excluding .rlcr/)")
            print(f"  (cd {one['dst']} && {' '.join(cmd)})")
        return 0

    runs_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        rows = list(pool.map(
            lambda one: execute_run(
                one, flow_dir, args.agent, args.task, args.reviewer, args.config
            ),
            planned,
        ))
    print_table(rows)
    if args.out:
        args.out.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
