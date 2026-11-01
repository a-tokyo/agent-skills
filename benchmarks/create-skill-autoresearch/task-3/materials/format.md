# `applicant.md` and `sai.json`

This document fixes the file formats only. It does not describe how the SAI is computed: that is the
federal methodology for the 2027–28 award year.

## `applicant.md`

A FAFSA submission summary: a title, an `Applicant:` line, then up to four sections, each a list of
`- Label: value` lines.

| Section | Present when |
|---------|--------------|
| `## Student` | always |
| `## Student's spouse` | the student reports a spouse |
| `## Parent` | the student answered the parent questions |
| `## Parent's spouse or partner` | the parent reports a spouse or partner |

Money is written `$12,345` or `-$1,200`; a missing money line means $0. Yes/no answers are `Yes` or `No`.

**Student (dependency questions):** `Date of birth` (YYYY-MM-DD), `Marital status` (Single, Married,
Remarried, Separated, Divorced, Widowed), `Enrolled in a master's or doctoral program in 2027-28`,
`Active duty in the U.S. Armed Forces`, `Veteran of the U.S. Armed Forces`,
`Has children or other dependents who get more than half their support from the student`,
`Parents deceased, in foster care, or ward of the court since age 13`,
`Emancipated minor or in legal guardianship (court-determined)`, `Homelessness determination`
(`None`, or who made the determination and its date), `State of residence`.

**Parent only:** `Parents' marital status` (Married, Remarried, Unmarried and both legal parents living
together, Single, Divorced, Separated, Widowed), `State of residence`.

**Family size:** `Family size reported on the FAFSA` appears in the Student section (independent
student) or the Parent section (dependent student) only when the applicant reported one.

**Tax return (any person):** `Filed a 2025 federal income tax return`. When `Yes`, these follow, all
from that person's 2025 return as the IRS transferred it: `Tax filing status` (Single, Married filing
jointly, Married filing separately, Head of household, Qualifying surviving spouse),
`Adjusted gross income`, `Income earned from work`, `Income tax paid`, `Exemptions on tax return`
(the IRS count of everyone claimed on the return, filers included), `Deductible payments to
IRA/Keogh/other`, `Tax-exempt interest income`, `Untaxed portions of IRA distributions`,
`IRA rollover`, `Untaxed portions of pensions`, `Pension rollover`, `Foreign income exclusion`,
`Taxable college grant and scholarship aid`, `Education credits`, `Federal work-study`,
`Received earned income tax credit`.

A spouse or partner section that only says `- Included on the joint return above: Yes` filed jointly with
the person above; their figures are already in that joint return.

**Assets (Student and Parent sections):** `Cash, savings and checking`, `Net worth of current
investments`, `Net worth of businesses and/or farms`, `Annual child support received`, as reported on
the FAFSA. They cover the spouse or partner too.

## `sai.json`

One JSON object, exactly these keys:

| Key | Value |
|-----|-------|
| `dependency` | `"dependent"` or `"independent"` |
| `formula` | `"A"`, `"B"` or `"C"`; `null` when the SAI is assigned without running a formula |
| `family_size` | integer used in the calculation; `null` when none is calculated |
| `max_pell_indicator` | integer maximum-Pell indicator, or `null` |
| `min_pell_indicator` | integer minimum-Pell indicator, or `null`; always `null` when `max_pell_indicator` is set |
| `income_protection_allowance` | integer IPA the formula uses (parents' in A; the student's, with spouse, in B and C) |
| `payroll_tax_allowance` | integer total payroll tax allowance (HI + OASDI) of the parents (A) or student and spouse (B, C) |
| `available_income` | integer: parents' available income (A) or student available income (B, C) |
| `contribution_from_assets` | integer: parents' contribution from assets (A) or student contribution from assets (B, C) |
| `contribution` | integer: parents' contribution (A), student's contribution from income (B), or total student contribution from adjusted available income (C) |
| `sai` | integer final Student Aid Index |

Every intermediate key is `null` when `formula` is `null`. Example:

```json
{"dependency": "dependent", "formula": "A", "family_size": 4, "max_pell_indicator": null,
 "min_pell_indicator": 2, "income_protection_allowance": 0, "payroll_tax_allowance": 0,
 "available_income": 0, "contribution_from_assets": 0, "contribution": 0, "sai": 0}
```

(The example shows the shape only; its numbers are placeholders.)
