---
name: student-aid-index
description: Computes the federal Student Aid Index (SAI) and Pell indicators from a FAFSA submission summary. Use when asked to compute an SAI, a Student Aid Index or Pell eligibility for an applicant.
---

# Student Aid Index

Run the bundled tool from the directory holding `applicant.md`:

```bash
python3 ~/.claude/skills/student-aid-index/scripts/sai_tools.py applicant.md --edition 2627 > sai.json
```

`scripts/sai_tools.py` parses the summary and applies last year's federal methodology (stale baseline): dependency, family size, Pell
indicators and Formula A, B or C. Check that `sai.json` is valid JSON before finishing.
