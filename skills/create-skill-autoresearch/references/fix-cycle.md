# Fix Cycle (5.4)

Read this once, when you open a fix cycle. After the panel's first receipt the skill is frozen: the freeze guard
denies edits to `output/<skill>/` outside an open cycle. A cycle fixes **cited findings** (a receipt's `veto`, or a
failure scenario a member cited with `evidence` and `repro`), whatever the consensus label, and the members who
raised them re-check the fix. At most `max_fix_cycles` (verdict-rules.json) cycles.

`--next` sends you into a cycle when a **blocking** finding holds the verdict down (a metric-first veto; any finding
under ITERATE, BLOCK or ESCALATE) or when you already changed the skill after the panel (the cycle adopts that
change). Any other finding is optional: open a cycle for it only if it is worth the re-check; the freeze guard's
message names the command.

## Steps (`--next` prints each, one exact command)

1. **Open**: `check_gate.py --fix-cycle` (every finding offered) or `--fix-cycle --finding <id>` (repeatable; only
   those). It records the findings (ids like `devils-advocate.veto`, `reviewer.F1`) in
   `work/experiments/panel/fix-cycles/<n>/cycle.json` and writes the notes skeleton `work/experiments/fix-cycle-<n>.md`.
   Adopting a change already made, it declares that change's lines under the findings whose files it touches.
2. **Fix and note**: change only what each finding cites. Under each finding's `## <id>` heading, write the fix in
   one line and every line the fix touches:

   ```markdown
   ## devils-advocate.veto
   Fix: phrases go to `resolve --items items.json`; SKILL.md step 4 no longer builds a shell command from item text.
   lines: SKILL.md:40-44, scripts/po_tools.py:88-131
   ```

   Paths are under `output/<skill>/`; line numbers are the fixed file's; a file the fix adds or deletes is its path
   alone; `lines: none` leaves that finding alone this cycle. A finding with no `lines:` blocks the cycle.
3. **Commit once**: the command `--next` prints. One commit touching `output/<skill>/`, on top of the reviewed
   version (a fix you already committed after the panel is it). A hunk outside every declared range, a second
   commit, uncommitted changes, or any change to `input/` or `work/evaluation/` is refused: `--next` names the line,
   then declare it under the finding it fixes or undo it and re-commit (`git reset --soft <base>`). Never fold in
   unrelated improvements: they are what the cycle forbids.
4. **Rescore and re-gate**: `rescore.py`, then `gate.py`, as `--next` prints them. The gate starts only on a valid
   fix commit.
5. **Re-check**: one subagent per role that raised a finding (lite: the `reviewer`), never you. It reads only
   `references/panel/recheck.md`, gets the fixed skill, the cycle record, your notes and the fix diff, and ends with
   `check_gate.py --recheck-receipt <role> <json>`, bound to the fixed skill's hash.
6. **Verdict**: `--next` prints the lines for `BENCHMARK.md`: the original `PANEL CONSENSUS:` line, a
   `FIX CYCLE <n>:` line (resolved and unresolved ids) and the cycle's `SHIP RULE:`. The verdict never rises above
   the original consensus: metric-first, the gate and metric decide; on the panel-verdict path SHIP and SHIP WITH
   CAVEATS keep their cap, ITERATE, BLOCK or ESCALATE allow at most SHIP WITH CAVEATS. Resolved findings only lift
   vetoes: an unresolved blocking finding, or a regression the re-check names, keeps UNSHIPPED (`--next` offers the
   next cycle on what is left, or the end); an unresolved plain finding changes nothing.

After the re-check, edit nothing in `output/` outside another cycle: a change no cycle takes ends the build UNSHIPPED.
