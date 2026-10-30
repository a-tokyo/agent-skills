#!/usr/bin/env python3
"""Reference solver for task-3 (student-aid-index). Never shown to any arm.

Usage: solve.py <applicant.md> [--edition 2728|2627] [--naive a,b,...]   (prints sai.json to stdout)

Implements the federal need-analysis methodology as published for the 2027-28 award year; the clause
map and sources are in ../NOTES.md. `--edition 2627` swaps in the prior edition's parameters and rules
(the stale baseline). `--naive` switches individual clauses to the plausible-but-wrong reading listed
in NAIVE (the generator uses them to prove which clauses gold can and cannot teach).
"""
from __future__ import annotations

import json
import re
import sys
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from pathlib import Path

D = Decimal

EDITIONS = {
    "2728": {
        "ipa_a": {2: 30300, 3: 37720, 4: 46590, 5: 54970, 6: 64290}, "ipa_a_step": 7260,
        "ipa_dep_student": 12220, "ipa_b": (19000, 30470),
        "ipa_c_married": {3: 59930, 4: 74000, 5: 87320, 6: 102120}, "ipa_c_married_step": 11530,
        "ipa_c_single": {2: 57050, 3: 71040, 4: 87700, 5: 103500, 6: 121030}, "ipa_c_single_step": 13680,
        "eea_cap": 5200,
        "bfnw": [(180000, 0, D("0.40")), (540000, 72000, D("0.50")), (905000, 252000, D("0.60")), (None, 471000, D("1"))],
        "aai": (-8900, -1958, [(22600, 0, D("0.22")), (28300, 4972, D("0.25")), (34000, 6397, D("0.29")),
                               (39900, 8050, D("0.34")), (45600, 10056, D("0.40")), (None, 12336, D("0.47"))]),
        "oasdi_base": 176100, "oasdi_base_mfj": 352200,
        # HHS poverty guidelines, prior-prior year (2025): base for 1 person, increment per extra person
        "poverty": {"other": (15650, 5500), "alaska": (19550, 6880), "hawaii": (17990, 6330)},
        "born_before": "2004-01-01", "homeless_on_or_after": "2026-07-01",
        "payroll_own_status": True,
    },
    "2627": {
        "ipa_a": {2: 29190, 3: 36330, 4: 44880, 5: 52950, 6: 61930}, "ipa_a_step": 6990,
        "ipa_dep_student": 11770, "ipa_b": (18310, 29350),
        "ipa_c_married": {3: 57730, 4: 71280, 5: 84120, 6: 98370}, "ipa_c_married_step": 11110,
        "ipa_c_single": {2: 54950, 3: 68430, 4: 84480, 5: 99700, 6: 116590}, "ipa_c_single_step": 13180,
        "eea_cap": 5000,
        "bfnw": [(175000, 0, D("0.40")), (520000, 70000, D("0.50")), (870000, 242500, D("0.60")), (None, 452500, D("1"))],
        "aai": (-8500, -1870, [(21800, 0, D("0.22")), (27300, 4796, D("0.25")), (32800, 6171, D("0.29")),
                               (38400, 7766, D("0.34")), (43900, 9670, D("0.40")), (None, 11870, D("0.47"))]),
        "oasdi_base": 168600, "oasdi_base_mfj": 337200,
        "poverty": {"other": (15060, 5380), "alaska": (18810, 6730), "hawaii": (17310, 6190)},
        "born_before": "2003-01-01", "homeless_on_or_after": "2025-07-01",
        # 2026-27 Table A1 note: the spouse's HI/OASDI use the parent's (or student's) filing-status rates
        "payroll_own_status": False,
    },
}

NAIVE = {
    "half_even": "round .5 to even instead of away from zero",
    "ira_floor_combined": "floor untaxed IRA/pension minus rollover at zero on the couple's total, not per person",
    "fei_signed": "add a negative foreign income exclusion as negative instead of its absolute value",
    "oasdi_per_person": "cap a joint return's OASDI at the single-earner wage base instead of the joint base",
    "b_clamp": "floor a Formula B student contribution from income at zero",
    "no_paai_allowance": "omit the allowance for parents' negative adjusted available income (A line 26)",
    "agi_nonpositive_max": "treat AGI <= 0 as within the maximum-Pell poverty threshold",
    "no_assumed_family": "use the exemption sum without the assumed minimum family size",
    "calc_over_reported": "prefer the calculated family size to the self-reported one",
    "separated_married": "treat a separated student as married",
    "max_pell_keeps_sai": "keep a positive calculated SAI for a maximum-Pell (indicator 2/3) applicant",
}

