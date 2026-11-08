# Phase 5.0-5.1: Eval Gate and Premortem

Inputs: `work/evaluation/data-split.yaml` and `output/<skill-name>/`.

## 5.0 Eval Gate

Run the `gate.py --detach --wait` command `check_gate.py --next` prints, from the workspace root, as **one
foreground shell call**: it starts the owned job `gate` and blocks up to 270 s (most gates end sooner). Claude
Code: give the Bash call `timeout: 600000` (the 120000 default cuts it off), never `run_in_background` or a smaller
`--wait-timeout`; elsewhere keep `--wait-timeout` under the shell tool's limit.

```bash
python3 <skill-dir>/scripts/gate.py --detach --wait --model <model> [options]   # as --next prints it
```

- Exit 124 (still running): re-issue the same command; never sleep, poll or restart. 0: valid. Any other exit names
  what to fix (the suite, `run-case.sh`: phase-3.md 3.6): fix it, then run what `--next` prints. A changed skill,
  gate input or option starts a new gate, so start it only when Phase 4 and any rescore are done; after the panel,
  only a fix cycle's commit (5.4). Only `gate.py` writes the artifact.
- Pass `--in-sample` with 3-9 gold standards (the gate cases are training cases; `BENCHMARK.md` and the panel label
  Δ in-sample), and `--executor` on the manual path. Grant only the tools the skill needs. A Claude Code build never
  passes `--backend manual`.
- Every file under `work/evaluation/` (bar `baseline-cache/`) is a gate input: keep scratch and logs, a saved
  `check_gate.py` output included, under `work/experiments/`.
- No `--runs 1` smoke gate first (r18: 17 extra gate runs); the load check catches a broken case file. A with-arm
  score of 0 while `skill-fired` passes means the skill fired but could not act: check the tool grants and fixtures.

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
