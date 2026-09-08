# Task 13 — Payment Splitting & Cost Tracking

The organizer dashboard prices every submitted line from the room's pinned,
captured store menu. It shows each line estimate, each person's running
subtotal, and the group estimate while submissions are still changing. Client
supplied prices are never trusted.

When the cart is built, the estimate is replaced by the store's returned line
totals and cart total. Tax and provider fees are therefore included whenever
Kung Fu Tea reports them. After paying on Kung Fu Tea, the organizer can enter
the receipt's tip and/or exact final amount when saving the finished order.

Cart-wide costs are allocated proportionally to each person's drink subtotal.
That policy applies to tax, tip, fees, discounts, and any remaining difference
between the line totals and final receipt total. Allocation uses integer cents
and largest-remainder rounding, so the displayed person totals always add up
exactly to the group total. A zero-value order falls back to an even split.

The live organizer view, cart handoff, and saved-order detail all show the
person breakdown. Each view offers a **Copy breakdown** action that produces a
plain-text, chat-friendly list of names, amounts, and the group total. Saved
orders retain the post-checkout split with the manifest and receipt totals.

The API representation is `costs` with:

- `source`: `menu_estimate` or `cart_total`
- `estimated`, `currency`, and `allocation`
- `subtotal`, `tax`, `tip`, `fees`, `adjustment`, `shared_total`, and `total`
- `priced_drinks`, `unpriced_drinks`, and `by_person`

An adjustment records discounts or any other total difference not explained
by the named tax, tip, and fee fields; it is allocated by the same policy.
