# Panel Role: Fix-Cycle Re-check (`<role>` as before)

You raised findings on a skill in the panel; the builder fixed them in one commit. Read only this file. You get: the
fixed skill (`output/<skill>/`), the cycle record (`work/experiments/panel/fix-cycles/<n>/cycle.json`: check only
the findings whose `role` is yours), the builder's fix notes (`work/experiments/fix-cycle-<n>.md`) and a `git diff`
command showing the fix commit. You do not re-score the skill and you raise no new finding.

For each of your findings:

1. Re-run its `repro` against the fixed skill, or trace it through the lines the diff touches. **Resolved** only when
   the failure the finding cites can no longer happen, not when the text merely mentions it.
2. Read every touched line (the diff, nothing else) for a **regression**: a failure the fix itself introduced, such
   as a broken command, a lost case, or a new unsafe path. Name it as a concrete scenario, or `null`.
3. Cite the fixed line that resolves it as `evidence`: a verbatim quote in double quotes from `output/<skill>/`
   (whitespace aside), checked like a panel quote. An unresolved finding may leave `evidence` empty.

A finding with a non-null `regression` counts as unresolved and holds the verdict at UNSHIPPED until a later cycle fixes it. A finding the notes mark `lines: none` was left alone: answer `"resolved": false`.

```json
{
  "findings": {
    "<finding id>": { "resolved": true, "evidence": "\"<verbatim quote from the fixed skill>\"", "regression": null }
  }
}
```

**Receipt.** Write the JSON to `<workspace>/work/experiments/panel/fix-cycles/<n>/<role>.recheck.json`, then run:

```bash
python3 <skill-dir>/scripts/check_gate.py --workspace <workspace> --recheck-receipt <role> <that file>
```

A non-zero exit names what to fix in your JSON; fix it and re-run. If you cannot run commands, return your JSON and
the orchestrator runs that command on it verbatim.
