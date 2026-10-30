# Parcel rating guide (written part)

_Last revised two years ago._

The shipping desk quotes outbound parcel shipments from the Porto warehouse. Rate tables come from the
carriers and are replaced every month.

## Shipment request format

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

A package line describes `count` identical boxes. `each` (the default) is the weight of one box;
`total` is the weight of all `count` boxes together. Inches and pounds are converted with
1 in = 2.54 cm and 1 lb = 0.45359237 kg before anything else.

## Rate tables (`rates/`)

| File | Columns |
|------|---------|
| `carriers.csv` | `carrier_id, name, status, currency, fuel_pct, preferred` |
| `services.csv` | `carrier_id, service, transit_days, min_charge, dg_allowed` |
| `zones.csv` | `carrier_id, country, zone` |
| `rates.csv` | `carrier_id, service, zone, max_kg, price, rate_type` |
| `surcharges.csv` | `carrier_id, code, amount, unit, min` |
| `remote_areas.csv` | `carrier_id, country, postcode_from, postcode_to` |
| `fx.csv` | `currency, rate_to_eur` |

Only carriers with `status` = `active` may be used. Prices, minimums and surcharge amounts are in the
carrier's `currency`.

## Rules we have written down

1. **Volumetric weight** of a box = L × W × H (cm) / 5000.
2. **Billable weight** of a box = the greater of its actual and volumetric weight, rounded **up** to the
   next 0.5 kg. Round each box on its own, then add the boxes up: the shipment's billable weight is the
   sum.
3. **Base charge**: find the carrier's zone for the destination country, then the `flat` row of
   `rates.csv` for that carrier, service and zone with the smallest `max_kg` that is at least the
   billable weight. Above the largest flat break, the price is the largest flat price plus the
   `per_kg` row's price for every kilogram above that break. The base charge is never below the
   service's `min_charge`.
4. **Surcharges** (`surcharges.csv`, codes below) are added when they apply:
   - `RES` residential delivery, once per shipment
   - `REM` remote area, when the destination postcode falls in one of the carrier's `remote_areas.csv`
     ranges for that country: `amount` per billable kg, at least `min`
   - `OVS` oversize, per box whose longest side is over 120 cm
   - `OWT` overweight, per box whose **actual** weight is over 30 kg
   - `DG` dangerous goods, once per shipment, when the contents are lithium batteries
   - `SIG` signature on delivery, once per shipment, when the notes ask for a signature
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
   next business day. Add the service's `transit_days` in business days (Monday to Friday).

Everything else follows the desk's established practice — see past quotes.

## Glossary

- **Heavy box**: a box whose billable weight is over 20 kg. Heavy boxes go through the dock scale, not
  the parcel conveyor.
- **Length + girth**: the longest side plus twice each of the other two sides. Carrier labels print it,
  and 300 cm is the figure that matters for billing.

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
