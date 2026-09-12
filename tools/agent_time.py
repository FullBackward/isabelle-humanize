#!/usr/bin/env python3
"""Humanfia-style per-run cost / token / agent-active-time from opencode's store.

Reads opencode's SQLite session DB (~/.local/share/opencode/opencode.db) and
aggregates, per run (one workspace directory + time window):

- cost (USD) — COMPUTED from token counts x the DeepSeek official price table
  below, NOT opencode's `cost` field: that field uses opencode's own
  catalogue prices, which underreport ~3.3x against DeepSeek console billing
  (verified 2026-09-10: A1 $3.3 vs console 25 CNY, B5 $14.7 vs console
  ~100 CNY, both off-peak). opencode's figure is printed for reference.
- token totals (input / output / reasoning / cache_read / cache_write)
- agent-active minutes: sum of step-start -> step-finish durations, the
  closest available analog to humanfia's "API time". Caveat: a step also
  contains its tool executions (bash sleeps, MCP waits), so for runs with
  long tool calls this overstates pure model-API time. For runs without
  infra incidents it tracks wall time closely.

Billing model (DeepSeek, per console deduction rules):
    cost = input(miss) x miss_price + cache_read x hit_price
           + cache_write x miss_price + (output + reasoning) x out_price
opencode's tokens_input is the NON-cached input; tokens_cache_read is the
re-sent cached context (billed at hit price); reasoning bills as output.

Usage:
    python tools/agent_time.py --workspace /home/winst/putnam-runs/q1 \
        [--from 2026-09-03T00:00] [--to 2026-09-05T00:00] [--db PATH] [-v]

Stdlib only. The DB is read-only for this script.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = str(Path.home() / ".local" / "share" / "opencode" / "opencode.db")

#: USD per 1M tokens, from https://api-docs.deepseek.com/quick_start/pricing
#: (fetched 2026-09-10). Peak hours: 01:00-04:00 and 06:00-10:00 UTC Mon-Fri;
#: all other hours off-peak. NOTE: deepseek-v4-pro retires 2026-09-14, after
#: which requests route to V4.1 Flash at Flash prices — update this table.
PRICES = {
    "deepseek-v4-pro": {
        "off-peak": {"hit": 0.022, "miss": 0.66, "out": 1.98},
        "peak": {"hit": 0.044, "miss": 1.32, "out": 3.96},
    },
    "deepseek-flash": {
        "off-peak": {"hit": 0.003, "miss": 0.15, "out": 0.60},
        "peak": {"hit": 0.006, "miss": 0.30, "out": 1.20},
    },
}
DEFAULT_MODEL = "deepseek-v4-pro"


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp() * 1000)


def collect(db_path: str, workspace: str, t0: int, t1: int) -> dict:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    scols = [r[1] for r in db.execute("PRAGMA table_info(session)")]
    sessions = []
    for row in db.execute(
        "SELECT * FROM session WHERE directory=? AND time_created BETWEEN ? AND ?"
        " ORDER BY time_created",
        (workspace, t0, t1),
    ):
        sessions.append(dict(zip(scols, row)))
    steps = 0
    active_ms = 0
    for sess in sessions:
        starts: dict[str, int] = {}
        finishes: list[tuple[str, int]] = []
        for mid, ts, data in db.execute(
            "SELECT message_id, time_created, data FROM part WHERE session_id=?",
            (sess["id"],),
        ):
            try:
                kind = json.loads(data).get("type")
            except Exception:
                continue
            if kind == "step-start":
                starts[mid] = ts
            elif kind == "step-finish":
                finishes.append((mid, ts))
        for mid, fin in finishes:
            cand = [s for m2, s in starts.items() if m2 == mid and s <= fin]
            if cand:
                active_ms += fin - max(cand)
                steps += 1
    return {
        "sessions": sessions,
        "n_sessions": len(sessions),
        "steps": steps,
        "opencode_cost": sum(s.get("cost") or 0 for s in sessions),
        "tokens_input": sum(s.get("tokens_input") or 0 for s in sessions),
        "tokens_output": sum(s.get("tokens_output") or 0 for s in sessions),
        "tokens_reasoning": sum(s.get("tokens_reasoning") or 0 for s in sessions),
        "tokens_cache_read": sum(s.get("tokens_cache_read") or 0 for s in sessions),
        "tokens_cache_write": sum(s.get("tokens_cache_write") or 0 for s in sessions),
        "active_min": active_ms / 60000.0,
    }


def deepseek_cost(stats: dict, model: str) -> dict[str, float]:
    """Cost bounds (off-peak, peak) from token counts x the PRICES table."""
    table = PRICES[model]
    out = {}
    for label, p in table.items():
        out[label] = (
            stats["tokens_cache_read"] * p["hit"]
            + (stats["tokens_input"] + stats["tokens_cache_write"]) * p["miss"]
            + (stats["tokens_output"] + stats["tokens_reasoning"]) * p["out"]
        ) / 1e6
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", required=True, help="run workspace directory (as spawned)")
    ap.add_argument("--from", dest="t0", default="2026-01-01T00:00")
    ap.add_argument("--to", dest="t1", default="2100-01-01T00:00")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(PRICES),
                    help="price table to bill against")
    ap.add_argument("-v", "--verbose", action="store_true", help="per-session breakdown")
    args = ap.parse_args()
    stats = collect(args.db, args.workspace, _ms(args.t0), _ms(args.t1))
    cost = deepseek_cost(stats, args.model)
    print(f"workspace: {args.workspace}  [{args.t0} .. {args.t1}]")
    print(f"sessions: {stats['n_sessions']}   steps: {stats['steps']}")
    print(
        f"cost ({args.model}): ${cost['off-peak']:.2f} off-peak / ${cost['peak']:.2f} peak"
        f"   (opencode catalogue said ${stats['opencode_cost']:.2f})"
    )
    print(
        "tokens: in={tokens_input:,} out={tokens_output:,} reasoning={tokens_reasoning:,} "
        "cache_read={tokens_cache_read:,} cache_write={tokens_cache_write:,}".format(**stats)
    )
    print(f"agent-active: {stats['active_min']:.1f} min")
    if args.verbose:
        for s in stats["sessions"]:
            when = datetime.fromtimestamp(s["time_created"] / 1000, timezone.utc)
            print(
                f"  {when:%m-%d %H:%M} {s['id'][:24]} cost=${s.get('cost') or 0:.4f} "
                f"reason={s.get('tokens_reasoning') or 0:,}"
            )


if __name__ == "__main__":
    main()
