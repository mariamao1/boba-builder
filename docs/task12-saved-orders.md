# Task 12 — Save & View Finished Orders

After a spreadsheet or group-link order produces a reviewable Kung Fu Tea
cart, its handoff screen offers a name and date form. Saving creates an
immutable snapshot under `.saved-orders/`; unlike the capped working files in
`.runs/`, these records are not pruned with temporary runs.

Saved orders are scoped to the browser. The browser generates an opaque secret
and keeps it in local storage (with session storage as a fallback), sends it in
`X-Saved-Orders-Token`, and the server stores only its SHA-256 digest. This fits
the app's account-free organizer model while preventing another browser from
listing or opening the archive. Clearing browser storage loses the key; the
saved files remain on the Boba Builder server.

`/saved-orders` lists this browser's snapshots and handles the empty state.
`/saved-orders/<id>` shows the date and source path, store, people/order/drink
counts, exact placed manifest with modifiers and notes, not-placed lines, and
the final subtotal, tax, fees, and total when supplied by Kung Fu Tea.

Each detail page supports:

- **Export CSV**, including all original normalized rows and whether each line
  made it into the cart.
- **Use for a new cart**, which creates a fresh editable run with the same rows
  and store. It never reuses the old Kung Fu Tea order or handoff URL, so menu
  choices and availability are checked again before a new cart is built.

API routes:

```text
GET  /api/saved-orders
POST /api/saved-orders
GET  /api/saved-orders/<id>
GET  /api/saved-orders/<id>/export
POST /api/saved-orders/<id>/repeat
```
