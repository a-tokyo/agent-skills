#!/usr/bin/env python3
"""Deterministic generator for task-2 (synthetic, generic data). Never shown to any arm.

Usage: generate.py   (writes ../input, ../holdout; idempotent for a fixed seed)

Each dataset gives the scenario roles to DIFFERENT item groups, so a skill that memorises the gold
outputs instead of reading the master data at run time fails the holdout.
"""
from __future__ import annotations

import csv
import json
import random
import shutil
from pathlib import Path

from solve import solve

HERE = Path(__file__).resolve().parent
TASK = HERE.parent

GROUPS: dict[str, tuple[str, str, int]] = {
    "monitor": ("27-inch monitor|27 inch monitor|monitor 27", "hardware", 260),
    "laptop": ("14-inch laptop|laptop 14", "hardware", 1400),
    "dock": ("usb-c dock|docking station", "hardware", 180),
    "chair": ("ergonomic chair|office chair", "furniture", 400),
    "desk": ("standing desk|sit-stand desk", "furniture", 600),
    "design": ("figma license|design tool license", "software", 150),
    "tracker": ("jira seat|issue tracker seat", "software", 90),
    "hdmi": ("hdmi cable|hdmi lead", "hardware", 8),
    "headset": ("headset|noise-cancelling headset", "hardware", 120),
    "consulting": ("consulting day|consultant day", "services", 900),
    "webcam": ("webcam|usb webcam", "hardware", 70),
    "toner": ("printer toner|toner cartridge", "hardware", 45),
    "whiteboard": ("whiteboard|mobile whiteboard", "furniture", 300),
    "pdf": ("pdf editor license", "software", 60),
    "projector": ("projector|meeting room projector", "hardware", 700),
}

DATASETS: dict[str, dict[str, object]] = {
    "train": {
        "seed": 11,
        "out": TASK / "input" / "master-data",
        "blocked": {"V004", "V011"},
        "discounts": {"V003": "10", "V008": "10", "V010": "5"},
        "fx": {"EUR": "1", "USD": "0.92", "GBP": "1.17"},
        "departments": {"Marketing": "CC-2100", "Engineering": "CC-3100", "Finance": "CC-1100",
                        "Sales": "CC-4100", "People": "CC-5100"},
        "sites": {"Porto": "PT-OPO", "Lisbon": "PT-LIS", "Madrid": "ES-MAD", "London": "GB-LON",
                  "Berlin": "DE-BER"},
        "roles": {"monitor": "preferred_not_cheapest", "dock": "fx_trick", "chair": "cheapest_blocked",
                  "whiteboard": "all_blocked", "pdf": "discontinued_only", "hdmi": "min_qty",
                  "toner": "min_qty"},
        "fx_trick_ccy": "USD",
    },
    "holdout": {
        "seed": 23,
        "out": TASK / "holdout" / "master-data",
        "blocked": {"V002", "V009"},
        "discounts": {"V005": "12.5", "V013": "10", "V007": "5"},
        "fx": {"EUR": "1", "USD": "0.88", "GBP": "1.21", "CHF": "1.04"},
        "departments": {"Marketing": "CC-7210", "Engineering": "CC-7310", "Finance": "CC-7110",
                        "Sales": "CC-7410", "People": "CC-7510"},
        "sites": {"Porto": "OPO-1", "Lisbon": "LIS-2", "Madrid": "MAD-1", "London": "LON-3",
                  "Berlin": "BER-1"},
        "roles": {"headset": "preferred_not_cheapest", "webcam": "fx_trick", "desk": "cheapest_blocked",
                  "projector": "all_blocked", "toner": "min_qty", "monitor": "discontinued_cheapest"},
        "fx_trick_ccy": "CHF",
    },
}

GOLD_REQUESTS = [
    ("Ana Silva (Marketing)", "Porto", [("two", "27-inch monitors (black)"), (1, "usb-c dock"), ("a", "figma license")], ""),
    ("Rui Costa (Engineering)", "Lisbon", [(9, "14-inch laptop"), (5, "docking station"), (10, "hdmi cable")],
     "Project Atlas onboarding. cost center override CC-9001"),
    ("Marta Gil (Finance)", "Madrid", [(3, "office chair"), ("a pair of", "hdmi leads"), (1, "hdmi cable")], ""),
    ("Tom Reed (Sales)", "London", [(2, "whiteboard"), (1, "pdf editor license"), (1, "headset")], ""),
    ("Lena Vogt (People)", "Berlin", [(4, "standing desk"), ("six", "ergonomic chairs"), (2, "consulting days")],
     "New team area."),
    ("Joao Reis (Engineering)", "Porto", [(3, "jira seat"), (1, "laser cutter"), (2, "webcam"), (1, "printer toner")],
     ""),
    ("Sofia Lopes (People)", "Lisbon", [(4, "figma licenses"), (1, "webcam")], "For the new design hires."),
]

