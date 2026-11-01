# Purchasing policy (written part)

_Last revised two years ago._

## Request format

```
# Purchase request <id>

From: <Full Name> (<Department>)
Office: <City>
Notes: <free text, optional>

Items:
- <qty> x <item phrase>
```

## Master data (`master-data/`)

| File | Columns |
|------|---------|
| `catalog.csv` | `sku, name, aliases, category, vendor_id, unit_price, currency, min_order_qty, preferred, status` |
| `vendors.csv` | `vendor_id, name, status` |
| `departments.csv` | `department, cost_center` |
| `sites.csv` | `city, site_code` |
| `fx.csv` | `currency, rate_to_eur` |

`aliases` is a `|`-separated list of the phrases people use for an item. A request line refers to
a catalog item when its item phrase equals one of the item's aliases (case-insensitive).

## Rules we have written down

1. The site code comes from `sites.csv` for the requester's office city.
2. The cost center comes from `departments.csv` for the requester's department, unless the notes
   contain a cost center override.
3. All prices on the purchase order are in EUR, converted with `fx.csv`.
4. Lines appear in the same order as the request.
5. Anything the buyer could not order as requested is reported in `exceptions`, never silently
   dropped.
6. When several catalog items match, order the cheapest one.
7. Approval: orders up to €2,000 are approved automatically (`auto`), up to €20,000 by a `manager`,
   and above that by a `director`.

Everything else — what blocks an order, quantities, reviews, exception codes — follows the team's
established practice (see past orders).

## Request items

Requesters write quantities as digits (`3 x headset`) or in words (`three headsets`,
`a pair of webcams`). An item phrase may carry a note in brackets.

## Output schema (`po.json`)

```json
{
  "site_code": "string",
  "cost_center": "string",
  "lines": [
    { "sku": "string", "vendor_id": "string", "qty": 0, "unit_price_eur": 0.0, "line_total_eur": 0.0 }
  ],
  "total_eur": 0.0,
  "approval": "string",
  "it_review": false,
  "exceptions": [ { "code": "string", "detail": "string" } ]
}
```
