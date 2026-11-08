#!/usr/bin/env python3
"""selftest-cost.py — offline proof that cost.py counts each API call once. Must print SELFTEST PASS.

Claude Code writes one jsonl line per content block, each repeating the message's usage. The fixture session holds a
three-block message (one id pair on three lines), a one-block message, a line with usage but no ids (kept: nothing
says it repeats), and a two-block message in a subagent file (dedupe is per file). A second file reusing the first
message's ids counts again: ids are only compared within one session file.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("cost", HERE / "cost.py")
cost = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cost)

USAGE = {"input_tokens": 10, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 1000, "output_tokens": 20}
UNITS = 10 + 1.25 * 100 + 0.1 * 1000 + 5 * 20  # 335 per call


def line(msg_id: str | None, request: str | None, block: str) -> str:
    event = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": block}], "usage": USAGE}}
    if msg_id:
        event["message"]["id"] = msg_id
    if request:
        event["requestId"] = request
    return json.dumps(event) + "\n"


def main() -> int:
    failures = []

    def check(name: str, got: float, want: float) -> None:
        if abs(got - want) > 1e-6:
            failures.append(f"{name}: got {got}, want {want}")

    with tempfile.TemporaryDirectory() as tmp:
        run = Path(tmp) / "x-t2-factory-v030-haiku-1"
        builder = run / "home" / ".claude" / "projects" / "-tmp-x-ws"
        (builder / "s1" / "subagents").mkdir(parents=True)
        main_session = builder / "s1.jsonl"
        main_session.write_text(
            line("msg_a", "req_a", "thinking") + line("msg_a", "req_a", "text") + line("msg_a", "req_a", "tool_use")
            + json.dumps({"type": "user", "message": {"role": "user", "content": "ok"}}) + "\n"
            + line("msg_b", "req_b", "text") + line(None, None, "text") + "not json\n")
        (builder / "s1" / "subagents" / "agent-1.jsonl").write_text(
            line("msg_a", "req_a", "text") + line("msg_a", "req_a", "tool_use"))

        units, calls = cost.session_units(main_session)
        check("main session deduplicated units", units, 3 * UNITS)
        check("main session deduplicated calls", calls, 3)
        units, calls = cost.session_units(main_session, dedupe=False)
        check("main session per-line units (old meter)", units, 5 * UNITS)
        check("main session per-line calls", calls, 5)

        built = cost.build_cost(run)
        check("builder", built["builder"], 3 * UNITS)
        check("subagents (ids compared per file)", built["subagents"], UNITS)
        check("total", built["total"], 4 * UNITS)
        check("total per-line", cost.build_cost(run, dedupe=False)["total"], 7 * UNITS)

        out = subprocess.run([sys.executable, str(HERE / "cost.py"), str(run), "--recompute", "--json"],
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            failures.append(f"--recompute --json exited {out.returncode}: {out.stderr}")
        else:
            data = json.loads(out.stdout)["builds"][run.name]
            check("--recompute total", data["total"], 4 * UNITS)
            check("--recompute total_per_line", data["total_per_line"], 7 * UNITS)
        text = subprocess.run([sys.executable, str(HERE / "cost.py"), str(run), "--recompute"],
                              capture_output=True, text=True, timeout=60).stdout
        if "per-line" not in text or "0.571" not in text:
            failures.append(f"--recompute table lacks the per-line column or the 4/7 ratio:\n{text}")

    with tempfile.TemporaryDirectory() as tmp:
        run = Path(tmp) / "x-t2-factory-v030-haiku-1"
        evaluation = cost._workspace(run) / "work" / "evaluation"
        evaluation.mkdir(parents=True)
        (evaluation / "gate.json").write_text(json.dumps({"model": "claude-haiku-5-5"}))
        (evaluation / "plugin-eval.json").write_text(json.dumps({"costUsd": 0.5}))
        (evaluation / "plugin-eval.prev.json").write_text(json.dumps({"costUsd": 0.25}))
        price = cost.PRICE_PER_MTOK["haiku"] / 1e6
        check("gate counts the replaced run too", cost.gate_plugin_units(run), 0.75 / price)

    for failure in failures:
        print("FAIL:", failure)
    print("SELFTEST FAIL" if failures else "SELFTEST PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
