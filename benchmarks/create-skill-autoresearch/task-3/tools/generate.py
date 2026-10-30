#!/usr/bin/env python3
"""Deterministic generator for task-3 (synthetic, generic data). Never shown to any arm.

Usage: generate.py   (writes ../input, ../holdout; idempotent for fixed seeds)

The holdout rate tables use other carriers, divisors, cutoffs, fuel rates, currencies, holidays, zones
and remote areas than the training tables, and they vary what every training table holds constant
(rounding and size thresholds, surcharge units, holiday countries, zone postcode ranges, box-weight
limits). After writing, it checks where every rule in solve.py shows and exits non-zero unless:
  - each class-B rule (stale guide) changes at least one gold quote,
  - no class-C rule (current guide, constant in training) changes any gold quote,
  - every class-B and class-C rule changes the SCORE of at least one holdout, not only its JSON,
  - among every combination of the class-B and class-A switches, only the true rules reproduce all
    eight gold quotes as the desk log records them (the learnability proof).

The gold quotes are desk-log summaries (carrier, service, billable weight, surcharge codes, total,
delivery date, exceptions): base, fuel and per-surcharge amounts are not shown, so the money rules
have to be inferred jointly from the totals. The holdout answer key keeps the full schema.
"""
from __future__ import annotations

import csv
import itertools
import json
import random
import shutil
import sys
from pathlib import Path

from solve import Rates, solve

HERE = Path(__file__).resolve().parent
TASK = HERE.parent

CLASS_B = ["divisor", "cutoff", "holidays", "whole_kg", "per_kg_total", "fuel_base", "girth", "selection",
           "dg_upgrade", "deadline"]
CLASS_C = ["c_round", "c_size", "c_owt", "unit_by_code", "hol_any", "hol_pt", "zone_first", "zone_last", "max_box"]
CLASS_A = ["sum_round", "owt_billable", "fx_total"]
COUNTRIES = ["FR", "DE", "ES", "IT", "BE", "AT"]
FLAT_BREAKS = {"train": [1, 2, 5, 10, 15, 20, 30, 50, 70], "holdout": [0.5, 1, 3, 5, 10, 20, 25, 40, 60]}
CARRIER_COLUMNS = ["carrier_id", "name", "status", "currency", "dim_divisor", "round_step_kg", "heavy_kg",
                   "oversize_cm", "girth_cm", "owt_kg", "fuel_pct", "cutoff", "preferred"]
DEFAULT_UNITS = {"RES": "shipment", "REM": "kg", "OVS": "package", "OWT": "package", "DG": "shipment", "SIG": "shipment"}