HOLDOUT_REQUESTS = [
    ("Ines Moura (Marketing)", "Lisbon", [("three", "noise-cancelling headsets"), ("a pair of", "usb webcams"), (1, "design tool license")],
     ""),
    ("Paul Weber (Sales)", "Berlin", [(2, "sit-stand desk"), ("a", "meeting room projector"), (1, "toner cartridge"), (1, "printer toner")],
     "Trade fair prep. cost center override CC-8800"),
    ("Clara Diaz (Finance)", "London", [(8, "laptop 14"), ("a dozen", "docking stations"), (1, "issue tracker seat")], ""),
    ("Omar Haddad (People)", "Madrid", [(1, "monitor 27"), (1, "3d printer"), (1, "pdf editor license"),
                                        (1, "consultant day")], ""),
    ("Nuno Alves (Engineering)", "Porto", [(6, "issue tracker seats"), (1, "headset (wireless)")], ""),
]


def sku_rows(rng: random.Random, group: str, role: str, ds: dict[str, object], vendor_ids: list[str]) -> list[dict[str, str]]:
    aliases, category, base = GROUPS[group]
    blocked = sorted(ds["blocked"])  # type: ignore[arg-type]
    open_vendors = [v for v in vendor_ids if v not in ds["blocked"]]  # type: ignore[operator]
    prefix = group[:3].upper()

    def row(i: int, vendor: str, price: float, ccy: str = "EUR", preferred: str = "N",
            min_qty: int = 1, status: str = "active") -> dict[str, str]:
        return {"sku": f"{prefix}-{rng.randint(100, 999)}{chr(65 + i)}", "name": f"{group.title()} model {chr(65 + i)}",
                "aliases": aliases, "category": category, "vendor_id": vendor, "unit_price": f"{price:.2f}",
                "currency": ccy, "min_order_qty": str(min_qty), "preferred": preferred, "status": status}

    vs = rng.sample(open_vendors, 3)
    p = [round(base * f, 2) for f in (rng.uniform(0.85, 0.95), rng.uniform(1.0, 1.1), rng.uniform(1.15, 1.3))]
    if role == "preferred_not_cheapest":
        return [row(0, vs[0], p[0]), row(1, vs[1], p[1]), row(2, vs[2], p[2], preferred="Y")]
    if role == "fx_trick":
        ccy = str(ds["fx_trick_ccy"])
        rate = float(ds["fx"][ccy])  # type: ignore[index]
        # The cheapest row in EUR must differ from the cheapest by nominal price.
        if rate < 1:
            foreign = round(p[0] * 1.03, 2)  # 103 USD * 0.92 = 94.8 EUR < 100 EUR
        else:
            foreign = round(p[0] * 0.98, 2)  # 98 CHF * 1.04 = 101.9 EUR > 100 EUR
        return [row(0, vs[0], p[0]), row(1, vs[1], foreign, ccy=ccy), row(2, vs[2], p[2], ccy="GBP")]
    if role == "cheapest_blocked":
        return [row(0, blocked[0], round(p[0] * 0.8, 2)), row(1, vs[1], p[1]), row(2, vs[2], p[2])]
    if role == "all_blocked":
        return [row(0, blocked[0], p[0]), row(1, blocked[1], p[1])]
    if role == "discontinued_only":
        return [row(0, vs[0], p[0], status="discontinued"), row(1, vs[1], p[1], status="discontinued")]
    if role == "discontinued_cheapest":
        return [row(0, vs[0], round(p[0] * 0.7, 2), status="discontinued"), row(1, vs[1], p[1]), row(2, vs[2], p[2])]
    if role == "min_qty":
        return [row(0, vs[0], p[0], preferred="Y", min_qty=10 if base < 20 else 5), row(1, vs[1], p[1], min_qty=20)]
    pref = rng.random() < 0.5
    return [row(0, vs[0], p[0]), row(1, vs[1], p[1], preferred="Y" if pref else "N"), row(2, vs[2], p[2])]


