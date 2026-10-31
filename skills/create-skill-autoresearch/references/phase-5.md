# Phase 5.0-5.1: Eval Gate and Premortem

Inputs: `work/evaluation/data-split.yaml` and `output/<skill-name>/`. The panel (SKILL.md 5.2-5.4) follows 5.1 in the
same turn.

## 5.0 Eval Gate

From the workspace root, one command starts the gate as the owned job `gate` and blocks on it for up to 570 s; give
that shell call its longest timeout (Claude Code: `timeout: 600000`):

```bash
python3 <skill-dir>/scripts/gate.py --detach --wait --model <model the skill targets> [--in-sample] \
  [--allow-tools Bash Write] [--executor single-call|agentic]
```

- Exit 124 (still running): re-issue the same command and nothing else; never poll `run_owned.py status`, never
  sleep. 0: valid. Any other exit names what to fix (the suite, `run-case.sh`: phase-3.md 3.6): fix it and re-issue.
  A changed skill, gate input or option starts a new gate; `--restart` re-runs an unchanged one, `--stop` ends it.
  Only `gate.py` writes the artifact.
- Pass `--in-sample` with 3-9 gold standards (the gate cases are training cases; `BENCHMARK.md` and the panel label
  Δ in-sample), and `--executor` on the manual path. Grant only the tools the skill needs. A Claude Code build never
  passes `--backend manual`.
- Every file under `work/evaluation/` (bar `baseline-cache/`) is a gate input: keep scratch and logs, a saved
  `check_gate.py` output included, under `work/experiments/`.
- Smoke-test the suite with `--runs 1` before the full gate: a with-arm score of 0 while `skill-fired` passes means
  the skill fired but could not act; check the tool grants and the fixtures. A 1-run artifact never passes.

Read WITH, W/OUT and Δ per case from the artifact:
- Δ <= 0 on a gold case is a **verified failure scenario** for the panel: the skill adds nothing there, or hurts; on
  a fast-path build it first returns the build to Phase 4 (3.5). It also caps the verdict (5.4).
- A should-trigger indicator that misses any with-arm run, or a near miss that fires in any run, is a description
  defect: the gate is invalid; return to 4.6, not to the panel.
- Pass the JSON summary to the panel as a known risk list and copy it into `BENCHMARK.md`.

## 5.1 Premortem

Run the **premortem skill** on the skill with its present-and-ask step replaced: write the whole risk list to
`work/experiments/premortem.md` and pick no mitigation. Every risk becomes a 5.2 test scenario, with the five craft
failure modes (premature completion, duplication, sediment, sprawl, no-op) as required probes. Then run SKILL.md 5.2
in the same turn, starting with `check_gate.py --panel`.
