# Gate Runner

What `scripts/gate.py` does, records and hashes. Read it only when a gate's exit message (phase-5.md 5.0) does not say
what to fix; the 5.0 commands and rules are in phase-5.md.

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
counts (on either 5.4 path, Δ <= 0 caps the verdict at SHIP WITH CAVEATS, below -`caveat_margin` at `UNSHIPPED`) and its indicator gates too. Smoke-test the suite with `gate.py --runs 1` before the full gate: a with-arm score
of 0 while `skill-fired` passes means the skill fired but could not act — check the tool grants and the
fixtures first. The 1-run artifact never passes `check_gate.py`; the full run replaces it.
