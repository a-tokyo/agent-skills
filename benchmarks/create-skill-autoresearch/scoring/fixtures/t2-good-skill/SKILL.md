---
name: purchase-order-builder
description: Builds a purchase order JSON from a free-text purchase request and master-data CSVs. Use when asked to turn a purchase request into a purchase order.
---

# Purchase order builder

Run the bundled tool from the directory holding `request.md` and `master-data/`:

```bash
python3 ~/.claude/skills/purchase-order-builder/scripts/po_tools.py master-data request.md > po.json
```

`scripts/po_tools.py` parses the request, looks every item up by alias, applies the purchasing
rules and prints the purchase order. Check that `po.json` is valid JSON before finishing.