DATASETS: dict[str, dict[str, object]] = {
    "train": {
        "seed": 31,
        "out": TASK / "input" / "rates",
        # every training carrier holds the same thresholds (the guide's), so gold cannot separate them
        "carriers": [
            ("C01", "Atlas Parcel", "active", "EUR", "5000", "0.5", "20", "120", "300", "30", "12.5", "16:00", "Y"),
            ("C02", "Nordic Express", "active", "GBP", "4000", "0.5", "20", "120", "300", "30", "9", "17:30", "N"),
            ("C03", "Iberia Freight", "active", "EUR", "6000", "0.5", "20", "120", "300", "30", "15", "14:00", "N"),
            ("C04", "Orion Logistics", "suspended", "EUR", "5000", "0.5", "20", "120", "300", "30", "10", "17:00", "N"),
        ],
        # carrier, service, transit_days, dg_allowed, price level, max_box_kg
        "services": [
            ("C01", "express", 1, "Y", 1.9, 70), ("C01", "standard", 3, "Y", 1.0, 70),
            ("C01", "economy", 5, "N", 0.75, 70),
            ("C02", "express", 2, "Y", 1.45, 70), ("C02", "standard", 4, "Y", 0.8, 70),
            ("C03", "standard", 3, "Y", 0.9, 70), ("C03", "economy", 6, "N", 0.62, 70),
            ("C04", "express", 1, "Y", 1.2, 70), ("C04", "standard", 2, "Y", 0.5, 70),
            ("C04", "economy", 4, "Y", 0.4, 70),
        ],
        "zone_ranges": [],
        "units": {},
        "remote": [("C01", "FR", 20000, 20999), ("C02", "FR", 20000, 20999), ("C03", "FR", 20000, 20999),
                   ("C01", "IT", 7000, 9999), ("C02", "IT", 7000, 9999), ("C03", "IT", 8000, 9999),
                   ("C01", "ES", 7000, 7999), ("C03", "ES", 7000, 7999), ("C02", "AT", 6500, 6999)],
        "holidays": [("2026-04-03", "PT", "Good Friday"), ("2026-04-25", "PT", "Freedom Day"),
                     ("2026-05-01", "PT", "Labour Day"), ("2026-06-04", "PT", "Corpus Christi"),
                     ("2026-06-10", "PT", "Portugal Day")],
        "fx": {"EUR": "1", "GBP": "1.17", "USD": "0.92"},
    },
    "holdout": {
        "seed": 47,
        "out": TASK / "holdout" / "rates",
        "carriers": [
            ("K11", "Kestrel Cargo", "active", "CHF", "4500", "0.5", "20", "120", "300", "30", "11", "15:30", "N"),
            ("K12", "Delta Courier", "active", "EUR", "6000", "1", "20", "150", "330", "32", "14.5", "18:00", "Y"),
            ("K13", "Polar Post", "suspended", "EUR", "5000", "0.5", "20", "120", "300", "30", "8", "17:00", "N"),
            ("K14", "Meridian Freight", "active", "EUR", "5500", "0.5", "25", "100", "260", "25", "17", "13:30", "N"),
        ],
        "services": [
            ("K11", "express", 2, "Y", 1.5, 70), ("K11", "standard", 3, "Y", 0.85, 70),
            ("K11", "economy", 6, "N", 0.6, 70),
            ("K12", "express", 1, "Y", 1.85, 70), ("K12", "standard", 2, "Y", 1.05, 70),
            ("K12", "economy", 5, "N", 0.72, 70),
            ("K13", "express", 1, "Y", 1.1, 70), ("K13", "standard", 2, "Y", 0.5, 70),
            ("K13", "economy", 3, "Y", 0.4, 70),
            ("K14", "standard", 4, "Y", 0.8, 20), ("K14", "economy", 7, "N", 0.55, 70),
        ],
        # carrier, country, postcode_from, postcode_to, zone — listed after the carrier's country row
        "zone_ranges": [("K12", "IT", 9000, 9999, "Z4"), ("K11", "DE", 18000, 18999, "Z4"),
                        ("K14", "FR", 20000, 20999, "Z4"), ("K12", "FR", 20000, 20999, "Z4"),
                        ("K12", "AT", 4000, 4999, "Z3")],
        "units": {("K12", "RES"): ("pct_base", "6"), ("K14", "RES"): ("pct_base", "9"), ("K11", "SIG"): ("package", None)},
        "remote": [("K11", "IT", 9000, 9999), ("K12", "IT", 9000, 9999), ("K14", "IT", 9000, 9999),
                   ("K11", "DE", 18500, 18599), ("K12", "DE", 18500, 18599), ("K14", "DE", 18000, 18999),
                   ("K11", "ES", 35000, 35999), ("K12", "ES", 35000, 35999), ("K14", "ES", 35000, 35999),
                   ("K12", "FR", 20000, 20999)],
        "holidays": [("2026-10-05", "PT", "Republic Day"), ("2026-10-12", "ES", "National Day"),
                     ("2026-11-01", "PT", "All Saints"), ("2026-11-11", "FR", "Armistice"),
                     ("2026-11-11", "BE", "Armistice"), ("2026-12-01", "PT", "Restoration"),
                     ("2026-12-07", "ES", "Constitution Day (moved)"), ("2026-12-08", "PT", "Immaculate Conception"),
                     ("2026-12-08", "IT", "Immaculate Conception"), ("2026-12-08", "AT", "Immaculate Conception"),
                     ("2026-12-25", "PT", "Christmas Day"), ("2026-12-25", "FR", "Christmas Day")],
        "fx": {"EUR": "1", "GBP": "1.15", "CHF": "1.06", "USD": "0.89"},
    },
}

