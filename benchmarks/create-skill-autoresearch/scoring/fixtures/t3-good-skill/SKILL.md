---
name: freight-quote-builder
description: Builds a parcel freight quote JSON from a free-text shipment request and carrier rate tables. Use when asked to quote a shipment or turn a shipment request into a freight quote.
---

# Freight quote builder

Run the bundled tool from the directory holding `shipment.md` and `rates/`:

```bash
python3 ~/.claude/skills/freight-quote-builder/scripts/quote_tools.py rates shipment.md > quote.json
```

`scripts/quote_tools.py` parses the request, rates it with every active carrier and service, applies
the desk's rating rules and prints the cheapest quote. Check that `quote.json` is valid JSON before
finishing.
