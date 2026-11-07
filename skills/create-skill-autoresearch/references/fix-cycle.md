# Fix Cycle (5.4)

Read this once, when `check_gate.py --next` offers a fix cycle: the panel's consensus would end the build UNSHIPPED
on **cited findings** (a receipt's `veto`; on the panel-verdict path also a failure scenario a member cited with
`evidence` and `repro`). Instead of ending, you fix exactly those findings and the members who raised them re-check
the fix. At most `max_fix_cycles` (verdict-rules.json) cycles; past that the build ends UNSHIPPED.

## Steps (`--next` prints each, one exact command)

1. **Open**: `check_gate.py --fix-cycle`. It records the findings (ids like `devils-advocate.veto`,
   `reviewer.F1`) in `work/experiments/panel/fix-cycles/<n>/cycle.json` and writes the notes skeleton
   `work/experiments/fix-cycle-<n>.md`.
2. **Fix and note**: change only what each finding cites. Under each finding's `## <id>` heading, write the fix in
   one line and every line the fix touches:

   ```markdown
   ## devils-advocate.veto
   Fix: phrases go to `resolve --items items.json`; SKILL.md step 4 no longer builds a shell command from item text.
   lines: SKILL.md:40-44, scripts/po_tools.py:88-131
   ```

   Paths are under `output/<skill>/`; line numbers are the fixed file's; a file the fix adds or deletes is its path
   alone. A finding with no `lines:` blocks the cycle.
3. **Commit once**: the command `--next` prints. One commit touching `output/<skill>/`, on top of the reviewed
   version (if you already committed a fix after the panel, that commit is it). A hunk outside every declared range,
   a second commit, uncommitted changes, or any change to `input/` or `work/evaluation/` is refused: `--next` names
   the line, then declare it under the finding it fixes or undo it and re-commit (`git reset --soft <base>`).
   Never fold in unrelated improvements: they are what the cycle forbids.
4. **Rescore and re-gate**: `rescore.py`, then `gate.py`, as `--next` prints them. The gate starts only on a valid
   fix commit.
5. **Re-check**: one subagent per role that raised a finding (lite: the `reviewer`), never you. It reads only
   `references/panel/recheck.md`, gets the fixed skill, the cycle record, your notes and the fix diff, and ends with
   `check_gate.py --recheck-receipt <role> <json>`, bound to the fixed skill's hash.
6. **Verdict**: `--next` prints the lines for `BENCHMARK.md`: the original `PANEL CONSENSUS:` line, a
   `FIX CYCLE <n>:` line (resolved and unresolved ids) and the cycle's `SHIP RULE:`. Resolved findings leave the
   veto; the panel's scores stand. Metric-first: the gate and metric decide. Panel-verdict path: an ITERATE, BLOCK
   or ESCALATE label allows at most SHIP WITH CAVEATS. An unresolved finding (or a regression) keeps UNSHIPPED:
   `--next` offers the next cycle on what is left, or the end.

After the re-check, edit nothing in `output/`: a change ends the build UNSHIPPED.