MONEY = ["Adjusted gross income", "Income earned from work", "Income tax paid", "Deductible payments to IRA/Keogh/other",
         "Tax-exempt interest income", "Untaxed portions of IRA distributions", "IRA rollover",
         "Untaxed portions of pensions", "Pension rollover", "Foreign income exclusion",
         "Taxable college grant and scholarship aid", "Education credits", "Federal work-study",
         "Cash, savings and checking", "Net worth of current investments", "Net worth of businesses and/or farms",
         "Annual child support received"]


# ---------------------------------------------------------------- parsing
def parse(text: str) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    cur = None
    for line in text.splitlines():
        h = re.match(r"^##\s+(.*?)\s*$", line)
        if h:
            cur = h.group(1).lower()
            sections[cur] = {}
            continue
        kv = re.match(r"^\s*-\s+([^:]+):\s*(.*?)\s*$", line)
        if kv and cur is not None:
            sections[cur][kv.group(1).strip()] = kv.group(2).strip()
    return sections


def money(v: str | None) -> int:
    if v is None or v == "" or v.lower() in ("none", "n/a", "-"):
        return 0
    neg = v.strip().startswith("-") or v.strip().startswith("(")
    n = int(re.sub(r"[^0-9]", "", v) or 0)
    return -n if neg else n


def yes(v: str | None) -> bool:
    return (v or "").strip().lower().startswith("y")


class Person:
    def __init__(self, sec: dict[str, str] | None):
        self.present = sec is not None and not yes((sec or {}).get("Included on the joint return above"))
        sec = sec if self.present else {}
        self.sec = sec
        self.filed = self.present and yes(sec.get("Filed a 2025 federal income tax return"))
        self.status = (sec.get("Tax filing status") or "").lower() if self.filed else ""
        self.m = {k: (money(sec.get(k)) if (self.filed or k in MONEY[13:]) else 0) for k in MONEY}
        self.exemptions = int(money(sec.get("Exemptions on tax return"))) if self.filed else 0
        self.eitc = yes(sec.get("Received earned income tax credit"))

    def __getitem__(self, k: str) -> int:
        return self.m[k]

    @property
    def mfj(self) -> bool:
        return self.status == "married filing jointly"


