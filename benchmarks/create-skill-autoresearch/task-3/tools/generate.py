#!/usr/bin/env python3
"""Deterministic generator for task-3 (synthetic, generic data). Never shown to any arm.

Usage: generate.py   (writes ../input, ../holdout; idempotent for fixed seeds)

The holdout rate tables use other carriers, divisors, cutoffs, fuel rates, currencies, holidays, zones
and remote areas than the training tables, so a skill that memorises gold outputs fails. After writing,
it prints which shipments each stale-guide (class B) and gold-fitting (class A) rule changes, and exits
non-zero if a class-B rule is invisible in the gold standards or any rule leaves every holdout unchanged.
"""
from __future__ import annotations

import csv
import json
import random
import shutil
import sys
from pathlib import Path

from solve import solve

HERE = Path(__file__).resolve().parent
TASK = HERE.parent

CLASS_B = ["divisor", "cutoff", "holidays", "whole_kg", "per_kg_total", "fuel_base", "girth", "selection",
           "dg_upgrade", "deadline"]
CLASS_A = ["sum_round", "owt_billable", "fx_total"]
COUNTRIES = ["FR", "DE", "ES", "IT", "BE", "AT"]
FLAT_BREAKS = {"train": [1, 2, 5, 10, 15, 20, 30, 50, 70], "holdout": [0.5, 1, 3, 5, 10, 20, 25, 40, 60]}

