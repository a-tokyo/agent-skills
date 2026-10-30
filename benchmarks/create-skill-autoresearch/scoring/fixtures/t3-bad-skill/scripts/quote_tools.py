#!/usr/bin/env python3
"""Reference solver for task-3 (freight-quote-builder). Never shown to any arm.

Usage: solve.py <rates-dir> <shipment.md> [--stale rule,rule]   -> prints the quote JSON

This is the ground truth the gold standards and the holdout answer key are generated from.

`solve(..., stale=...)` switches individual rules to the version a builder gets by trusting the
written guide (class B, the guide is stale and a gold standard contradicts it) or by fitting the gold
standards without reading the guide (class A, the guide is right and the gold standards happen not to
tell the two apart). generate.py uses the switches to prove that every class-B rule is visible in at
least one gold standard and every rule of either class changes at least one holdout answer.

Class B (guide stale, gold shows practice):
  divisor       guide: volumetric divisor 5000 for every carrier; practice: carriers.csv dim_divisor
  cutoff        guide: pickup cutoff 17:00; practice: carriers.csv cutoff
  holidays      guide: business days are Mon-Fri; practice: holidays.csv dates are not business days
  whole_kg      guide: billable weight rounds up to 0.5 kg; practice: above 20 kg, to the next whole kg
  per_kg_total  guide: above the last break, last flat price + per-kg x excess; practice: per-kg x all kg
  fuel_base     guide: fuel on the base charge; practice: on base + RES + REM + OVS + OWT (not DG, SIG)
  girth         guide: oversize when the longest side > 120 cm; practice: or length + girth > 300 cm
  selection     guide: preferred carrier, else lowest base; practice: lowest total EUR
  dg_upgrade    guide: no DG carrier on the service -> DG_NOT_ALLOWED, no quote; practice: next faster
                service, SERVICE_UPGRADED "<from>-><to>"
  deadline      guide: silent; practice: no option meets the date -> earliest delivery, DEADLINE_MISSED
Class A (guide right, gold does not separate):
  sum_round     per-package rounding (naive: round the shipment sum once)
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
SERVICE_ORDER = ["economy", "standard", "express"]
FUEL_CODES = ("RES", "REM", "OVS", "OWT")
SURCHARGE_ORDER = ("RES", "REM", "OVS", "OWT", "DG", "SIG")
PKG_RE = re.compile(
    r"^-\s+(\d+)\s*x\s+(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*(cm|in),\s*"
    r"(\d+(?:\.\d+)?)\s*(kg|lb)(?:\s+(each|total))?\s*$", re.IGNORECASE)
DEST_RE = re.compile(r"^(\d+)\s+(.+?),\s*([A-Z]{2})\s*\((residential|business)\)\s*$", re.IGNORECASE)


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def parse_request(text: str) -> dict[str, object]:
    fields: dict[str, str] = {}
    packages: list[dict[str, Decimal]] = []
    for line in text.splitlines():
        line = line.strip()
        if m := PKG_RE.match(line):
            count = int(m.group(1))
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
        self.zones = {(z["carrier_id"], z["country"]): z["zone"] for z in read_csv(master / "zones.csv")}
        self.bands: dict[tuple[str, str, str], list[dict[str, str]]] = {}
        for r in read_csv(master / "rates.csv"):
            self.bands.setdefault((r["carrier_id"], r["service"], r["zone"]), []).append(r)
        self.surcharges = {(s["carrier_id"], s["code"]): s for s in read_csv(master / "surcharges.csv")}
        self.remote = read_csv(master / "remote_areas.csv")
        self.holidays = {date.fromisoformat(h["date"]) for h in read_csv(master / "holidays.csv")}
        self.fx = {r["currency"]: Decimal(r["rate_to_eur"]) for r in read_csv(master / "fx.csv")}


def business_day(d: date, rates: Rates, stale: frozenset[str]) -> bool:
    return d.weekday() < 5 and ("holidays" in stale or d not in rates.holidays)


def next_business_day(d: date, rates: Rates, stale: frozenset[str]) -> date:
    d += timedelta(days=1)
    while not business_day(d, rates, stale):
        d += timedelta(days=1)
    return d


def delivery_date(req: dict, carrier: dict, transit: int, rates: Rates, stale: frozenset[str]) -> date:
    ship: datetime = req["ship"]
    cutoff = "17:00" if "cutoff" in stale else carrier["cutoff"]
    pickup = ship.date()
    if not business_day(pickup, rates, stale) or ship.strftime("%H:%M") >= cutoff:
        pickup = next_business_day(pickup, rates, stale)
    for _ in range(transit):
        pickup = next_business_day(pickup, rates, stale)
    return pickup


def round_billable(kg: Decimal, stale: frozenset[str]) -> Decimal:
    if kg > 20 and "whole_kg" not in stale:
        return kg.to_integral_value(rounding=ROUND_CEILING)
    return (kg * 2).to_integral_value(rounding=ROUND_CEILING) / 2


def billable_kg(req: dict, carrier: dict, stale: frozenset[str]) -> Decimal:
    divisor = Decimal("5000") if "divisor" in stale else Decimal(carrier["dim_divisor"])
    per_pkg = []
    for p in req["packages"]:
        l, w, h = p["dims"]
        per_pkg.append(max(p["kg"], l * w * h / divisor))
    if "sum_round" in stale:
        return round_billable(sum(per_pkg, Decimal("0")), stale)
    return sum((round_billable(kg, stale) for kg in per_pkg), Decimal("0"))


def band_price(bands: list[dict[str, str]], kg: Decimal, stale: frozenset[str]) -> Decimal:
    flat = sorted((b for b in bands if b["rate_type"] == "flat"), key=lambda b: Decimal(b["max_kg"]))
    per_kg = Decimal(next(b["price"] for b in bands if b["rate_type"] == "per_kg"))
    for b in flat:
        if kg <= Decimal(b["max_kg"]):
            return Decimal(b["price"])
    if "per_kg_total" in stale:
        return Decimal(flat[-1]["price"]) + per_kg * (kg - Decimal(flat[-1]["max_kg"]))
    return per_kg * kg


def oversize(p: dict, stale: frozenset[str]) -> bool:
    l, w, h = p["dims"]
    return l > 120 or ("girth" not in stale and l + 2 * w + 2 * h > 300)


def quote(req: dict, rates: Rates, cid: str, svc: dict, stale: frozenset[str]) -> dict | None:
    carrier = rates.carriers[cid]
    zone = rates.zones.get((cid, req["country"]))
    if zone is None or (cid, svc["service"], zone) not in rates.bands:
        return None
    kg = billable_kg(req, carrier, stale)
    base = money(max(band_price(rates.bands[(cid, svc["service"], zone)], kg, stale), Decimal(svc["min_charge"])))

    def amount(code: str) -> Decimal:
        return Decimal(rates.surcharges[(cid, code)]["amount"])

    sur: dict[str, Decimal] = {}
    if req["residential"]:
        sur["RES"] = money(amount("RES"))
    if any(r["carrier_id"] == cid and r["country"] == req["country"]
           and int(r["postcode_from"]) <= req["postcode"] <= int(r["postcode_to"]) for r in rates.remote):
        sur["REM"] = money(max(amount("REM") * kg, Decimal(rates.surcharges[(cid, "REM")]["min"] or "0")))
    n_ovs = sum(1 for p in req["packages"] if oversize(p, stale))
    if n_ovs:
        sur["OVS"] = money(amount("OVS") * n_ovs)
    if "owt_billable" in stale:
        divisor = Decimal("5000") if "divisor" in stale else Decimal(carrier["dim_divisor"])
        n_owt = sum(1 for p in req["packages"]
                    if max(p["kg"], p["dims"][0] * p["dims"][1] * p["dims"][2] / divisor) > 30)
    else:
        n_owt = sum(1 for p in req["packages"] if p["kg"] > 30)
    if n_owt:
        sur["OWT"] = money(amount("OWT") * n_owt)
    if req["dg"]:
        sur["DG"] = money(amount("DG"))
    if req["signature"]:
        sur["SIG"] = money(amount("SIG"))
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
        "_base_native_eur": eur(base),
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
        if preferred:
            return min(preferred, key=lambda q: (q["_base_native_eur"], q["carrier_id"]))
        return min(cands, key=lambda q: (q["_base_native_eur"], q["carrier_id"]))
    return min(cands, key=lambda q: (q["total_eur"], q["delivery_date"], q["carrier_id"],
                                     SERVICE_ORDER.index(q["service"])))


def solve(master: Path, request_text: str, stale: frozenset[str] = frozenset()) -> dict[str, object]:
    rates = Rates(master)
    req = parse_request(request_text)
    exceptions: list[dict[str, str]] = []
    service = req["service"]
    chosen = None
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
