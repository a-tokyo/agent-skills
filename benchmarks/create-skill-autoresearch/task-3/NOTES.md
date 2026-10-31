# task-3 private notes (never copied to a builder or executor)

`arms/run-arm.sh` copies only `brief.md`, `input/` and `materials/`. This file, `tools/` and `holdout/`
stay out of every run.

## The standard

- **U.S. Department of Education, Federal Student Aid, *2027–28 Student Aid Index (SAI) and Pell Grant
  Eligibility Guide*, Version 1, June 2026** (announced in Electronic Announcement APP-26-08,
  12 June 2026). Public domain (a US federal work). Official copy:
  `https://fsapartners.ed.gov/sites/default/files/2026-06/2728StudentAidIndexSAIandPellGrantEligibilityGuide.pdf`
  (it returns 403 to plain `curl`). The mirror NCAN hosts was used to build the solver:
  `https://www.ncan.org/common/Uploaded files/FAFSA/2027-28/2027-2028 Student Aid Index and Pell Grant Eligibility Guide.pdf`.
- **HHS 2025 Poverty Guidelines**, 90 FR (2025-01377, 17 January 2025), which the guide's Step 1 tells
  you to use ("prior-prior tax year").
- Stale baseline (`solve.py --edition 2627`): the *2026–27* guide, Version 1.1, August 2025, with the
  2024 poverty guidelines (89 FR, 2024-00796) it references.

## Why prior knowledge is weak (pre-flight, 2026-10-30)

Both builder models were asked twelve 2027–28 SAI parameters from memory, with no tools
(`claude -p --tools ""`). The prompt and answers are kept with the run evidence, not in the repo.

- **Haiku 5.5** said Formulas A, B and C "no longer exist" (wrong: they are the 2027–28 formulas) and
  declined every table value.
- **Sonnet 5.5** declined most values. Where it did answer, it gave an employment expense allowance cap
  of $4,000 (actual $5,200), an AAI floor of -$1,500 (the contribution floor is -$1,958; -1,500 is the SAI
  clamp), the **2026** poverty guidelines (the guide uses 2025) and a 20% Formula C asset rate (actual 7%).
- Control: the same models recited tax-year-2025 federal income tax rules (OBBBA deductions, Tax Table
  mechanics, age-65 cutoff) correctly from memory, 11–12 of 12. That is why tax was rejected as the domain.

## Clause map (2027–28 guide), and which applicant exercises it

| Clause | Where | Applicants |
|--------|-------|------------|
| Independent if born before 1 Jan 2004 | App. B | H-1 (born 2004-01-01: dependent) |
| Married, not separated | App. B | H-4 (separated: dependent), H-2 |
| Homeless determination on or after 1 Jul 2026 | App. B | H-5 (April 2026: dependent; 2026–27 says 1 Jul 2025) |
| Max Pell 1: all contributors non-filers -> SAI -1500, no formula/family size | Step 1 | H-4 |
| Max Pell 2/3: 0 < AGI+FEI <= 225% / 175% of poverty, threshold rounded first; SAI = min(calc, 0) | Step 1 | G-1, H-2, H-6 (AGI < 0: none) |
| 2025 HHS guidelines by state (AK, HI, other) | Step 1 | G-1, H-2, H-3 |
| Min Pell 1–5 (325/275/400/350/275%) | Step 3 | G-3 (4), H-3 (3), H-6 (1) |
| Round to 3 decimals then to whole, .5 away from zero | Notes, App. K | H-2, H-3 |
| Payroll: each filer at their own filing status (new in 2027–28; 2026–27 used the lead filer's) | Table A1 note | H-1 |
| Payroll: MFJ HI 250k, MFJ OASDI base 352,200; MFS HI 125k; wage base 176,100 (TY2025) | A1/B1/C1 | H-1, H-5 |
| Untaxed IRA/pension minus rollover floored per person | lines d, e; exemplar | H-1 |
| Foreign income exclusion entered as absolute value | line f | H-1 |
| Family size: exemptions, assumed minimum (3/2/1), self-reported wins | App. C | H-3, H-5 |
| Single parent: unmarried + HoH/QSS/EITC; independent also needs dependents | App. G | G-1, H-3, H-6 |
| Formula A IPA (A2), EEA min(35%, 5,200), student IPA 12,220, asset rates 12%/20%, A3, A5 | Formula A | G-1, G-2, H-1, H-5, H-6 |
| Line 26: negative parents' AAI becomes a student allowance | Formula A | H-6 |
| Formula B IPA 19,000/30,470, EEA only if married, contribution from income not floored | Formula B | H-2 |
| Formula C IPA (C2/C3), 7% asset rate, C6 | Formula C | G-3, H-3 |
| SAI clamp -1,500 | Notes | H-2 |

`tools/generate.py` checks the solver against the guide's own Formula A exemplar (Appendix K, Tables
10–11: IPA 71,550, PAI 36,451, PCA 8,804, SAI 12,198) before writing anything.

## Format conventions that are ours, not the guide's

- `min_pell_indicator` is reported only when no maximum-Pell indicator applies.
- A joint return is written once, under the first filer; the spouse section says it is included.
- Non-filers report no income; assets are always reported (the asset-exemption rules of the FAFSA
  Specifications Guide, Vol. 5, are out of scope).
- "Student is a parent" for minimum-Pell indicator 4 is read as the Has Dependents indicator.