def filler(rng: random.Random, vendor_ids: list[str], n: int) -> list[dict[str, str]]:
    nouns = ["reagent", "valve", "bracket", "sensor", "gasket", "label roll", "binder", "lanyard", "pallet",
             "cable tie", "battery pack", "router", "switch", "patch panel", "stapler", "shredder", "kettle"]
    rows = []
    for i in range(n):
        noun = rng.choice(nouns)
        code = f"{rng.choice('ABCDEFGHJKLMNPRSTUVWXYZ')}{rng.randint(10, 999)}"
        rows.append({"sku": f"GEN-{i:04d}", "name": f"{noun.title()} {code}", "aliases": f"{noun} {code.lower()}",
                     "category": rng.choice(["hardware", "consumables", "facilities"]),
                     "vendor_id": rng.choice(vendor_ids), "unit_price": f"{rng.uniform(2, 900):.2f}",
                     "currency": rng.choice(["EUR", "EUR", "USD", "GBP"]),
                     "min_order_qty": str(rng.choice([1, 1, 1, 5, 10])), "preferred": rng.choice(["N", "N", "Y"]),
                     "status": rng.choice(["active", "active", "active", "discontinued"])})
    return rows


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def build_dataset(ds: dict[str, object]) -> Path:
    rng = random.Random(ds["seed"])  # type: ignore[arg-type]
    out = Path(ds["out"])  # type: ignore[arg-type]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    vendor_ids = [f"V{i:03d}" for i in range(1, 15)]
    vendors = [{"vendor_id": v, "name": f"{rng.choice(['Nordic', 'Iberia', 'Atlas', 'Delta', 'Kestrel', 'Orion'])} "
                f"{rng.choice(['Supply', 'Trading', 'Systems', 'Office', 'Partners'])} {v[-2:]}",
                "status": "blocked" if v in ds["blocked"] else "active",
                "contract_discount_pct": ds["discounts"].get(v, "0")} for v in vendor_ids]  # type: ignore[operator,union-attr]
    roles: dict[str, str] = ds["roles"]  # type: ignore[assignment]
    catalog = [r for g in GROUPS for r in sku_rows(rng, g, roles.get(g, "plain"), ds, vendor_ids)]
    catalog += filler(rng, vendor_ids, 260)
    rng.shuffle(catalog)
    write_csv(out / "catalog.csv", catalog)
    write_csv(out / "vendors.csv", vendors)
    write_csv(out / "departments.csv", [{"department": k, "cost_center": v} for k, v in ds["departments"].items()])  # type: ignore[union-attr]
    write_csv(out / "sites.csv", [{"city": k, "site_code": v} for k, v in ds["sites"].items()])  # type: ignore[union-attr]
    write_csv(out / "fx.csv", [{"currency": k, "rate_to_eur": v} for k, v in ds["fx"].items()])  # type: ignore[union-attr]
    return out


def request_md(n: str, who: str, city: str, items: list[tuple[int, str]], notes: str) -> str:
    lines = [f"# Purchase request {n}", "", f"From: {who}", f"Office: {city}"]
    if notes:
        lines.append(f"Notes: {notes}")
    lines += ["", "Items:"] + [f"- {q} {phrase}" if isinstance(q, str) else f"- {q} x {phrase}" for q, phrase in items]
    return "\n".join(lines) + "\n"


def main() -> None:
    train = build_dataset(DATASETS["train"])
    hold = build_dataset(DATASETS["holdout"])
    inp, hdir = TASK / "input", TASK / "holdout"
    for old in list(inp.glob("request-*.md")) + list(inp.glob("po-*.json")) + list(hdir.glob("request-*.md")):
        old.unlink()
    for i, (who, city, items, notes) in enumerate(GOLD_REQUESTS, 1):
        text = request_md(f"{i:02d}", who, city, items, notes)
        (inp / f"request-{i:02d}.md").write_text(text, encoding="utf-8")
        (inp / f"po-{i:02d}.json").write_text(json.dumps(solve(train, text), indent=2) + "\n", encoding="utf-8")
    key = {}
    for i, (who, city, items, notes) in enumerate(HOLDOUT_REQUESTS, 1):
        text = request_md(f"h{i}", who, city, items, notes)
        (hdir / f"request-h{i}.md").write_text(text, encoding="utf-8")
        key[f"h{i}"] = {"input": f"request-h{i}.md", "expected": solve(hold, text)}
    (hdir / "answer-key.json").write_text(json.dumps(key, indent=2) + "\n", encoding="utf-8")
    for f in sorted(inp.glob("po-*.json")):
        po = json.loads(f.read_text())
        print(f.name, po["total_eur"], po["approval"], po["it_review"], [e["code"] for e in po["exceptions"]])
    for k, v in key.items():
        e = v["expected"]
        print(k, e["total_eur"], e["approval"], e["it_review"], [x["code"] for x in e["exceptions"]])


if __name__ == "__main__":
    main()
