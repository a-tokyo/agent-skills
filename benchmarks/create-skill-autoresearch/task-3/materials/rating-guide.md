# Parcel rating guide

The shipping desk quotes outbound parcel shipments from the Porto warehouse (Portugal, `PT`). Rate
tables come from the carriers' data feed and are replaced every month.

This guide has two parts. **Part 1, the request and rate-table format, is maintained by the data team
with the feed and is current.** Part 2, the rating rules, was written two years ago.

# Part 1 — formats (current)

## Shipment request

```
# Shipment request <id>

Ship date: <YYYY-MM-DD HH:MM>          local time the parcels are ready at the dock
Destination: <postcode> <City>, <CC> (residential|business)
Service: express | standard | economy | cheapest | deliver by <YYYY-MM-DD>
Contents: <free text>
Notes: <free text, optional>

Packages:
- <count> x <L>x<W>x<H> cm|in, <weight> kg|lb [each|total]
```

A package line describes `count` identical boxes; `count` is digits or a word from `one` to `ten`.
`each` (the default) is the weight of one box; `total` is the weight of all `count` boxes together.
Inches and pounds are converted with 1 in = 2.54 cm and 1 lb = 0.45359237 kg before anything else.
Postcodes are compared as integers (`09120` is 9120).

## Rate tables (`rates/`)

| File | Columns |
|------|---------|
| `carriers.csv` | `carrier_id, name, status, currency, dim_divisor, round_step_kg, heavy_kg, oversize_cm, girth_cm, owt_kg, fuel_pct, cutoff, preferred` |
| `services.csv` | `carrier_id, service, transit_days, min_charge, dg_allowed, max_box_kg` |
| `zones.csv` | `carrier_id, country, postcode_from, postcode_to, zone` |
| `rates.csv` | `carrier_id, service, zone, max_kg, price, rate_type` |
| `surcharges.csv` | `carrier_id, code, amount, unit, min` |
| `remote_areas.csv` | `carrier_id, country, postcode_from, postcode_to` |
| `holidays.csv` | `date, country, name` |
| `fx.csv` | `currency, rate_to_eur` |

Column meanings:

- `carriers.csv`: only `status` = `active` carriers may be used. `currency` is the currency of every
  price, minimum and surcharge amount of that carrier. `round_step_kg`, `heavy_kg`, `oversize_cm`,
  `girth_cm` and `owt_kg` are that carrier's weight-rounding step and its heavy-box, oversize,
  length-plus-girth and overweight thresholds; carriers set their own. A box is heavy when the
  greater of its actual and volumetric weight, before rounding, is over `heavy_kg`. `cutoff` is the latest
  `HH:MM` the carrier collects the same day. `preferred` is `Y` or `N`.
- `services.csv`: `max_box_kg` is the heaviest **actual** box weight the service accepts; a shipment
  with a heavier box cannot use that service. `dg_allowed` is `Y` when the service carries
  dangerous goods.
- `zones.csv`: a row with blank `postcode_from`/`postcode_to` gives the carrier's zone for the whole
  country. A row with a postcode range gives the zone for postcodes inside the range and wins over
  the country-wide row.
- `rates.csv`: `rate_type` is `flat` (the price for any billable weight up to `max_kg`) or `per_kg`
  (one row per carrier, service and zone, blank `max_kg`, used above the largest flat break).
- `surcharges.csv`: `unit` says how `amount` is applied — `shipment` (once), `package` (per box the
  surcharge applies to: every box, or for `OVS`/`OWT` only the qualifying boxes), `kg` (per billable
  kg of the shipment) or `pct_base` (`amount` percent of the base charge). When `min` is set, the
  surcharge is at least `min`.
- `holidays.csv`: public holidays by `country`. A business day is Monday to Friday and not a holiday
  in `PT` or in the destination country; other countries' holidays do not matter.

## Output schema (`quote.json`)

```json
{
  "carrier_id": "string",
  "service": "express|standard|economy",
  "billable_kg": 0.0,
  "base_eur": 0.0,
  "surcharges": [ { "code": "string", "amount_eur": 0.0 } ],
  "fuel_eur": 0.0,
  "total_eur": 0.0,
  "delivery_date": "YYYY-MM-DD",
  "exceptions": [ { "code": "string", "detail": "string" } ]
}
```

`surcharges` lists only the surcharges that apply, in the order RES, REM, OVS, OWT, DG, SIG.
`exceptions` is empty when the shipment was quoted as requested.

# Part 2 — rating rules (written two years ago)

1. **Volumetric weight** of a box = L × W × H (cm) / 5000.
2. **Billable weight** of a box = the greater of its actual and volumetric weight, rounded **up** to the
   carrier's `round_step_kg`. Round each box on its own, then add the boxes up: the shipment's
   billable weight is the sum.
3. **Base charge**: find the carrier's zone for the destination, then the `flat` row of `rates.csv`
   for that carrier, service and zone with the smallest `max_kg` that is at least the billable
   weight. Above the largest flat break, the price is the largest flat price plus the `per_kg` row's
   price for every kilogram above that break. The base charge is never below the service's
   `min_charge`.
4. **Surcharges** (`surcharges.csv`) are added when they apply:
   - `RES` residential delivery
   - `REM` remote area, when the destination postcode falls in one of the carrier's
     `remote_areas.csv` ranges for that country
   - `OVS` oversize, for boxes whose longest side is over the carrier's `oversize_cm`
   - `OWT` overweight, for boxes whose **actual** weight is over the carrier's `owt_kg`
   - `DG` dangerous goods, when the contents are lithium batteries
   - `SIG` signature on delivery, when the notes ask for a signature
5. **Fuel surcharge** = the carrier's `fuel_pct` of the base charge.
6. **Rounding and currency**: every charge (base, each surcharge, fuel) is computed in the carrier's
   currency and rounded half-up to cents; each is then converted to EUR with `fx.csv` and rounded
   half-up to cents again. `total_eur` is the sum of the EUR amounts. Fuel is computed from the
   rounded carrier-currency amounts.
7. **Choosing a carrier**: use the preferred carrier (`preferred` = `Y`) if it offers the requested
   service to the destination; otherwise the carrier with the lowest base charge.
   `cheapest` means any service may be used. `deliver by` means any service that arrives on or before
   that date.
8. **Dangerous goods** may only travel on a service whose `dg_allowed` is `Y`. If no carrier allows
   them on the requested service, do not quote: report `DG_NOT_ALLOWED`.
9. **Delivery date**: parcels ready before the 17:00 pickup cutoff leave the same day, later ones the
   next business day. Add the service's `transit_days` in business days.

Everything else follows the desk's established practice — see past quotes.
