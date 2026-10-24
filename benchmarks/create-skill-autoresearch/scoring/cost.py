#!/usr/bin/env python3
"""cost.py <run-dir>... [--compare PREFIX=PREFIX] [--json] — what each build spent (criterion 17).

Cost units approximate dollars in units of one fresh input token, so runs on different models compare
within a cell (same model) without a price table:

    units = input + 1.25 * cache_creation_input + 0.1 * cache_read_input + 5 * output

summed over every Claude session jsonl under <run-dir>/home/.claude/projects/ (builder, subagents and
any executor sessions the build launched), plus the plugin-eval gate's own reported cost, which runs
outside those sessions: work/evaluation/plugin-eval.json `costUsd` divided by the model's input price
per token (PRICE_PER_MTOK below; the gate model is the build's executor model, read from gate.json).

Per build it reports units by session class:
  builder     the build's main session (project dir ending in -ws or -work)
  subagents   sessions under any project's subagents/ directory (panel roles, research, ...)
  executors   every other session: run-case.sh / evaluate.sh executors started under HOME
  gate_plugin the plugin-eval gate's costUsd, converted

Not counted: executor calls made straight to an API with curl or an SDK (they leave no session jsonl).
They are the same kind of call in every arm, so cell comparisons stay like-for-like; report them apart
if a factory starts relying on them.

--compare A=B prints, per task x model cell, the median units of run-id prefix A against prefix B with
B's own run-to-run spread (max - min of B's runs) and whether median(A) <= median(B) + that spread
(criterion 17, Amendment 4a; a spread pooled over both arms would let A's own outliers widen the bar):
    python3 scoring/cost.py runs/u-* runs/m-* --compare u-=m- --arms factory-v030=factory-v021
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

WEIGHTS = {"input_tokens": 1.0, "cache_creation_input_tokens": 1.25,
           "cache_read_input_tokens": 0.1, "output_tokens": 5.0}
# USD per million fresh input tokens, used only to convert plugin-eval's costUsd into units.
PRICE_PER_MTOK = {"haiku": 1.0, "sonnet": 3.0, "opus": 5.0}
CLASSES = ("builder", "subagents", "executors", "gate_plugin")


def session_units(path: Path) -> tuple[float, int]:
    units, calls = 0.0, 0
    with path.open(errors="replace") as fh:
        for line in fh:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = event.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
            if not isinstance(usage, dict):
                continue
            calls += 1
            units += sum(w * (usage.get(k) or 0) for k, w in WEIGHTS.items())
    return units, calls


def _workspace(run: Path) -> Path:
    return run / "ws" if (run / "ws").is_dir() else run / "work"


def _price(model: str | None) -> float | None:
    for family, price in PRICE_PER_MTOK.items():
        if model and family in model:
            return price / 1e6
    return None


def gate_plugin_units(run: Path) -> float:
    evaluation = _workspace(run) / "work" / "evaluation"
    try:
        cost = float(json.loads((evaluation / "plugin-eval.json").read_text())["costUsd"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0
    try:
        model = json.loads((evaluation / "gate.json").read_text()).get("model")
    except (OSError, ValueError):
        model = None
    price = _price(model) or _price(run.name) or PRICE_PER_MTOK["haiku"] / 1e6
    return cost / price


def build_cost(run: Path) -> dict:
    out = {c: 0.0 for c in CLASSES} | {"calls": 0, "sessions": 0}
    projects = run / "home" / ".claude" / "projects"
    if projects.is_dir():
        for project in projects.iterdir():
            if not project.is_dir():
                continue
            is_builder = bool(re.search(r"-(ws|work)$", project.name))
            for path in project.rglob("*.jsonl"):
                units, calls = session_units(path)
                cls = "subagents" if "subagents" in path.parts else ("builder" if is_builder else "executors")
                out[cls] += units
                out["calls"] += calls
                out["sessions"] += 1
    out["gate_plugin"] = gate_plugin_units(run)
    out["total"] = sum(out[c] for c in CLASSES)
    return out


def _cell(run_id: str) -> tuple[str, str, str] | None:
    """(prefix, task, model) from ids such as u-t2-factory-v030-haiku-3 or m-t1-bare-sonnet-2."""
    m = re.match(r"^([a-z]+-)(t\d)-(.+)-(sonnet|haiku|opus)-\d+(?:-retry)?$", run_id)
    return (m.group(1), m.group(2) + "|" + m.group(3), m.group(4)) if m else None


def compare(results: dict[str, dict], a: str, b: str, arm_map: dict[str, str]) -> list[dict]:
    rows = []
    cells: dict[tuple[str, str], dict[str, list[float]]] = {}
    for run_id, cost in results.items():
        parsed = _cell(run_id)
        if not parsed:
            continue
        prefix, task_arm, model = parsed
        task, arm = task_arm.split("|", 1)
        if prefix == a and arm in arm_map:
            cells.setdefault((task, model), {}).setdefault("a", []).append(cost["total"])
        elif prefix == b and arm in arm_map.values():
            cells.setdefault((task, model), {}).setdefault("b", []).append(cost["total"])
    for (task, model), arms in sorted(cells.items()):
        if not arms.get("a") or not arms.get("b"):
            continue
        spread = max(arms["b"]) - min(arms["b"])
        med_a, med_b = statistics.median(arms["a"]), statistics.median(arms["b"])
        rows.append({"task": task, "model": model, "n_a": len(arms["a"]), "n_b": len(arms["b"]),
                     "median_a": med_a, "median_b": med_b, "ratio": med_a / med_b if med_b else None,
                     "reference_spread": spread, "bar": med_b + spread, "pass": med_a <= med_b + spread})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--compare", help="PREFIX_A=PREFIX_B, e.g. u-=m-")
    parser.add_argument("--arms", default="factory-v030=factory-v021",
                        help="ARM_A=ARM_B arm names inside the run ids (default factory-v030=factory-v021)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    results = {run.name: build_cost(run) for run in args.runs if run.is_dir()}
    if not results:
        parser.error("no run directories given")
    rows = None
    if args.compare:
        a, b = args.compare.split("=", 1)
        arm_a, arm_b = args.arms.split("=", 1)
        rows = compare(results, a, b, {arm_a: arm_b})
    if args.json:
        print(json.dumps({"builds": results, "compare": rows}, indent=2))
        return 0
    print(f"{'run':34s} {'total':>8s} {'builder':>8s} {'subagent':>8s} {'executor':>8s} {'gate':>6s} {'calls':>6s}")
    for run_id, c in sorted(results.items()):
        print(f"{run_id:34s} {c['total']/1e6:8.2f} {c['builder']/1e6:8.2f} {c['subagents']/1e6:8.2f} "
              f"{c['executors']/1e6:8.2f} {c['gate_plugin']/1e6:6.2f} {c['calls']:6d}")
    if rows is not None:
        print("\ncell          n     median A   median B   ratio   B spread   bar      criterion 17")
        for r in rows:
            print(f"{r['task']} {r['model']:7s} {r['n_a']}/{r['n_b']}  {r['median_a']/1e6:9.2f}M {r['median_b']/1e6:9.2f}M "
                  f"{r['ratio']:6.2f}x {r['reference_spread']/1e6:8.2f}M {r['bar']/1e6:7.2f}M  {'PASS' if r['pass'] else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