# ship date, destination, service, contents, notes, package lines
GOLD_REQUESTS = [
    ("2026-03-10 14:30", "69003 Lyon, FR (business)", "standard", "office supplies", "",
     ["2 x 50x40x30 cm, 6.2 kg each"]),
    ("2026-03-12 15:20", "80331 Munich, DE (residential)", "express", "spare parts", "signature required",
     ["1 x 24x16x12 in, 14 lb"]),
    ("2026-04-01 09:30", "28013 Madrid, ES (business)", "standard", "machine parts", "",
     ["3 x 60x50x50 cm, 66 kg total", "1 x 40x30x20 cm, 4.1 kg"]),
    ("2026-04-14 10:00", "20090 Ajaccio, FR (residential)", "cheapest", "lithium batteries", "",
     ["1 x 100x70x60 cm, 18.4 kg"]),
    ("2026-05-20 13:15", "1050 Brussels, BE (business)", "economy", "lithium batteries", "signature required",
     ["2 x 30x25x20 cm, 17.2 kg each"]),
    ("2026-05-27 16:40", "1010 Vienna, AT (business)", "deliver by 2026-06-02", "textiles", "",
     ["4 x 60x40x40 cm, 9.5 kg each"]),
    ("2026-06-08 10:20", "08100 Sassari, IT (residential)", "standard", "furniture", "",
     ["1 x 130x60x40 cm, 34 kg", "2 x 70x50x35 cm, 12.4 kg each"]),
    ("2026-06-15 16:30", "50667 Cologne, DE (business)", "deliver by 2026-06-16", "documents", "",
     ["1 x 35x25x10 cm, 1.2 kg"]),
]

HOLDOUT_REQUESTS = [
    ("2026-11-27 14:10", "09120 Cagliari, IT (residential)", "standard", "garden tools", "",
     ["three x 70x50x40 cm, 41.4 kg total", "1 x 20x14x10 in, 22 lb"]),
    ("2026-12-04 15:45", "18546 Sassnitz, DE (business)", "deliver by 2026-12-09", "lithium batteries",
     "signature required", ["2 x 55x45x35 cm, 16.3 kg each"]),
    ("2026-10-02 11:30", "35001 Las Palmas, ES (residential)", "economy", "lithium batteries", "",
     ["1 x 110x60x55 cm, 24.2 kg"]),
    ("2026-11-09 09:00", "75011 Paris, FR (business)", "cheapest", "display stands", "signature required",
     ["2 x 125x40x30 cm, 31.5 kg each", "1 x 60x60x60 cm, 12 kg"]),
    ("2026-11-30 16:05", "4020 Linz, AT (business)", "deliver by 2026-12-01", "laptops", "",
     ["5 x 18x13x4 in, 7.5 lb each"]),
]


