# Phase 5.0-5.1: Eval Gate and Premortem

Read on entering Phase 5: run the eval gate, then the premortem. Its inputs: `work/evaluation/data-split.yaml` and
`output/<skill-name>/`; [phase-3.md](phase-3.md) 3.6 only when a gate failure names the suite or `run-case.sh`. The
panel (5.2-5.4, SKILL.md) follows 5.1 in the same turn.

## Contents
- 5.0 Eval Gate (commands, reading the artifact)
- Gate Runner (what `gate.py` does, its flags, what invalidates it)
- 5.1 Premortem

---

## 5.0 Eval Gate

A full gate runs longer than one shell call waits, so one command, from the workspace root, starts it and blocks on
it for up to 570 s; give that shell call its longest timeout (Claude Code: `timeout: 600000`; its default 120 s
sends the call to the background):

```bash
python3 <skill-dir>/scripts/gate.py --detach --wait --model <model the skill targets> [--in-sample] \
  [--allow-tools Bash Write] [--executor single-call|agentic]
```

Exit 124 (still running): re-issue the same command and nothing else — it waits on the running gate, and if the gate
finished in between it reports that result; never poll `run_owned.py status`, never sleep between calls. Any other exit
is the gate's own: 0 valid; non-zero names what to fix — fix it and re-issue (a changed skill or gate input starts a
new gate, as does any other option: gate.json records a hash of every option that changes the run; `--restart` in
place of `--detach` re-runs an unchanged one).

`gate.py` runs as the owned job `gate`, as `run_owned.py start gate -- python3 <skill-dir>/scripts/gate.py …`
would (log `work/pids/gate.log`), and refuses a second start while it runs. To restart the gate, run
`gate.py --restart` with the same options; `gate.py --stop` ends it. It picks the path by the [phase-3.md](phase-3.md) 3.6 rule (a Claude Code build never passes
`--backend manual`; only another host whose shell inherits `$CLAUDECODE` does, plus `--force-manual "<host>: <why>"`; manual runs `run-case.sh` and needs `--executor`), runs every recorded case 3 times per arm, and writes `plugin-eval.json`
or `uplift.json` plus `gate.json` (hashes of skill, cases, scorers, fixtures, artifact) under `work/evaluation/`. Grant only the tools
the skill needs. With 3-9 gold standards pass `--in-sample` (gate cases are training cases; `BENCHMARK.md` and
the panel label Δ in-sample). A non-zero exit says why: fix the suite or `run-case.sh` and re-run; only
`gate.py` writes the artifact. Every file under `work/evaluation/` (bar the gate's no-skill-arm `baseline-cache/`) is a gate input, so keep scratch and logs (saved
`check_gate.py` output included) under `work/experiments/`. Plugin-eval flags: Gate Runner below.

Read WITH, W/OUT and Δ per case from the artifact; the verdict comes from Δ, the same-model uplift the shipping evidence needs ([benchmark-standard.md](benchmark-standard.md)):
- Δ <= 0 on a gate gold-standard case is a **verified failure scenario** for the panel — the skill adds
  nothing there, or hurts; on a fast-path build it first returns the build to Phase 4 ([phase-3.md](phase-3.md) 3.5);
- a should-trigger indicator that misses any with-arm run, or a near miss that fires in any run, is a description
  defect: the gate is invalid; return to 4.6, not to the panel;
- pass the JSON summary to the panel as a known risk list and copy it into `BENCHMARK.md`.

## Gate Runner

`scripts/gate.py` is the only writer of the gate artifact, and it always runs as the owned job `gate`, started and
waited on with the 5.0 commands: a full gate can take over an hour (default plugin-eval timeout 90 minutes).

`--detach` and `--restart` first check
the suite, the recorded cases, each case's `scaffold_script` and (manual mode) `run-case.sh` in the same call,
and exit 3 before starting anything — a failed restart leaves the running gate alone. `gate.py --restart …` stops only the
recorded `gate` group and starts it again, detached. On TERM the gate stops the
eval runs it started (each in its own process group) before it exits, and leaves `gate.json` unfinished, so
a stopped gate never validates. Without `--detach`, `gate.py` starts the job and waits for it, which suits a
human at a terminal; if that call is cut off the job keeps running.

