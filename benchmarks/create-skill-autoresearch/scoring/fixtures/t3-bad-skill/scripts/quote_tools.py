#!/usr/bin/env python3
"""Reference solver for task-3 (freight-quote-builder). Never shown to any arm.

Usage: solve.py <rates-dir> <shipment.md> [--stale rule,rule]   -> prints the quote JSON

This is the ground truth the gold standards and the holdout answer key are generated from.

`solve(..., stale=...)` switches individual rules to a wrong version, so generate.py can prove where
each rule shows. Three classes:

Class B (the guide's rules section is stale; at least one gold quote contradicts it):
  divisor       guide: volumetric divisor 5000 for every carrier; practice: carriers.csv dim_divisor
  cutoff        guide: pickup cutoff 17:00; practice: carriers.csv cutoff
  holidays      guide: business days are Mon-Fri; practice: holidays.csv dates are not business days
  whole_kg      guide: round up to round_step_kg; practice: a box over heavy_kg rounds to the next whole kg
  per_kg_total  guide: above the last break, last flat price + per-kg x excess; practice: per-kg x all kg
  fuel_base     guide: fuel on the base charge; practice: on base + RES + REM + OVS + OWT (not DG, SIG)
  girth         guide: oversize when the longest side > oversize_cm; practice: or length + girth > girth_cm
  selection     guide: preferred carrier, else lowest base; practice: lowest total EUR
  dg_upgrade    guide: no DG carrier on the service -> DG_NOT_ALLOWED, no quote; practice: next faster
                service, SERVICE_UPGRADED "<from>-><to>"
  deadline      guide: silent; practice: no option meets the date -> earliest delivery, DEADLINE_MISSED
Class C (the guide's format section is current and states the rule; every training table holds one
value, so the gold quotes cannot tell the rule from a constant or a shortcut; the holdout tables vary):
  c_round       carriers.csv round_step_kg / heavy_kg (shortcut: 0.5 and 20)
  c_size        carriers.csv oversize_cm / girth_cm (shortcut: 120 and 300)
  c_owt         carriers.csv owt_kg (shortcut: 30)
  unit_by_code  surcharges.csv unit decides the multiplier (shortcut: RES/DG/SIG per shipment,
                OVS/OWT per box, REM per kg, whatever the unit says)
  hol_any       holidays.csv country: only PT and destination holidays count (shortcut: every row)
  hol_pt        same rule (shortcut: only PT rows)
  zone_first    zones.csv postcode range rows win over the country row (shortcut: first country row)
  zone_last     same rule (shortcut: last country row, a dict keyed by carrier and country)
  max_box       services.csv max_box_kg excludes a service for a heavier box (shortcut: ignored)
Class A (the guide is right and the gold quotes do not separate it from a naive reading):
  sum_round     per-box rounding (naive: round the shipment sum once)
  owt_billable  overweight on actual kg (naive: on billable kg)
  fx_total      convert each charge to EUR and round (naive: convert the carrier-currency total once)
"""
from __future__ import annotations

import csv
import json
import re
import sys
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from pathlib import Path

IN_CM = Decimal("2.54")
LB_KG = Decimal("0.45359237")
ORIGIN = "PT"
SERVICE_ORDER = ["economy", "standard", "express"]
FUEL_CODES = ("RES", "REM", "OVS", "OWT")
SURCHARGE_ORDER = ("RES", "REM", "OVS", "OWT", "DG", "SIG")
CODE_UNITS = {"RES": "shipment", "REM": "kg", "OVS": "package", "OWT": "package", "DG": "shipment", "SIG": "shipment"}
SHORTCUT = {"round_step_kg": "0.5", "heavy_kg": "20", "oversize_cm": "120", "girth_cm": "300", "owt_kg": "30"}
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10}
PKG_RE = re.compile(
    r"^-\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s*x\s+(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*x\s*"
    r"(\d+(?:\.\d+)?)\s*(cm|in),\s*(\d+(?:\.\d+)?)\s*(kg|lb)(?:\s+(each|total))?\s*$", re.IGNORECASE)
