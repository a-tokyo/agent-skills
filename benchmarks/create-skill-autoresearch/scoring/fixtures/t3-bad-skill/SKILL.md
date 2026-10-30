---
name: freight-quote-builder
description: Builds a parcel freight quote JSON from a free-text shipment request and carrier rate tables. Use when asked to quote a shipment or turn a shipment request into a freight quote.
---

# Freight quote builder (follows the written rating guide only)

Run the bundled tool from the directory holding `shipment.md` and `rates/`:

```bash
python3 ~/.claude/skills/freight-quote-builder/scripts/quote_tools.py rates shipment.md \
  --stale divisor,cutoff,holidays,whole_kg,per_kg_total,fuel_base,girth,selection,dg_upgrade,deadline > quote.json
```

`scripts/quote_tools.py` applies the two-year-old written guide's rules everywhere the desk's practice
has moved on. It is the known-bad baseline: what a builder gets by trusting the guide over the gold quotes.