# ---------------------------------------------------------------- arithmetic
class Calc:
    def __init__(self, ed: dict, naive: set[str]):
        self.ed, self.naive = ed, naive
        self.mode = ROUND_HALF_EVEN if "half_even" in naive else ROUND_HALF_UP  # HALF_UP = away from zero

    def r3(self, x: Decimal) -> Decimal:
        return x.quantize(D("0.001"), rounding=self.mode)

    def r(self, x: Decimal) -> int:
        return int(self.r3(D(x)).quantize(D("1"), rounding=self.mode))

    def table(self, value: int, bands) -> int:
        lower = 0
        for upper, base, rate in bands:
            if upper is None or value <= upper:
                return self.r(D(base) + rate * (value - lower))
            lower = upper
        raise AssertionError

    def bfnw(self, nw: int) -> int:
        return 0 if nw < 1 else self.table(nw, self.ed["bfnw"])

    def aai(self, aai: int) -> int:
        floor_at, floor_val, bands = self.ed["aai"]
        if aai < floor_at:
            return floor_val
        if aai <= bands[0][0]:
            return self.r(bands[0][2] * aai)
        return self._aai_upper(aai, bands)

    def _aai_upper(self, aai: int, bands) -> int:
        lower = bands[0][0]
        for upper, base, rate in bands[1:]:
            if upper is None or aai <= upper:
                return self.r(D(base) + rate * (aai - lower))
            lower = upper
        raise AssertionError

    def payroll_one(self, iefw: int, status: str, dependent_student: bool = False, base: int | None = None):
        """HI and OASDI for one filer, each rounded to 3 decimals (not to whole dollars)."""
        if dependent_student or status in ("single", "head of household", "qualifying surviving spouse", ""):
            hi_cut, oasdi_cap = 200000, self.ed["oasdi_base"]
        elif status == "married filing jointly":
            hi_cut, oasdi_cap = 250000, self.ed["oasdi_base_mfj"]
        elif status == "married filing separately":
            hi_cut, oasdi_cap = 125000, self.ed["oasdi_base"]
        else:
            raise ValueError(f"unknown filing status {status!r}")
        if base is not None:
            oasdi_cap = base
        iefw = max(iefw, 0)
        hi = D("0.0145") * min(iefw, hi_cut) + D("0.0235") * max(iefw - hi_cut, 0)
        oasdi = D("0.062") * min(iefw, oasdi_cap)
        return self.r3(hi), self.r3(oasdi)

    def payroll(self, people: list[Person], dependent_student: bool = False) -> tuple[int, int]:
        filers = [p for p in people if p.present]
        if dependent_student:
            hi, oa = self.payroll_one(filers[0]["Income earned from work"], "", True)
            return self.r(hi), self.r(oa)
        mfj_cap = self.ed["oasdi_base"] if "oasdi_per_person" in self.naive else None
        if len(filers) == 2 and filers[0].mfj and filers[1].mfj:
            combined = filers[0]["Income earned from work"] + filers[1]["Income earned from work"]
            hi, oa = self.payroll_one(combined, "married filing jointly", base=mfj_cap)
            return self.r(hi), self.r(oa)
        if len(filers) == 1 and filers[0].mfj:
            hi, oa = self.payroll_one(filers[0]["Income earned from work"], "married filing jointly", base=mfj_cap)
            return self.r(hi), self.r(oa)
        lead = filers[0].status
        his, oas = D(0), D(0)
        for p in filers:
            st = p.status if (self.ed["payroll_own_status"] or p is filers[0]) else lead
            h, o = self.payroll_one(p["Income earned from work"], st)
            his, oas = his + h, oas + o
        return self.r(his), self.r(oas)

    def untaxed(self, people: list[Person], dist: str, roll: str) -> int:
        if "ira_floor_combined" in self.naive:
            return max(sum(p[dist] - p[roll] for p in people), 0)
        return sum(max(p[dist] - p[roll], 0) for p in people)

    def income(self, people: list[Person]) -> int:
        add = sum(p["Adjusted gross income"] + p["Deductible payments to IRA/Keogh/other"] + p["Tax-exempt interest income"]
                  for p in people)
        add += self.untaxed(people, "Untaxed portions of IRA distributions", "IRA rollover")
        add += self.untaxed(people, "Untaxed portions of pensions", "Pension rollover")
        fei = sum(p["Foreign income exclusion"] for p in people) if "fei_signed" in self.naive \
            else sum(abs(p["Foreign income exclusion"]) for p in people)
        add += fei
        off = sum(p["Taxable college grant and scholarship aid"] + p["Education credits"] + p["Federal work-study"]
                  for p in people)
        return add - off


def poverty(ed: dict, state: str, fs: int) -> int:
    s = (state or "").strip().lower()
    key = "alaska" if s in ("alaska", "ak") else "hawaii" if s in ("hawaii", "hi") else "other"
    base, inc = ed["poverty"][key]
    return base + inc * (fs - 1)


def ipa_lookup(table: dict[int, int], step: int, fs: int) -> int:
    top = max(table)
    return table[fs] if fs in table else table[top] + step * (fs - top)


