# Panel Synthesis Round

Used only when `check_gate.py --consensus` prints `SYNTHESIS NEEDED on <dimensions>`. You are the same panel member
(`verifier-a`, `verifier-b` or `devils-advocate`) as in round one; [member-rules.md](member-rules.md) still applies.
The orchestrator fills the `{{...}}` inputs from the first-round receipts, with role labels stripped.

```markdown
# Synthesis Round: Disagreement Resolution

The panel scored this artifact independently. Disagreement exists on:
{{disputed_dimensions}}

## Your Scores vs Anonymous Peer Scores
{{score_comparison_table}}

## Anonymous Peer Rationales
{{anonymized_rationales}}

## Task
For each disputed dimension, either:
a) REVISE your score (with justification), OR
b) MAINTAIN your score (with one-sentence rebuttal to the strongest opposing argument)

You may ONLY change scores on disputed dimensions.

## Output (JSON)
{
  "revised_scores": {
    "<dimension>": {
      "original": <N>, "revised": <N>,
      "action": "REVISED|MAINTAINED",
      "justification": "<why>"
    }
  },
  "final_recommendation": "SHIP|SHIP_WITH_CAVEATS|ITERATE|BLOCK"
}
```

A revised score meets the first round's evidence rule (8+ or 3- needs evidence): its backing is its own optional
`"evidence"` (a verbatim quote in double quotes), else your first-round evidence, else its `justification`, and new
text is checked like a quote. Include `veto` (and, for the Devil's Advocate, `escalation`) only to restate or
withdraw it (`null`); omitted, your first round's stands.

**Receipt.** Write your JSON to `<workspace>/work/experiments/panel/<role>.synthesis.scores.json` (not
`<role>.synthesis.json`: the receipt goes there), then run, with your role id:

```bash
python3 <skill-dir>/scripts/check_gate.py --workspace <workspace> --panel-receipt <role> <workspace>/work/experiments/panel/<role>.synthesis.scores.json --synthesis
```

A non-zero exit names what to fix in your JSON; fix it and re-run.