DEST_RE = re.compile(r"^(\d+)\s+(.+?),\s*([A-Z]{2})\s*\((residential|business)\)\s*$", re.IGNORECASE)


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def parse_request(text: str) -> dict[str, object]:
    fields: dict[str, str] = {}
    packages: list[dict[str, object]] = []
    for line in text.splitlines():
        line = line.strip()
        if m := PKG_RE.match(line):
            word = m.group(1).lower()
            count = int(word) if word.isdigit() else NUMBER_WORDS[word]
            dims = [Decimal(m.group(i)) for i in (2, 3, 4)]
            if m.group(5).lower() == "in":
                dims = [d * IN_CM for d in dims]
            weight = Decimal(m.group(6))
            if m.group(7).lower() == "lb":
                weight *= LB_KG
            if (m.group(8) or "each").lower() == "total":
                weight /= count
            for _ in range(count):
                packages.append({"dims": sorted(dims, reverse=True), "kg": weight})
        elif ":" in line and not line.startswith("-"):
            key, _, value = line.partition(":")
            fields[key.strip().lower()] = value.strip()
    dest = DEST_RE.match(fields["destination"])
    service = fields["service"].strip().lower()
    deadline = None
    if service.startswith("deliver by"):
        deadline = date.fromisoformat(service.split()[-1])
        service = "deadline"
    return {
        "ship": datetime.strptime(fields["ship date"], "%Y-%m-%d %H:%M"),
        "postcode": int(dest.group(1)),
        "country": dest.group(3).upper(),
        "residential": dest.group(4).lower() == "residential",
        "service": service,
        "deadline": deadline,
        "dg": "lithium" in fields.get("contents", "").lower(),
        "signature": "signature" in fields.get("notes", "").lower(),
        "packages": packages,
    }


class Rates:
    def __init__(self, master: Path) -> None:
        self.carriers = {c["carrier_id"]: c for c in read_csv(master / "carriers.csv")}
        self.services = read_csv(master / "services.csv")
        self.zones = read_csv(master / "zones.csv")
        self.bands: dict[tuple[str, str, str], list[dict[str, str]]] = {}
        for r in read_csv(master / "rates.csv"):
            self.bands.setdefault((r["carrier_id"], r["service"], r["zone"]), []).append(r)
        self.surcharges = {(s["carrier_id"], s["code"]): s for s in read_csv(master / "surcharges.csv")}
        self.remote = read_csv(master / "remote_areas.csv")
        self.holidays = [(date.fromisoformat(h["date"]), h["country"]) for h in read_csv(master / "holidays.csv")]
        self.fx = {r["currency"]: Decimal(r["rate_to_eur"]) for r in read_csv(master / "fx.csv")}


def param(carrier: dict, column: str, rule: str, stale: frozenset[str]) -> Decimal:
    return Decimal(SHORTCUT[column] if rule in stale else carrier[column])


def closed(d: date, country: str, rates: Rates, stale: frozenset[str]) -> bool:
    if "holidays" in stale:
        return False
    if "hol_any" in stale:
        return any(day == d for day, _ in rates.holidays)
    if "hol_pt" in stale:
        return (d, ORIGIN) in rates.holidays
    return (d, ORIGIN) in rates.holidays or (d, country) in rates.holidays


def business_day(d: date, country: str, rates: Rates, stale: frozenset[str]) -> bool:
    return d.weekday() < 5 and not closed(d, country, rates, stale)


def next_business_day(d: date, country: str, rates: Rates, stale: frozenset[str]) -> date:
    d += timedelta(days=1)
    while not business_day(d, country, rates, stale):
        d += timedelta(days=1)
    return d


def delivery_date(req: dict, carrier: dict, transit: int, rates: Rates, stale: frozenset[str]) -> date:
    ship: datetime = req["ship"]
    country = req["country"]
    cutoff = "17:00" if "cutoff" in stale else carrier["cutoff"]
    pickup = ship.date()
    if not business_day(pickup, country, rates, stale) or ship.strftime("%H:%M") >= cutoff:
        pickup = next_business_day(pickup, country, rates, stale)
    for _ in range(transit):
        pickup = next_business_day(pickup, country, rates, stale)
    return pickup


def round_billable(kg: Decimal, carrier: dict, stale: frozenset[str]) -> Decimal:
    if kg > param(carrier, "heavy_kg", "c_round", stale) and "whole_kg" not in stale:
        return kg.to_integral_value(rounding=ROUND_CEILING)
    step = param(carrier, "round_step_kg", "c_round", stale)
    return (kg / step).to_integral_value(rounding=ROUND_CEILING) * step