Every run first records the case list as `gate.py record` does (3.6): in plugin mode the case directories
under `evals/` (one level deep, each named like its `case.yaml` `name:`), after
`claude plugin eval . --max-cost-usd 0 --ablation none` loads every case file and stops before the first
run — plugin eval skips a case it cannot load (an unknown grader key, a bad enum, broken grader
frontmatter), so the record step exits 3 naming each one instead; in manual mode the gate gold standards
from the split. In plugin mode it then refreshes `work/evaluation/plugin/skills/<skill-name>/` from
`output/` and runs this from `work/evaluation/plugin/` as its own owned job:

```bash
claude plugin eval . --ablation with-without --runs 3 -j 8 --model <model> --allow-tools Bash Write \
  --scaffold --trust-plugin --no-publish --threshold 0 --json <workspace>/work/evaluation/plugin-eval.json
```

`--scaffold` runs each case's `scaffold_script` (without it the fixtures never arrive), `--allow-tools`
grants the gated tools `prompt.md` lists, `--trust-plugin` answers the first-run trust prompt in unattended
runs, `--threshold 0` keeps a low score from failing the run (the verdict reads Δ, not the exit code), and
`--no-publish` keeps the report local. An interrupted or cost-capped run writes `"partial": true`.

In manual mode it calls `work/evaluation/run-case.sh <case> <with|without>` 3 times per arm per case
(`--jobs` at once, default 8), each run in its own process group with a per-run timeout (default 20
minutes), retries a failed run once, and writes `uplift.json`; a second failure leaves it `"partial": true`
with an `errors` list. Both modes record `work/evaluation/gate.json` (mode, probe exit code, cases, and
sha256 hashes, taken when the run starts and checked again when it ends, of `output/<skill-name>/`, the case
set — names plus each case's `evals/` directory; interpreter caches excluded —, data-split.yaml's splits as
parsed (comments and layout aside), `run-case.sh`, every file under `input/` and every other file under
`work/evaluation/` — scorers, fixtures, golden `*.out` files, dotfiles, logs and the wrapper plugin's manifest
and mocks; not the gate's own outputs, `*.tmp`, `*.prev.json`, `plugin/skills/` or `plugin/evals/results/` —
and, once it finishes, of the artifact itself), so keep scratch and logs (a saved `check_gate.py` output, say)
under `work/experiments/`: one written under `work/evaluation/` during the gate leaves no finished run, and after
it makes the gate stale.
It moves a previous artifact to `<name>.prev.json`
first so a stale file can never pass, and ends by running `check_gate.py`. It exits 0 only when `gate.json`
records a finished run whose hashes still match, and the artifact says `"partial": false`, covers exactly
the recorded cases and has at least 3 runs per arm. Any edit after the gate fails it: "skill changed since
the gate; re-run 5.0" for the skill or suite, "gate inputs changed since the gate; re-run 5.0" for the
adapter, a scorer, a fixture or the artifact. A case is gold when any of its graders is score-bearing (anything but `tool_used` on
`Skill`), even if it also carries a Skill indicator; every gold case needs a numeric Δ, and when
`data-split.yaml` records gold standards a gate with no gold case fails (`no_gold`). Its `GATE VALID:`
line gives the mean Δ and the Δ <= 0 list over gold cases only, then every Skill indicator whatever its
case's class: a should-trigger grader reports how many with-arm runs fired the skill, a near-miss grader
(`max: 0`) how many runs in both arms left it quiet. Indicators gate: one with-arm run where a should-trigger
grader fails, or one run where a near miss fires, makes the gate invalid (`trigger_failed`, `near_miss_fired`:
a description defect, back to 4.6). A should-trigger prompt that also grades output is therefore gold: its Δ
counts (Δ <= 0 caps the verdict, 5.4) and its indicator gates too. Smoke-test the suite with `gate.py --runs 1` before the full gate: a with-arm score
of 0 while `skill-fired` passes means the skill fired but could not act — check the tool grants and the
fixtures first. The 1-run artifact never passes `check_gate.py`; the full run replaces it.

## 5.1 Premortem

Run the **premortem skill** on the skill artifact with its present-and-ask step replaced: write the whole risk list to `work/experiments/premortem.md` and pick no mitigation — every risk becomes a 5.2 test scenario, with the five craft failure modes (premature completion, duplication, sediment, sprawl, no-op) as required probes. Then run SKILL.md 5.2 in the same turn, starting with `check_gate.py --panel`.
