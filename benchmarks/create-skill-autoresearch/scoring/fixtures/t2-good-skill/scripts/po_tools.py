#!/usr/bin/env python3
"""Reference solver for task-2. Never shown to any arm.

Usage: solve.py <master-data-dir> <request.md>   -> prints the purchase order JSON

This is the ground truth the gold standards and the holdout answer key are generated from.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

APPROVAL_LIMITS: list[tuple[Decimal, str]] = [(Decimal("1000"), "auto"), (Decimal("10000"), "manager")]
SOFTWARE_MANAGER_FLOOR = Decimal("500")
PAREN_RE = re.compile(r"\s*\([^)]*\)")
LINE_RE = re.compile(r"^-\s+(.+?)\s*$")
QTY_RE = re.compile(r"^(?:(\d+)\s*x\s+|(\d+)\s+|(a pair of|a dozen|an?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+)(.+)$", re.IGNORECASE)
NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "a pair of": 2, "a dozen": 12}
OVERRIDE_RE = re.compile(r"cost center override\s+(CC-\d+)", re.IGNORECASE)


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def parse_quantity(entry: str) -> tuple[int, str]:
    m = QTY_RE.match(entry)
    if not m:
        return 1, entry
    if m.group(1) or m.group(2):
        return int(m.group(1) or m.group(2)), m.group(4)
    return NUMBER_WORDS[m.group(3).lower()], m.group(4)


def parse_request(text: str) -> dict[str, object]:
    fields: dict[str, str] = {}
    items: list[tuple[int, str]] = []
    notes = ""
    for line in text.splitlines():
        if m := LINE_RE.match(line.strip()):
            items.append(parse_quantity(m.group(1)))
        elif ":" in line and not line.startswith("-"):
            key, _, value = line.partition(":")
            fields[key.strip().lower()] = value.strip()
    notes = fields.get("notes", "")
    return {
        "requester": fields["from"],
        "department": re.search(r"\(([^)]+)\)", fields["from"]).group(1),
        "city": fields["office"],
        "items": items,
        "notes": notes,
    }


def solve(master: Path, request_text: str) -> dict[str, object]:
    catalog = read_csv(master / "catalog.csv")
    vendors = {v["vendor_id"]: v for v in read_csv(master / "vendors.csv")}
    departments = {d["department"].lower(): d["cost_center"] for d in read_csv(master / "departments.csv")}
    sites = {s["city"].lower(): s["site_code"] for s in read_csv(master / "sites.csv")}
    fx = {r["currency"]: Decimal(r["rate_to_eur"]) for r in read_csv(master / "fx.csv")}

    req = parse_request(request_text)
    override = OVERRIDE_RE.search(str(req["notes"]))
    cost_center = override.group(1) if override else departments[str(req["department"]).lower()]

    lines: list[dict[str, object]] = []
    exceptions: list[dict[str, str]] = []
    def lookup(wanted: str) -> list[dict[str, str]]:
        return [
            row for row in catalog
            if row["status"] == "active" and wanted in [a.strip().lower() for a in row["aliases"].split("|")]
        ]

    for qty, phrase in req["items"]:  # type: ignore[misc]
        wanted = PAREN_RE.sub("", phrase).strip().lower()
        matches = lookup(wanted)
        if not matches and wanted.endswith("s"):
            matches = lookup(wanted[:-1])
        if not matches:
            exceptions.append({"code": "ITEM_NOT_FOUND", "detail": phrase})
            continue
        open_vendor = [row for row in matches if vendors[row["vendor_id"]]["status"] == "active"]
        if not open_vendor:
            exceptions.append({"code": "VENDOR_BLOCKED", "detail": phrase})
            continue

        def eur(row: dict[str, str]) -> Decimal:
            discount = Decimal(vendors[row["vendor_id"]]["contract_discount_pct"]) / 100
            return money(Decimal(row["unit_price"]) * (1 - discount) * fx[row["currency"]])

        open_vendor.sort(key=lambda r: (r["preferred"] != "Y", eur(r), r["sku"]))
        pick = open_vendor[0]
        merged = next((l for l in lines if l["sku"] == pick["sku"]), None)
        if merged is not None:
            qty += int(merged["qty"])  # type: ignore[call-overload]
        min_qty = int(pick["min_order_qty"])
        if qty < min_qty:
            exceptions.append({"code": "QTY_ROUNDED", "detail": f'{pick["sku"]} {qty}->{min_qty}'})
            qty = min_qty
        unit = eur(pick)
        if merged is not None:
            merged["qty"] = qty
            merged["line_total_eur"] = float(money(unit * qty))
            continue
        lines.append({
            "sku": pick["sku"],
            "vendor_id": pick["vendor_id"],
            "qty": qty,
            "unit_price_eur": float(unit),
            "line_total_eur": float(money(unit * qty)),
            "category": pick["category"],
        })

    total = money(sum((Decimal(str(l["line_total_eur"])) for l in lines), Decimal("0")))
    approval = next((label for limit, label in APPROVAL_LIMITS if total < limit), "director")
    software = sum((Decimal(str(l["line_total_eur"])) for l in lines if l["category"] == "software"), Decimal("0"))
    if approval == "auto" and software >= SOFTWARE_MANAGER_FLOOR:
        approval = "manager"
    it_review = any(l["category"] == "software" for l in lines)
    for line in lines:
        del line["category"]
    return {
        "site_code": sites[str(req["city"]).lower()],
        "cost_center": cost_center,
        "lines": lines,
        "total_eur": float(total),
        "approval": approval,
        "it_review": it_review,
        "exceptions": exceptions,
    }


if __name__ == "__main__":
    master_dir, request_path = Path(sys.argv[1]), Path(sys.argv[2])
    print(json.dumps(solve(master_dir, request_path.read_text(encoding="utf-8")), indent=2))