def volumetric(p: dict, carrier: dict, stale: frozenset[str]) -> Decimal:
    divisor = Decimal("5000") if "divisor" in stale else Decimal(carrier["dim_divisor"])
    l, w, h = p["dims"]
    return l * w * h / divisor


def billable_kg(req: dict, carrier: dict, stale: frozenset[str]) -> Decimal:
    per_pkg = [max(p["kg"], volumetric(p, carrier, stale)) for p in req["packages"]]
    if "sum_round" in stale:
        return round_billable(sum(per_pkg, Decimal("0")), carrier, stale)
    return sum((round_billable(kg, carrier, stale) for kg in per_pkg), Decimal("0"))


def band_price(bands: list[dict[str, str]], kg: Decimal, stale: frozenset[str]) -> Decimal:
    flat = sorted((b for b in bands if b["rate_type"] == "flat"), key=lambda b: Decimal(b["max_kg"]))
    per_kg = Decimal(next(b["price"] for b in bands if b["rate_type"] == "per_kg"))
    for b in flat:
        if kg <= Decimal(b["max_kg"]):
            return Decimal(b["price"])
    if "per_kg_total" in stale:
        return Decimal(flat[-1]["price"]) + per_kg * (kg - Decimal(flat[-1]["max_kg"]))
    return per_kg * kg


def oversize(p: dict, carrier: dict, stale: frozenset[str]) -> bool:
    l, w, h = p["dims"]
    if l > param(carrier, "oversize_cm", "c_size", stale):
        return True
    return "girth" not in stale and l + 2 * w + 2 * h > param(carrier, "girth_cm", "c_size", stale)


def overweight(p: dict, carrier: dict, stale: frozenset[str]) -> bool:
    kg = max(p["kg"], volumetric(p, carrier, stale)) if "owt_billable" in stale else p["kg"]
    return kg > param(carrier, "owt_kg", "c_owt", stale)


def zone_for(req: dict, rates: Rates, cid: str, stale: frozenset[str]) -> str | None:
    rows = [z for z in rates.zones if z["carrier_id"] == cid and z["country"] == req["country"]]
    if not rows:
        return None
    if "zone_first" in stale:
        return rows[0]["zone"]
    if "zone_last" in stale:
        return rows[-1]["zone"]
    for z in rows:
        if z["postcode_from"] and int(z["postcode_from"]) <= req["postcode"] <= int(z["postcode_to"]):
            return z["zone"]
    return next(z["zone"] for z in rows if not z["postcode_from"])


def quote(req: dict, rates: Rates, cid: str, svc: dict, stale: frozenset[str]) -> dict | None:
    carrier = rates.carriers[cid]
    zone = zone_for(req, rates, cid, stale)
    if zone is None or (cid, svc["service"], zone) not in rates.bands:
        return None
    if "max_box" not in stale and any(p["kg"] > Decimal(svc["max_box_kg"]) for p in req["packages"]):
        return None
    kg = billable_kg(req, carrier, stale)
    base = money(max(band_price(rates.bands[(cid, svc["service"], zone)], kg, stale), Decimal(svc["min_charge"])))

    n_box = len(req["packages"])
    n_ovs = sum(1 for p in req["packages"] if oversize(p, carrier, stale))
    n_owt = sum(1 for p in req["packages"] if overweight(p, carrier, stale))
    remote = any(r["carrier_id"] == cid and r["country"] == req["country"]
                 and int(r["postcode_from"]) <= req["postcode"] <= int(r["postcode_to"]) for r in rates.remote)
    applies = {"RES": req["residential"], "REM": remote, "OVS": n_ovs > 0, "OWT": n_owt > 0, "DG": req["dg"],
               "SIG": req["signature"]}
    boxes = {"OVS": n_ovs, "OWT": n_owt}

    sur: dict[str, Decimal] = {}
    for code in SURCHARGE_ORDER:
        if not applies[code]:
            continue
        row = rates.surcharges[(cid, code)]
        unit = CODE_UNITS[code] if "unit_by_code" in stale else row["unit"]
        amount = Decimal(row["amount"])
        if unit == "package":
            value = amount * boxes.get(code, n_box)
        elif unit == "kg":
            value = amount * kg
        elif unit == "pct_base":
            value = amount * base / 100
        else:
            value = amount
        if row["min"]:
            value = max(value, Decimal(row["min"]))
        sur[code] = money(value)
    fuel_basis = base if "fuel_base" in stale else base + sum((sur[c] for c in FUEL_CODES if c in sur), Decimal("0"))
    fuel = money(fuel_basis * Decimal(carrier["fuel_pct"]) / 100)

    rate = rates.fx[carrier["currency"]]
    eur = lambda x: money(x * rate)  # noqa: E731
    sur_eur = [{"code": c, "amount_eur": float(eur(sur[c]))} for c in SURCHARGE_ORDER if c in sur]
    if "fx_total" in stale:
        total = eur(base + sum(sur.values(), Decimal("0")) + fuel)
    else:
        total = eur(base) + sum((eur(v) for v in sur.values()), Decimal("0")) + eur(fuel)
    return {
        "carrier_id": cid,
        "service": svc["service"],
        "billable_kg": float(kg),
        "base_eur": float(eur(base)),
        "surcharges": sur_eur,
        "fuel_eur": float(eur(fuel)),
        "total_eur": float(total),
        "delivery_date": delivery_date(req, carrier, int(svc["transit_days"]), rates, stale).isoformat(),
        "_base_eur": eur(base),
        "_preferred": carrier.get("preferred") == "Y",
    }


