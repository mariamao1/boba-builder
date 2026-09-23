# Task 26 — Multiple Payers

Group-order settlement now has two layers. The existing per-person breakdown
remains the source of truth for drink subtotals and proportional shared costs.
An optional payer configuration rolls those complete person shares up into the
people or teams funding the order.

The organizer configures payer names from the private dashboard and chooses:

- **Assigned**: each participant belongs to one payer. A participant's drink
  subtotal and their allocated tax, tip, fees, discounts, and adjustments all
  stay together. Unassigned participants fall back to the first payer.
- **Even**: the subtotal and shared-cost pool are each divided evenly across
  all payers using integer cents and largest-remainder rounding.

The organizer also designates who makes the single store payment. Every other
non-zero payer share becomes a reimbursement to that checkout payer. Payer
shares and settlement instructions always reconcile exactly to the group
total.

The configuration is stored on the room, carried into its finalized run, and
applied again to the live cart response and final receipt amount. Saved orders
retain both the configuration and final payer settlement. The organizer,
preview, and saved-order views include payer totals in their copyable payment
breakdowns.

## API shape

`PATCH /api/group-orders/<room_id>/payers` accepts the organizer token and:

```json
{
  "payers": ["Design", "Engineering"],
  "mode": "assigned",
  "assignments": {"alice": "Design", "bob": "Engineering"},
  "paid_by": "Design"
}
```

Names are whitespace-normalized and unique case-insensitively. Assignment keys
are normalized participant names. An empty `payers` list returns the order to
the original single-payer behavior.

When configured, `costs.payer_split` contains `mode`, `paid_by`, payer rows,
settlement transfers, and the reconciled total. `costs.by_person` is unchanged,
so existing payment-splitting consumers remain compatible.