def write_csv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def build_dataset(name: str, ds: dict[str, object]) -> Path:
    rng = random.Random(ds["seed"])  # type: ignore[arg-type]
    out = Path(ds["out"])  # type: ignore[arg-type]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    carriers: list[tuple] = ds["carriers"]  # type: ignore[assignment]
    write_csv(out / "carriers.csv", CARRIER_COLUMNS, [list(c) for c in carriers])
    currency = {c[0]: c[3] for c in carriers}
    fx = {k: float(v) for k, v in ds["fx"].items()}  # type: ignore[union-attr]
    services, zones, rates = [], [], []
    ranges: list[tuple] = ds["zone_ranges"]  # type: ignore[assignment]
    for cid, *_ in carriers:
        for country in COUNTRIES:
            zones.append([cid, country, "", "", f"Z{rng.randint(1, 3)}"])
            zones += [[c, k, lo, hi, z] for c, k, lo, hi, z in ranges if (c, k) == (cid, country)]
    zone_names = {(z[0], z[4]) for z in zones}
    for cid, svc, transit, dg, level, max_box in ds["services"]:  # type: ignore[union-attr]
        min_charge = round(level * rng.uniform(9, 13) / fx[currency[cid]], 2)
        services.append([cid, svc, transit, f"{min_charge:.2f}", dg, max_box])
        for zone in sorted(z for c, z in zone_names if c == cid):
            zf = 1 + 0.18 * (int(zone[1]) - 1)
            start = level * zf * rng.uniform(7, 9)
            slope = level * zf * rng.uniform(1.1, 1.5)
            for brk in FLAT_BREAKS[name]:
                price = (start + slope * brk ** 0.9) / fx[currency[cid]]
                rates.append([cid, svc, zone, brk, f"{price:.2f}", "flat"])
            last = FLAT_BREAKS[name][-1]
            per_kg = (start + slope * last ** 0.9) / last * rng.uniform(0.78, 0.9) / fx[currency[cid]]
            rates.append([cid, svc, zone, "", f"{per_kg:.2f}", "per_kg"])
    write_csv(out / "services.csv", ["carrier_id", "service", "transit_days", "min_charge", "dg_allowed", "max_box_kg"],
              services)
    write_csv(out / "zones.csv", ["carrier_id", "country", "postcode_from", "postcode_to", "zone"], zones)
    write_csv(out / "rates.csv", ["carrier_id", "service", "zone", "max_kg", "price", "rate_type"], rates)
    units: dict = ds["units"]  # type: ignore[assignment]
    sur = []
    for cid, *_ in carriers:
        f = fx[currency[cid]]
        amounts = {"RES": (rng.uniform(3, 6) / f, ""), "REM": (rng.uniform(0.35, 0.6) / f, rng.uniform(12, 20) / f),
                   "OVS": (rng.uniform(22, 40) / f, ""), "OWT": (rng.uniform(14, 24) / f, ""),
                   "DG": (rng.uniform(28, 45) / f, ""), "SIG": (rng.uniform(2, 4) / f, "")}
        for code, (amount, minimum) in amounts.items():
            unit, override = units.get((cid, code), (DEFAULT_UNITS[code], None))
            amount_s = override if override is not None else f"{amount:.2f}"
            sur.append([cid, code, amount_s, unit, f"{minimum:.2f}" if minimum != "" else ""])
    write_csv(out / "surcharges.csv", ["carrier_id", "code", "amount", "unit", "min"], sur)
    write_csv(out / "remote_areas.csv", ["carrier_id", "country", "postcode_from", "postcode_to"],
              [list(r) for r in ds["remote"]])  # type: ignore[union-attr]
    write_csv(out / "holidays.csv", ["date", "country", "name"], [list(h) for h in ds["holidays"]])  # type: ignore[union-attr]
    write_csv(out / "fx.csv", ["currency", "rate_to_eur"], [[k, v] for k, v in ds["fx"].items()])  # type: ignore[union-attr]
    return out


def request_md(n: str, req: tuple) -> str:
    ship, dest, service, contents, notes, pkgs = req
    lines = [f"# Shipment request {n}", "", f"Ship date: {ship}", f"Destination: {dest}", f"Service: {service}",
             f"Contents: {contents}"]
    if notes:
        lines.append(f"Notes: {notes}")
    lines += ["", "Packages:"] + [f"- {p}" for p in pkgs]
    return "\n".join(lines) + "\n"


def score(got: dict, want: dict) -> float:
    """Mirror of scoring/score-task3.mjs checkQuote, for the coverage proof only."""
    near = lambda a, b: isinstance(a, (int, float)) and abs(a - b) <= 0.011  # noqa: E731
    gs, ws = {s["code"]: s["amount_eur"] for s in got["surcharges"]}, {s["code"]: s["amount_eur"] for s in want["surcharges"]}
    checks = [True, got["carrier_id"] == want["carrier_id"], got["service"] == want["service"],
              near(got["billable_kg"], want["billable_kg"]), near(got["base_eur"], want["base_eur"]),
              sorted(gs) == sorted(ws), sorted(gs) == sorted(ws) and all(near(gs[c], ws[c]) for c in ws),
              near(got["fuel_eur"], want["fuel_eur"]), near(got["total_eur"], want["total_eur"]),
              got["delivery_date"] == want["delivery_date"],
              [e["code"] for e in got["exceptions"]] == [e["code"] for e in want["exceptions"]],
              [e["detail"] for e in got["exceptions"]] == [e["detail"] for e in want["exceptions"]]]
    return sum(checks) / len(checks)


