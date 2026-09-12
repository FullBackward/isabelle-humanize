#!/usr/bin/env python3
"""LSP MCP smoke test: drive mcp_lsp_server end-to-end, no hmz involved.

Usage:
    python tools/smoke_lsp.py [--workdir DIR] [--repo DIR] [--venv-python PY]
    python tools/smoke_lsp.py --arbiter [--record DIR]

Needs: pip install "mcp<2" httpx  (the lsp-mcp venv; the mcp<2 pin is
load-bearing -- mcp 2.0 removed FastMCP). The IsabelleGym stack must be up
(curl http://localhost:8001/healthz).

Default mode drives the handoff sequence over stdio MCP:
isabelle_open -> append a proof step ON DISK -> isabelle_diagnostic_messages
-> isabelle_goal -> isabelle_sledgehammer -> isabelle_multi_attempt
(2 candidates) -> isabelle_close. Exit criterion: every call returns
structured output and `lemma True by simp` closes end-to-end
(success=true, proof_open=false after the disk edit).

--arbiter POSTs a known-good and a sorry-containing theory text to the gym's
bigstep endpoint and expects theory_verified true/false respectively --
the exact request/response shape flows/isabelle_rlcr/arbiter.py reimplements
with stdlib urllib. --record DIR additionally saves the four JSON documents
(request/response x good/sorry) as fixtures (tests/fixtures/arbiter/ was
recorded this way in M0).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
from datetime import timedelta

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
FLOW_DIR = REPO_ROOT / "flows" / "isabelle_rlcr"

#: Defaults match the M0 machine setup; every one is overridable.
DEFAULT_REPO = os.environ.get("ISABELLEGYM_REPO", str(pathlib.Path.home() / "IsabelleGym"))
DEFAULT_VENV_PY = str(pathlib.Path.home() / ".venvs" / "lsp-mcp" / "bin" / "python")
DEFAULT_GYM_URL = "http://localhost:8001"

INITIAL = 'theory Smoke\nimports Main\nbegin\n\nlemma true_smoke: "True"\n'
APPEND = "  by simp\n"
LEMMA_LINE = 5  # 1-based line of the lemma command

GOOD_THEORY = (
    "theory Smoke\nimports Main\nbegin\n\nlemma true_smoke: \"True\" by simp\n\nend\n"
)
SORRY_THEORY = (
    "theory Smoke\nimports Main\nbegin\n\nlemma true_smoke: \"True\" by sorry\n\nend\n"
)


async def smoke(args: argparse.Namespace) -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    work = pathlib.Path(args.workdir)
    thy = work / "Smoke.thy"
    work.mkdir(parents=True, exist_ok=True)
    thy.write_text(INITIAL, encoding="utf-8")

    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": args.repo,
            "ISABELLE_MCP_LSP_GYM_URL": args.gym_url,
            "ISABELLE_MCP_LSP_TRANSPORT": "stdio",
            "ISABELLE_MCP_LSP_LOAD_TIMEOUT": "300",
            "ISABELLE_MCP_LSP_HTTP_TIMEOUT": "900",
        }
    )
    params = StdioServerParameters(
        command=args.venv_python, args=["-m", "mcp_lsp_server.app"], env=env
    )

    transcript: list = []
    failures = 0

    async with stdio_client(params) as (read, write):
        async with ClientSession(
            read, write, read_timeout_seconds=timedelta(seconds=900)
        ) as s:
            await s.initialize()
            tools = await s.list_tools()
            names = sorted(t.name for t in tools.tools)
            transcript.append(["list_tools", names])
            print(f"== list_tools\n{names}\n", flush=True)

            async def call(name: str, **kwargs):
                nonlocal failures
                r = await s.call_tool(name, kwargs)
                out = "\n".join(getattr(b, "text", str(b)) for b in r.content)
                transcript.append([name, kwargs, out])
                print(f"== {name} {kwargs}\n{out}\n", flush=True)
                if r.isError:
                    failures += 1
                return out

            await call("isabelle_open", file_path=str(thy))
            # Append a proof step ON DISK; the next tool call must re-sync.
            with thy.open("a", encoding="utf-8") as f:
                f.write(APPEND)
            print(f"-- appended on disk: {APPEND!r}\n", flush=True)
            diag = await call("isabelle_diagnostic_messages", file_path=str(thy))
            await call("isabelle_goal", file_path=str(thy), line=LEMMA_LINE)
            await call(
                "isabelle_sledgehammer",
                file_path=str(thy),
                line=LEMMA_LINE,
                timeout_s=60,
            )
            await call(
                "isabelle_multi_attempt",
                file_path=str(thy),
                line=LEMMA_LINE + 1,
                candidates=["by simp", "by auto"],
            )
            await call("isabelle_close", file_path=str(thy))

    out = work / "transcript.json"
    out.write_text(json.dumps(transcript, indent=2), encoding="utf-8")
    print(f"transcript written to {out}", flush=True)

    closed = "proof_open" in diag and "false" in diag
    if failures or not closed:
        print(f"SMOKE FAIL: {failures} tool error(s), closed={closed}")
        return 1
    print("SMOKE PASS: all calls structured, lemma True closed end-to-end")
    return 0


def arbiter_smoke(args: argparse.Namespace) -> int:
    """The M0.5 arbiter check: bigstep endpoint, good vs sorry-containing text."""
    if str(FLOW_DIR) not in sys.path:
        sys.path.insert(0, str(FLOW_DIR))
    import arbiter  # noqa: E402 -- flow dir on sys.path, as hmz loads it

    base = args.gym_url.rstrip("/")
    failures = 0
    for tag, text, expect in (("good", GOOD_THEORY, True), ("sorry", SORRY_THEORY, False)):
        payload = {
            "theory_name": "Smoke",
            "theory": text,
            "dependencies": ["Main"],
            "field": "HOL",
            "timeout": 600,
        }
        try:
            answered = arbiter._request(
                "POST", f"{base}/api/v1/sessions/bigstep", payload, 660.0
            )
        except arbiter.ArbiterError as failed:
            print(f"== {tag}: TRANSPORT FAIL: {failed}")
            failures += 1
            continue
        verified = bool(answered.get("theory_verified"))
        status = "OK" if verified == expect else "UNEXPECTED"
        print(f"== {tag}: theory_verified={verified} (expected {expect}) -- {status}")
        if verified != expect:
            failures += 1
        if args.record:
            record = pathlib.Path(args.record)
            record.mkdir(parents=True, exist_ok=True)
            (record / f"request_{tag}.json").write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )
            (record / f"response_{tag}.json").write_text(
                json.dumps(answered, indent=2) + "\n", encoding="utf-8"
            )
    if args.record:
        print(f"fixtures written to {args.record}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gym-url", default=DEFAULT_GYM_URL)
    parser.add_argument("--repo", default=DEFAULT_REPO,
                        help="IsabelleGym checkout (PYTHONPATH for the MCP server)")
    parser.add_argument("--venv-python", default=DEFAULT_VENV_PY,
                        help="python with 'mcp<2' + httpx for the MCP server")
    parser.add_argument("--workdir", default=str(pathlib.Path.home() / "m0-workspace"))
    parser.add_argument("--arbiter", action="store_true",
                        help="check the bigstep arbiter endpoint instead of the MCP")
    parser.add_argument("--record", metavar="DIR",
                        help="with --arbiter: save request/response JSON fixtures here")
    args = parser.parse_args(argv)

    if args.arbiter:
        return arbiter_smoke(args)
    try:
        import mcp  # noqa: F401
    except ImportError:
        print("error: the 'mcp<2' package is required (pip install \"mcp<2\" httpx)",
              file=sys.stderr)
        return 2
    return asyncio.run(smoke(args))


if __name__ == "__main__":
    raise SystemExit(main())
