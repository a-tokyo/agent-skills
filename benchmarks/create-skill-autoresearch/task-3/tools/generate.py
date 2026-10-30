#!/usr/bin/env python3
"""Deterministic generator for task-3 (student-aid-index). Never shown to any arm.

Usage: generate.py   (writes ../input and ../holdout; idempotent)

Applicants are hand-specified records rendered to the FAFSA-summary format in ../materials/format.md.
After writing, it exits non-zero unless:
  - the solver reproduces the published Formula A exemplar of the 2027-28 guide line by line,
  - the prior edition (--edition 2627) gets the SAI of every gold applicant wrong (gold cannot be
    met from a stale recollection, so a builder has to research),
  - every naive clause in solve.NAIVE changes the score of at least one holdout, and at least
    MIN_UNTAUGHT of them change no gold SAI (gold confirms the tables, it does not teach the clauses).
Sources and clause map: ../NOTES.md.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from solve import NAIVE, solve

HERE = Path(__file__).resolve().parent
TASK = HERE.parent
MIN_UNTAUGHT = 6

DEP_NO = {
    "Enrolled in a master's or doctoral program in 2027-28": "No",
    "Active duty in the U.S. Armed Forces": "No",
    "Veteran of the U.S. Armed Forces": "No",
    "Has children or other dependents who get more than half their support from the student": "No",
    "Parents deceased, in foster care, or ward of the court since age 13": "No",
    "Emancipated minor or in legal guardianship (court-determined)": "No",
    "Homelessness determination": "None",
}
TAX_FIELDS = ["Tax filing status", "Adjusted gross income", "Income earned from work", "Income tax paid",
              "Exemptions on tax return", "Deductible payments to IRA/Keogh/other", "Tax-exempt interest income",
              "Untaxed portions of IRA distributions", "IRA rollover", "Untaxed portions of pensions", "Pension rollover",
              "Foreign income exclusion", "Taxable college grant and scholarship aid", "Education credits",
              "Federal work-study", "Received earned income tax credit"]
ASSET_FIELDS = ["Cash, savings and checking", "Net worth of current investments",
                "Net worth of businesses and/or farms", "Annual child support received"]


def fmt(v):
    if isinstance(v, int):
        return f"-${-v:,}" if v < 0 else f"${v:,}"
    return str(v)


def person(title: str, d: dict, assets: bool) -> list[str]:
    lines = [f"## {title}", ""]
    head = [k for k in d if k not in TAX_FIELDS and k not in ASSET_FIELDS]
    for k in head:
        lines.append(f"- {k}: {fmt(d[k])}")
    if "Filed a 2025 federal income tax return" in d and d["Filed a 2025 federal income tax return"] == "Yes":
        for k in TAX_FIELDS:
            v = d.get(k, "No" if k == "Received earned income tax credit" else 0)
            if k == "Exemptions on tax return":
                lines.append(f"- {k}: {v}")
            else:
                lines.append(f"- {k}: {fmt(v)}")
    if assets:
        for k in ASSET_FIELDS:
            lines.append(f"- {k}: {fmt(d.get(k, 0))}")
    return lines + [""]


def render(case: dict) -> str:
    out = [f"# FAFSA submission summary, 2027-28 award year", "", f"Applicant: {case['id']}", ""]
    first = {k: case["student"][k] for k in ("Date of birth", "Marital status") if k in case["student"]}
    stu = {**first, **DEP_NO, **case["student"]}
    out += person("Student", stu, assets=True)
    if "spouse" in case:
        out += person("Student's spouse", case["spouse"], assets=False)
    if "parent" in case:
        out += person("Parent", case["parent"], assets=True)
    if "parent_spouse" in case:
        out += person("Parent's spouse or partner", case["parent_spouse"], assets=False)
    return "\n".join(out).rstrip() + "\n"


def filer(status, agi, iefw, tax, ex, **kw):
    return {"Filed a 2025 federal income tax return": "Yes", "Tax filing status": status, "Adjusted gross income": agi,
            "Income earned from work": iefw, "Income tax paid": tax, "Exemptions on tax return": ex, **kw}


NONFILER = {"Filed a 2025 federal income tax return": "No"}
JOINT = {"Included on the joint return above": "Yes"}

# The guide's Formula A exemplar (2027-28 guide, Appendix K, Tables 10-11), as an applicant record.
EXEMPLAR = {
    "id": "EXEMPLAR",
    "student": {"Date of birth": "2008-05-01", "Marital status": "Single", "State of residence": "Ohio",
                **filer("Single", 5723, 6755, 0, 0)},
    "parent": {"Parents' marital status": "Married", "State of residence": "Ohio", "Family size reported on the FAFSA": 7,
               **filer("Single", 86334, 95456, 28334, 3, **{"Untaxed portions of IRA distributions": 2000, "IRA rollover": 1000}),
               "Cash, savings and checking": 24789, "Net worth of current investments": 48574},
    "parent_spouse": filer("Qualifying surviving spouse", 79998, 85002, 11992, 3,
                           **{"Untaxed portions of IRA distributions": 1000, "IRA rollover": 2000}),
}
EXEMPLAR_EXPECT = {"income_protection_allowance": 71550, "payroll_tax_allowance": 2617 + 11188, "available_income": 36451,
                   "contribution_from_assets": 8804, "contribution": 12198, "sai": 12198, "family_size": 7, "formula": "A"}

GOLD = [
    {   # dependent, unmarried single parent (HoH) in Hawaii; low AGI -> max Pell 2, SAI floored at 0 from positive
        "id": "G-1",
        "student": {"Date of birth": "2008-02-11", "Marital status": "Single", "State of residence": "Hawaii",
                    **filer("Single", 7400, 7400, 0, 0), "Cash, savings and checking": 1800},
        "parent": {"Parents' marital status": "Divorced", "State of residence": "Hawaii",
                   **filer("Head of household", 67500, 69000, 3300, 3), "Cash, savings and checking": 9500,
                   "Net worth of current investments": 4000, "Annual child support received": 6000},
    },
    {   # dependent, married parents on a joint return; student works; full Formula A
        "id": "G-2",
        "student": {"Date of birth": "2007-09-30", "Marital status": "Single", "State of residence": "Texas",
                    **filer("Single", 16850, 16850, 610, 0), "Cash, savings and checking": 5200,
                    "Net worth of current investments": 1500},
        "parent": {"Parents' marital status": "Married", "State of residence": "Texas",
                   **filer("Married filing jointly", 118400, 112000, 9800, 5,
                           **{"Deductible payments to IRA/Keogh/other": 6000, "Education credits": 1500}),
                   "Cash, savings and checking": 21000, "Net worth of current investments": 38000},
        "parent_spouse": JOINT,
    },
    {   # independent: married, joint return, one child -> Formula C, minimum Pell 4
        "id": "G-3",
        "student": {"Date of birth": "1999-04-17", "Marital status": "Married", "State of residence": "Oregon",
                    **{k: "Yes" if k.startswith("Has children") else v for k, v in DEP_NO.items()},
                    **filer("Married filing jointly", 78600, 81000, 4100, 3), "Cash, savings and checking": 7400,
                    "Net worth of current investments": 12000},
        "spouse": JOINT,
    },
]

HOLDOUT = [
    {   # born 1 Jan 2004 (dependent, not "before"); married parents on separate returns with different statuses
        # (each filer's own rates);
        # business net worth in band 2; negative foreign income exclusion; student earns over the student IPA
        "id": "H-1",
        "student": {"Date of birth": "2004-01-01", "Marital status": "Single", "State of residence": "Colorado",
                    **filer("Single", 21400, 22900, 1180, 0), "Cash, savings and checking": 3100},
        "parent": {"Parents' marital status": "Married", "State of residence": "Colorado",
                   **filer("Married filing separately", 38900, 41250, 2350, 0,
                           **{"Untaxed portions of pensions": 2500, "Pension rollover": 4000,
                              "Untaxed portions of IRA distributions": 1800}),
                   "Cash, savings and checking": 15800, "Net worth of current investments": 22500,
                   "Net worth of businesses and/or farms": 260000},
        "parent_spouse": filer("Head of household", 141300, 152000, 21900, 3,
                               **{"Foreign income exclusion": -8400, "Untaxed portions of IRA distributions": 3000,
                                  "IRA rollover": 3000, "Untaxed portions of pensions": 3000}),
    },
    {   # independent, married, separate returns; low income -> max Pell 3, negative SAI kept (not floored at 0)
        "id": "H-2",
        "student": {"Date of birth": "2005-06-02", "Marital status": "Married", "State of residence": "Alaska",
                    **filer("Married filing separately", 14903, 15600, 0, 1, **{"Federal work-study": 2400}),
                    "Cash, savings and checking": 41000},
        "spouse": filer("Married filing separately", 18300, 19100, 240, 1),
    },
    {   # independent single parent (HoH) of two; above 225% poverty -> no max Pell; min Pell 3 (<= 400%);
        # self-reported family size overrides exemptions; Formula C with 7% asset rate and EEA cap
        "id": "H-3",
        "student": {"Date of birth": "1994-11-23", "Marital status": "Divorced", "State of residence": "Hawaii",
                    "Family size reported on the FAFSA": 3,
                    **{k: "Yes" if k.startswith("Has children") else v for k, v in DEP_NO.items()},
                    **filer("Head of household", 88731, 91500, 6400, 2), "Cash, savings and checking": 18000,
                    "Net worth of current investments": 26000, "Annual child support received": 7200},
    },
    {   # separated student (still dependent); parents unmarried and living together, both non-filers -> SAI -1500
        "id": "H-4",
        "student": {"Date of birth": "2005-03-09", "Marital status": "Separated", "State of residence": "Georgia",
                    **filer("Single", 9800, 9800, 0, 0), "Cash, savings and checking": 12500},
        "parent": {"Parents' marital status": "Unmarried and both legal parents living together",
                   "State of residence": "Georgia", **NONFILER, "Cash, savings and checking": 2000},
        "parent_spouse": NONFILER,
    },
    {   # homeless determination before 1 July 2026 does not make the student independent; parents on a joint
        # return with two exemptions (assumed family size 3); joint OASDI base above the single base; negative AGI
        # student; parents' AAI in the top band
        "id": "H-5",
        "student": {"Date of birth": "2006-12-14", "Marital status": "Single", "State of residence": "Illinois",
                    "Homelessness determination": "Determined at risk of homelessness by the high school district on 2026-04-20",
                    **filer("Single", -1200, 0, 0, 0), "Cash, savings and checking": 400},
        "parent": {"Parents' marital status": "Remarried", "State of residence": "Illinois",
                   **filer("Married filing jointly", 238000, 246000, 41200, 2, **{"Tax-exempt interest income": 1300}),
                   "Cash, savings and checking": 30500, "Net worth of current investments": 64000},
        "parent_spouse": JOINT,
    },
    {   # parent's AGI is negative (business loss): no max Pell (AGI must be > 0) but min Pell 1; negative parents'
        # AAI becomes an allowance against the student's income
        "id": "H-6",
        "student": {"Date of birth": "2006-07-28", "Marital status": "Single", "State of residence": "Maine",
                    **filer("Single", 31800, 31800, 1900, 0), "Cash, savings and checking": 2600},
        "parent": {"Parents' marital status": "Widowed", "State of residence": "Maine",
                   **filer("Qualifying surviving spouse", -4700, 0, 0, 3), "Cash, savings and checking": 3900,
                   "Net worth of businesses and/or farms": 15000},
    },
]

GOLD_FIELDS = ["dependency", "formula", "family_size", "max_pell_indicator", "min_pell_indicator", "sai"]


def score(got: dict, want: dict) -> float:
    keys = list(want)
    return sum(got.get(k) == want[k] for k in keys) / len(keys)


def main() -> int:
    fails = []
    ex = solve(render(EXEMPLAR))
    for k, v in EXEMPLAR_EXPECT.items():
        if ex[k] != v:
            fails.append(f"exemplar {k}: {ex[k]} != {v}")

    inp, hold = TASK / "input", TASK / "holdout"
    for d in (inp, hold):
        for f in d.glob("*"):
            f.unlink()
    gold_texts = {}
    for i, case in enumerate(GOLD, 1):
        text = render(case)
        gold_texts[case["id"]] = text
        (inp / f"applicant-{i:02d}.md").write_text(text)
        full = solve(text)
        (inp / f"result-{i:02d}.json").write_text(json.dumps({k: full[k] for k in GOLD_FIELDS}, indent=2) + "\n")
        stale = solve(text, "2627")
        if stale["sai"] == full["sai"]:
            fails.append(f"{case['id']}: prior edition reproduces the gold SAI")
    key, hold_texts = {}, {}
    for i, case in enumerate(HOLDOUT, 1):
        text = render(case)
        hold_texts[case["id"]] = text
        name = f"applicant-h{i}.md"
        (hold / name).write_text(text)
        key[case["id"]] = {"input": name, "expected": solve(text)}
    (hold / "answer-key.json").write_text(json.dumps(key, indent=2) + "\n")

    untaught = []
    for n in NAIVE:
        hit = [h for h, t in hold_texts.items() if score(solve(t, naive={n}), key[h]["expected"]) < 1]
        gold_hit = [g for g, t in gold_texts.items() if solve(t, naive={n})["sai"] != solve(t)["sai"]]
        print(f"# naive {n:22s} holdouts={hit} gold={gold_hit}")
        if not hit:
            fails.append(f"naive {n} changes no holdout score")
        if not gold_hit:
            untaught.append(n)
    if len(untaught) < MIN_UNTAUGHT:
        fails.append(f"only {len(untaught)} clauses untaught by gold (< {MIN_UNTAUGHT})")
    stale = [score(solve(t, "2627"), key[h]["expected"]) for h, t in hold_texts.items()]
    allnaive = [score(solve(t, naive=set(NAIVE)), key[h]["expected"]) for h, t in hold_texts.items()]
    print(f"# stale edition holdout field score: {[round(s, 2) for s in stale]} mean={sum(stale) / len(stale):.3f}")
    print(f"# all-naive holdout field score:    {[round(s, 2) for s in allnaive]} mean={sum(allnaive) / len(allnaive):.3f}")
    for h, c in key.items():
        print(f"# {h}: {json.dumps(c['expected'])}")
    if fails:
        print("GENERATE FAIL\n- " + "\n- ".join(fails))
        return 1
    print("GENERATE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