def options(req: dict, rates: Rates, service: str | None, stale: frozenset[str]) -> list[dict]:
    out = []
    for svc in rates.services:
        cid = svc["carrier_id"]
        if rates.carriers[cid]["status"] != "active":
            continue
        if service is not None and svc["service"] != service:
            continue
        if req["dg"] and svc["dg_allowed"] != "Y":
            continue
        q = quote(req, rates, cid, svc, stale)
        if q is not None:
            out.append(q)
    return out


def pick(cands: list[dict], stale: frozenset[str]) -> dict:
    if "selection" in stale:
        preferred = [q for q in cands if q["_preferred"]]
        return min(preferred or cands, key=lambda q: (q["_base_eur"], q["carrier_id"]))
    return min(cands, key=lambda q: (q["total_eur"], q["delivery_date"], q["carrier_id"],
                                     SERVICE_ORDER.index(q["service"])))


def solve(master: Path, request_text: str, stale: frozenset[str] = frozenset()) -> dict[str, object]:
    rates = Rates(master)
    req = parse_request(request_text)
    exceptions: list[dict[str, str]] = []
    service = req["service"]
    if service in SERVICE_ORDER:
        cands = options(req, rates, service, stale)
        if not cands and req["dg"]:
            if "dg_upgrade" in stale:
                return {"carrier_id": None, "service": None, "billable_kg": 0, "base_eur": 0, "surcharges": [],
                        "fuel_eur": 0, "total_eur": 0, "delivery_date": None,
                        "exceptions": [{"code": "DG_NOT_ALLOWED", "detail": service}]}
            for faster in SERVICE_ORDER[SERVICE_ORDER.index(service) + 1:]:
                cands = options(req, rates, faster, stale)
                if cands:
                    exceptions.append({"code": "SERVICE_UPGRADED", "detail": f"{service}->{faster}"})
                    break
        chosen = pick(cands, stale)
    elif service == "cheapest":
        chosen = pick(options(req, rates, None, stale), stale)
    else:
        every = options(req, rates, None, stale)
        deadline = req["deadline"].isoformat()
        on_time = [q for q in every if q["delivery_date"] <= deadline]
        if on_time:
            chosen = pick(on_time, stale)
        elif "deadline" in stale:
            chosen = pick(every, stale)
        else:
            chosen = min(every, key=lambda q: (q["delivery_date"], q["total_eur"], q["carrier_id"]))
            exceptions.append({"code": "DEADLINE_MISSED", "detail": deadline})
    out = {k: v for k, v in chosen.items() if not k.startswith("_")}
    out["exceptions"] = exceptions
    return out


if __name__ == "__main__":
    master_dir, request_path = Path(sys.argv[1]), Path(sys.argv[2])
    stale = frozenset(sys.argv[sys.argv.index("--stale") + 1].split(",")) if "--stale" in sys.argv else frozenset()
    print(json.dumps(solve(master_dir, request_path.read_text(encoding="utf-8"), stale), indent=2))