# ---------------------------------------------------------------- the methodology
def solve(text: str, edition: str = "2728", naive: set[str] | None = None) -> dict:
    naive = set(naive or ())
    ed = EDITIONS[edition]
    c = Calc(ed, naive)
    s = parse(text)
    st = s["student"]
    student = Person(st)

    # Dependency status (HEA 480(d), guide Appendix B)
    marital = (st.get("Marital status") or "").lower()
    married_now = marital in ("married", "remarried") or ("separated_married" in naive and marital == "separated")
    homeless = st.get("Homelessness determination") or "None"
    hm = re.search(r"(\d{4}-\d{2}-\d{2})", homeless)
    independent = any([
        (st.get("Date of birth") or "9999") < ed["born_before"],
        married_now,
        yes(st.get("Enrolled in a master's or doctoral program in 2027-28")),
        yes(st.get("Active duty in the U.S. Armed Forces")),
        yes(st.get("Veteran of the U.S. Armed Forces")),
        yes(st.get("Has children or other dependents who get more than half their support from the student")),
        yes(st.get("Parents deceased, in foster care, or ward of the court since age 13")),
        yes(st.get("Emancipated minor or in legal guardianship (court-determined)")),
        bool(hm) and hm.group(1) >= ed["homeless_on_or_after"],
    ])
    out = {"dependency": "independent" if independent else "dependent", "formula": None, "family_size": None,
           "max_pell_indicator": None, "min_pell_indicator": None, "income_protection_allowance": None,
           "payroll_tax_allowance": None, "available_income": None, "contribution_from_assets": None,
           "contribution": None, "sai": None}

    if not independent:
        par = s.get("parent", {})
        pm = (par.get("Parents' marital status") or "").lower()
        parents_married = pm in ("married", "remarried", "unmarried and both legal parents living together")
        p1 = Person(par)
        p2 = Person(s.get("parent's spouse or partner")) if parents_married else Person(None)
        contributors = [p for p in (p1, p2) if p.present]
        state = par.get("State of residence", "")
        reported = par.get("Family size reported on the FAFSA", "")
        nonfilers = all(not p.filed for p in contributors)
        single_parent = (not parents_married) and (
            p1.status in ("head of household", "qualifying surviving spouse") or p1.eitc)
    else:
        sp_sec = s.get("student's spouse")
        spouse = Person(sp_sec) if married_now else Person(None)
        contributors = [p for p in (student, spouse) if p.present]
        state = st.get("State of residence", "")
        reported = st.get("Family size reported on the FAFSA", "")
        nonfilers = all(not p.filed for p in contributors)

    # Step 1: maximum Pell indicator 1 (non-filers): SAI -1500, nothing else computed
    if nonfilers:
        out["max_pell_indicator"] = 1
        out["sai"] = -1500
        return out

    # Family size (Appendix C), self-reported value wins when given
    if not independent:
        if parents_married and p1.mfj:
            calc = p1.exemptions + student.exemptions
            fs = calc if calc > 2 or "no_assumed_family" in naive else 3
        elif parents_married:
            calc = p1.exemptions + p2.exemptions + student.exemptions
            fs = calc if calc > 2 or "no_assumed_family" in naive else 3
        else:
            calc = p1.exemptions + student.exemptions
            fs = calc if calc >= 2 or "no_assumed_family" in naive else 2
    else:
        if married_now and student.mfj:
            calc = student.exemptions
            fs = calc if calc > 1 or "no_assumed_family" in naive else 2
        elif married_now:
            calc = student.exemptions + spouse.exemptions
            fs = calc if calc >= 2 or "no_assumed_family" in naive else 2
        else:
            calc = student.exemptions
            fs = calc if calc >= 1 or "no_assumed_family" in naive else 1
    if reported.strip() and "calc_over_reported" not in naive:
        fs = int(money(reported))
    out["family_size"] = fs

    if independent:
        has_dep = fs >= 3 if married_now else fs >= 2
        single_parent = (not married_now) and has_dep and (
            student.status in ("head of household", "qualifying surviving spouse") or student.eitc)
        formula = "C" if has_dep else "B"
    else:
        formula = "A"
    out["formula"] = formula

    # Step 1 continued: maximum Pell indicators 2 and 3
    agi = sum(p["Adjusted gross income"] + abs(p["Foreign income exclusion"]) for p in contributors)
    pov = poverty(ed, state, fs)
    pct = lambda k: c.r(D(pov) * D(k))
    low_ok = agi > 0 or "agi_nonpositive_max" in naive
    if single_parent and low_ok and agi <= pct("2.25"):
        out["max_pell_indicator"] = 2
    elif not single_parent and low_ok and agi <= pct("1.75"):
        out["max_pell_indicator"] = 3

    # Step 2: the SAI formula
    eea_cap = ed["eea_cap"]
    if formula == "A":
        ti = c.income(contributors)
        hi, oa = c.payroll(contributors)
        ipa = ipa_lookup(ed["ipa_a"], ed["ipa_a_step"], fs)
        eea = min(c.r(D("0.35") * sum(p["Income earned from work"] for p in contributors)), eea_cap)
        tax = sum(p["Income tax paid"] for p in contributors)
        pai = ti - (tax + hi + oa + ipa + eea)
        nw = p1["Annual child support received"] + p1["Cash, savings and checking"] + \
            max(p1["Net worth of current investments"], 0) + c.bfnw(p1["Net worth of businesses and/or farms"])
        pca = max(c.r(D(nw) * D("0.12")), 0)
        paai = pai + pca
        pc = c.aai(paai)
        # student side
        sti = c.income([student])
        shi, soa = c.payroll([student], dependent_student=True)
        neg = -paai if paai < 0 and "no_paai_allowance" not in naive else 0
        stai = sti - (student["Income tax paid"] + shi + soa + ed["ipa_dep_student"] + neg)
        sci = max(c.r(D(stai) * D("0.5")), 0)
        snw = student["Cash, savings and checking"] + max(student["Net worth of current investments"], 0) + \
            c.bfnw(student["Net worth of businesses and/or farms"])
        sca = max(c.r(D(snw) * D("0.20")), 0)
        sai = pc + sci + sca
        out.update(income_protection_allowance=ipa, payroll_tax_allowance=hi + oa, available_income=pai,
                   contribution_from_assets=pca, contribution=pc)
    else:
        ti = c.income(contributors)
        hi, oa = c.payroll(contributors)
        earned = sum(p["Income earned from work"] for p in contributors)
        if formula == "B":
            ipa = ed["ipa_b"][1 if married_now else 0]
            eea = min(c.r(D("0.35") * earned), eea_cap) if married_now else 0
        else:
            ipa = ipa_lookup(ed["ipa_c_married"], ed["ipa_c_married_step"], fs) if married_now \
                else ipa_lookup(ed["ipa_c_single"], ed["ipa_c_single_step"], fs)
            eea = min(c.r(D("0.35") * earned), eea_cap)
        tax = sum(p["Income tax paid"] for p in contributors)
        stai = ti - (tax + hi + oa + ipa + eea)
        nw = student["Annual child support received"] + student["Cash, savings and checking"] + \
            max(student["Net worth of current investments"], 0) + c.bfnw(student["Net worth of businesses and/or farms"])
        if formula == "B":
            sci = c.r(D(stai) * D("0.5"))
            if "b_clamp" in naive:
                sci = max(sci, 0)
            sca = max(c.r(D(nw) * D("0.20")), 0)
            sai = sci + sca
            contribution = sci
        else:
            sca = max(c.r(D(nw) * D("0.07")), 0)
            contribution = c.aai(stai + sca)
            sai = contribution
        out.update(income_protection_allowance=ipa, payroll_tax_allowance=hi + oa, available_income=stai,
                   contribution_from_assets=sca, contribution=contribution)

    sai = max(min(sai, 999999), -1500)
    if out["max_pell_indicator"] in (2, 3) and "max_pell_keeps_sai" not in naive:
        sai = min(sai, 0)
    out["sai"] = sai

    # Step 3: minimum Pell indicator (reported only when no maximum-Pell indicator applies)
    if out["max_pell_indicator"] is None:
        if not independent:
            if single_parent and agi <= pct("3.25"):
                out["min_pell_indicator"] = 1
            elif not single_parent and agi <= pct("2.75"):
                out["min_pell_indicator"] = 2
        else:
            has_dep = formula == "C"
            if single_parent and agi <= pct("4.00"):
                out["min_pell_indicator"] = 3
            elif has_dep and not single_parent and agi <= pct("3.50"):
                out["min_pell_indicator"] = 4
            elif not has_dep and agi <= pct("2.75"):
                out["min_pell_indicator"] = 5
    return out


def main(argv: list[str]) -> int:
    args = list(argv[1:])
    edition, naive = "2728", set()
    if "--edition" in args:
        i = args.index("--edition"); edition = args[i + 1]; del args[i:i + 2]
    if "--naive" in args:
        i = args.index("--naive"); naive = {n for n in args[i + 1].split(",") if n}; del args[i:i + 2]
    unknown = naive - set(NAIVE)
    if unknown or len(args) != 1:
        print(f"usage: solve.py <applicant.md> [--edition 2728|2627] [--naive {','.join(NAIVE)}]", file=sys.stderr)
        return 2
    print(json.dumps(solve(Path(args[0]).read_text(), edition, naive), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
