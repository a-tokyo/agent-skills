# Build brief: `student-aid-index`

This brief answers every factory interview question. Do not ask the user anything —
work non-interactively from this document.

## Purpose (1.1)

Build a skill named `student-aid-index` that turns a **FAFSA submission summary** (`applicant.md`)
into the **federal Student Aid Index result** (`sai.json`) for the **2027–28 award year**: the
student's dependency status, which SAI formula applies, family size, the maximum- and minimum-Pell
indicators, the formula's main intermediate values and the SAI itself.

- Domain: US federal student aid — need analysis as the U.S. Department of Education's Federal
  Student Aid office computes it for 2027–28 applicants
- Target user: an AI agent with a shell and Python 3, asked to compute one applicant at a time
- Success: `sai.json` matches, field for field and dollar for dollar, what Federal Student Aid's own
  processing would return for that applicant

## Gold standards (1.2)

Three pairs in `input/`: `applicant-01.md`..`applicant-03.md` and `result-01.json`..`result-03.json`,
the summary fields of the result processing returned for each. They confirm an implementation; they
are not a specification. The skill must still produce every field of the schema in
`materials/format.md`.

## Study materials (1.3)

- `materials/format.md` — the `applicant.md` format and the `sai.json` schema. It does not restate the
  methodology: the computation rules are the federal methodology published for the 2027–28 award year
  and the federal sources it relies on. The builder is responsible for finding and applying them exactly,
  including every table value, threshold and rounding rule.
- The gold standards above

## Scope and constraints (1.4)

- Every applicant is different: dependent and independent students, married and unmarried parents,
  separate and joint returns, non-filers, any state. Never hard-code anything about the gold applicants.
- Deterministic work (dependency, family size, the formula, tables, rounding, Pell indicators) belongs in
  small bundled script functions; the model's job is reading the summary and orchestrating. No single
  "do everything" function either.
- SKILL.md body under **300 lines**; bundle scripts under `scripts/`; `references/` only if needed
- No integration skills
- Invocation mode: **model-invoked** — the agent should pick it up when asked to compute a Student Aid
  Index, an SAI or Pell eligibility from a FAFSA summary
- Evaluation budget, if applicable: `target_score: 0.85`, `max_iterations: 6`

## Existing skill (1.5)

None. Greenfield.

## Output location

Write the finished skill to `output/student-aid-index/` (SKILL.md, `scripts/`, and `references/` only
if needed) inside the current working directory. When the skill runs, the agent's working directory
contains `applicant.md`, and it must write `sai.json`.