DATASETS: dict[str, dict[str, object]] = {
    "train": {
        "seed": 31,
        "out": TASK / "input" / "rates",
        # carrier_id, name, status, currency, dim_divisor, fuel_pct, cutoff, preferred
        "carriers": [
            ("C01", "Atlas Parcel", "active", "EUR", "5000", "12.5", "16:00", "Y"),
            ("C02", "Nordic Express", "active", "GBP", "4000", "9", "17:30", "N"),
            ("C03", "Iberia Freight", "active", "EUR", "6000", "15", "14:00", "N"),
            ("C04", "Orion Logistics", "suspended", "EUR", "5000", "10", "17:00", "N"),
        ],
        # carrier, service, transit_days, dg_allowed, price level
        "services": [
            ("C01", "express", 1, "Y", 1.9), ("C01", "standard", 3, "Y", 1.0), ("C01", "economy", 5, "N", 0.75),
            ("C02", "express", 2, "Y", 1.45), ("C02", "standard", 4, "Y", 0.8),
            ("C03", "standard", 3, "Y", 0.9), ("C03", "economy", 6, "N", 0.62),
            ("C04", "express", 1, "Y", 1.2), ("C04", "standard", 2, "Y", 0.5), ("C04", "economy", 4, "Y", 0.4),
        ],
        "remote": [("C01", "FR", 20000, 20999), ("C02", "FR", 20000, 20999), ("C03", "FR", 20000, 20999),
                   ("C01", "IT", 7000, 9999), ("C02", "IT", 7000, 9999), ("C03", "IT", 8000, 9999),
                   ("C01", "ES", 7000, 7999), ("C03", "ES", 7000, 7999), ("C02", "AT", 6500, 6999)],
        "holidays": [("2026-04-03", "Good Friday"), ("2026-04-25", "Freedom Day"), ("2026-05-01", "Labour Day"),
                     ("2026-06-04", "Corpus Christi"), ("2026-06-10", "Portugal Day")],
        "fx": {"EUR": "1", "GBP": "1.17", "USD": "0.92"},
    },
    "holdout": {
        "seed": 47,
        "out": TASK / "holdout" / "rates",
        "carriers": [
            ("K11", "Kestrel Cargo", "active", "CHF", "4500", "11", "15:30", "N"),
            ("K12", "Delta Courier", "active", "EUR", "6000", "14.5", "18:00", "Y"),
            ("K13", "Polar Post", "suspended", "EUR", "5000", "8", "17:00", "N"),
            ("K14", "Meridian Freight", "active", "EUR", "5500", "17", "13:30", "N"),
        ],
        "services": [
            ("K11", "express", 2, "Y", 1.5), ("K11", "standard", 3, "Y", 0.85), ("K11", "economy", 6, "N", 0.6),
            ("K12", "express", 1, "Y", 1.85), ("K12", "standard", 2, "Y", 1.05), ("K12", "economy", 5, "N", 0.72),
            ("K13", "express", 1, "Y", 1.1), ("K13", "standard", 2, "Y", 0.5), ("K13", "economy", 3, "Y", 0.4),
            ("K14", "standard", 4, "Y", 0.8), ("K14", "economy", 7, "N", 0.55),
        ],
        "remote": [("K11", "IT", 9000, 9999), ("K12", "IT", 9000, 9999), ("K14", "IT", 9000, 9999),
                   ("K11", "DE", 18500, 18599), ("K12", "DE", 18500, 18599), ("K14", "DE", 18000, 18999),
                   ("K11", "ES", 35000, 35999), ("K12", "ES", 35000, 35999), ("K14", "ES", 35000, 35999),
                   ("K12", "FR", 20000, 20999)],
        "holidays": [("2026-10-05", "Republic Day"), ("2026-11-01", "All Saints"), ("2026-12-01", "Restoration"),
                     ("2026-12-08", "Immaculate Conception"), ("2026-12-25", "Christmas Day")],
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
    ("2026-06-08 10:20", "8100 Sassari, IT (residential)", "standard", "furniture", "",
     ["1 x 130x60x40 cm, 34 kg", "2 x 60x40x35 cm, 21.3 kg each"]),
    ("2026-06-15 16:30", "50667 Cologne, DE (business)", "deliver by 2026-06-16", "documents", "",
     ["1 x 35x25x10 cm, 1.2 kg"]),
]

HOLDOUT_REQUESTS = [
    ("2026-11-27 14:10", "9120 Cagliari, IT (residential)", "standard", "garden tools", "",
     ["3 x 70x50x40 cm, 41.4 kg total", "1 x 20x14x10 in, 22 lb"]),
    ("2026-12-04 15:45", "18546 Sassnitz, DE (business)", "deliver by 2026-12-10", "lithium batteries",
     "signature required", ["2 x 55x45x35 cm, 16.6 kg each"]),
    ("2026-10-02 11:30", "35001 Las Palmas, ES (residential)", "economy", "lithium batteries", "",
     ["1 x 110x60x55 cm, 24.2 kg"]),
    ("2026-12-21 09:00", "75011 Paris, FR (business)", "cheapest", "display stands", "signature required",
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
    write_csv(out / "carriers.csv",
              ["carrier_id", "name", "status", "currency", "dim_divisor", "fuel_pct", "cutoff", "preferred"],
              [list(c) for c in carriers])
    currency = {c[0]: c[3] for c in carriers}
    fx = {k: float(v) for k, v in ds["fx"].items()}  # type: ignore[union-attr]
    services = []
    zones = []
    rates = []
    for cid, *_ in carriers:
        for country in COUNTRIES:
            zones.append([cid, country, f"Z{rng.randint(1, 4)}"])
    zone_of = {(z[0], z[1]): z[2] for z in zones}
    for cid, svc, transit, dg, level in ds["services"]:  # type: ignore[union-attr]
        min_charge = round(level * rng.uniform(9, 13) / fx[currency[cid]], 2)
        services.append([cid, svc, transit, f"{min_charge:.2f}", dg])
        for zone in sorted({z for (c, _), z in zone_of.items() if c == cid}):
            zf = 1 + 0.18 * (int(zone[1]) - 1)
            start = level * zf * rng.uniform(7, 9)
            slope = level * zf * rng.uniform(1.1, 1.5)
            for brk in FLAT_BREAKS[name]:
                price = (start + slope * brk ** 0.9) / fx[currency[cid]]
                rates.append([cid, svc, zone, brk, f"{price:.2f}", "flat"])
            last = FLAT_BREAKS[name][-1]
            per_kg = (start + slope * last ** 0.9) / last * rng.uniform(0.78, 0.9) / fx[currency[cid]]
            rates.append([cid, svc, zone, "", f"{per_kg:.2f}", "per_kg"])
    write_csv(out / "services.csv", ["carrier_id", "service", "transit_days", "min_charge", "dg_allowed"], services)
    write_csv(out / "zones.csv", ["carrier_id", "country", "zone"], zones)
    write_csv(out / "rates.csv", ["carrier_id", "service", "zone", "max_kg", "price", "rate_type"], rates)
    sur = []
    for cid, *_ in carriers:
        f = fx[currency[cid]]
        sur += [
            [cid, "RES", f"{rng.uniform(3, 6) / f:.2f}", "shipment", ""],
            [cid, "REM", f"{rng.uniform(0.35, 0.6) / f:.2f}", "kg", f"{rng.uniform(12, 20) / f:.2f}"],
            [cid, "OVS", f"{rng.uniform(22, 40) / f:.2f}", "package", ""],
            [cid, "OWT", f"{rng.uniform(14, 24) / f:.2f}", "package", ""],
            [cid, "DG", f"{rng.uniform(28, 45) / f:.2f}", "shipment", ""],
            [cid, "SIG", f"{rng.uniform(2, 4) / f:.2f}", "shipment", ""],
        ]
    write_csv(out / "surcharges.csv", ["carrier_id", "code", "amount", "unit", "min"], sur)
    write_csv(out / "remote_areas.csv", ["carrier_id", "country", "postcode_from", "postcode_to"],
              [list(r) for r in ds["remote"]])  # type: ignore[union-attr]
    write_csv(out / "holidays.csv", ["date", "name"], [list(h) for h in ds["holidays"]])  # type: ignore[union-attr]
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


def coverage(master: Path, texts: dict[str, str]) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for rule in CLASS_B + CLASS_A:
        hits[rule] = [k for k, t in texts.items() if solve(master, t, frozenset({rule})) != solve(master, t)]
    return hits


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
        (inp / f"quote-{i:02d}.json").write_text(json.dumps(solve(train, text), indent=2) + "\n", encoding="utf-8")
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
    gold_hits, hold_hits = coverage(train, gold_texts), coverage(hold, hold_texts)
    problems = []
    for rule in CLASS_B + CLASS_A:
        cls = "B" if rule in CLASS_B else "A"
        print(f"{cls} {rule:13s} gold={','.join(gold_hits[rule]) or '-':24s} holdout={','.join(hold_hits[rule]) or '-'}")
        if cls == "B" and not gold_hits[rule]:
            problems.append(f"class-B rule {rule} is not visible in any gold standard")
        if not hold_hits[rule]:
            problems.append(f"rule {rule} changes no holdout answer")
    if problems:
        print("COVERAGE FAIL\n- " + "\n- ".join(problems))
        sys.exit(1)
    print("COVERAGE OK")


if __name__ == "__main__":
    main()