def desk_log(q: dict) -> dict:
    """What the desk log records of a full quote: no base, fuel or per-surcharge amounts."""
    return {"carrier_id": q["carrier_id"], "service": q["service"], "billable_kg": q["billable_kg"],
            "surcharge_codes": [s["code"] for s in q["surcharges"]], "total_eur": q["total_eur"],
            "delivery_date": q["delivery_date"], "exceptions": q["exceptions"]}


def unique_fit(train: Path, gold_texts: dict[str, str]) -> list[frozenset[str]]:
    """Every non-empty combination of class-B and class-A switches that reproduces all gold desk logs."""
    rates = Rates(train)
    want = {k: desk_log(solve(train, t, rates=rates)) for k, t in gold_texts.items()}
    rules = CLASS_B + CLASS_A
    fits = []
    for n in range(1, len(rules) + 1):
        for combo in itertools.combinations(rules, n):
            stale = frozenset(combo)
            if all(desk_log(solve(train, t, stale, rates)) == want[k] for k, t in gold_texts.items()):
                fits.append(stale)
    return fits


def main() -> None:
    train = build_dataset("train", DATASETS["train"])
    hold = build_dataset("holdout", DATASETS["holdout"])
    inp, hdir = TASK / "input", TASK / "holdout"
    for old in list(inp.glob("shipment-*.md")) + list(inp.glob("quote-*.json")) + list(hdir.glob("shipment-*.md")):
        old.unlink()
    gold_texts, hold_texts = {}, {}
    for i, req in enumerate(GOLD_REQUESTS, 1):
        text = request_md(f"{i:02d}", req)
        gold_texts[f"{i:02d}"] = text
        (inp / f"shipment-{i:02d}.md").write_text(text, encoding="utf-8")
        (inp / f"quote-{i:02d}.json").write_text(json.dumps(desk_log(solve(train, text)), indent=2) + "\n",
                                                 encoding="utf-8")
    key = {}
    for i, req in enumerate(HOLDOUT_REQUESTS, 1):
        text = request_md(f"h{i}", req)
        hold_texts[f"h{i}"] = text
        (hdir / f"shipment-h{i}.md").write_text(text, encoding="utf-8")
        key[f"h{i}"] = {"input": f"shipment-h{i}.md", "expected": solve(hold, text)}
    (hdir / "answer-key.json").write_text(json.dumps(key, indent=2) + "\n", encoding="utf-8")
    for k, t in {**gold_texts, **hold_texts}.items():
        q = solve(hold if k.startswith("h") else train, t)
        print(k, q["carrier_id"], q["service"], q["billable_kg"], q["total_eur"], q["delivery_date"],
              [s["code"] for s in q["surcharges"]], [e["code"] for e in q["exceptions"]])
    problems = []
    for rule in CLASS_B + CLASS_C + CLASS_A:
        cls = "B" if rule in CLASS_B else "C" if rule in CLASS_C else "A"
        gold = [k for k, t in gold_texts.items()
                if desk_log(solve(train, t, frozenset({rule}))) != desk_log(solve(train, t))]
        hits = {k: score(solve(hold, t, frozenset({rule})), key[k]["expected"]) for k, t in hold_texts.items()}
        hits = {k: s for k, s in hits.items() if s < 1}
        mean = 1 - sum(1 - s for s in hits.values()) / len(hold_texts)
        print(f"{cls} {rule:13s} gold={','.join(gold) or '-':24s} holdout={','.join(hits) or '-':16s} "
              f"execution if only this is wrong={mean:.3f}")
        if cls == "B" and not gold:
            problems.append(f"class-B rule {rule} is not visible in any gold quote")
        if cls == "C" and gold:
            problems.append(f"class-C rule {rule} changes gold quote(s) {gold}")
        if cls != "A" and not hits:
            problems.append(f"rule {rule} changes no holdout score")
    others = unique_fit(train, gold_texts)
    print(f"learnability: {len(others)} wrong rule combinations reproduce every gold desk log")
    for combo in others:
        if not combo <= set(CLASS_A):
            problems.append(f"gold desk logs do not rule out {sorted(combo)}")
    if problems:
        print("COVERAGE FAIL\n- " + "\n- ".join(problems))
        sys.exit(1)
    print("COVERAGE OK")


if __name__ == "__main__":
    main()
