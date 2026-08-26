# Task 5 — Cart Building & Kung Fu Tea Handoff

The cart stage uses Task 1's clone-on-open handoff:

```
https://kft.orderexperience.net/{restaurant_id}/menu?order_id={order_id}
```

`app/cart.py:build()` refreshes the target store and menu, re-runs the matcher
against those live item/option names, creates one anonymous pickup order, adds
each mapped row, refreshes the quote, and returns the handoff URL. Opening that
URL makes Kung Fu Tea create an editable copy in the user's browser.

The access token used while building the source cart is never returned or saved.
There is no checkout or submit wrapper in this project, and the preview always
stops on an “Open the cart at Kung Fu Tea” link. The person reviews, changes,
submits, and pays on Kung Fu Tea's site.

## Failure behavior

- A closed store still gets a review-ready cart because the API permits cart
  creation while closed. The page says when it next opens when hours are known.
- A store that has disabled pickup gets no handoff.
- Sold-out or removed items are reported by person and row before cart creation.
  A modifier that disappeared after preview also fails that line instead of
  silently adding a different drink configuration.
- An item rejected during the add call does not discard successful items. The
  result is marked `partial`, lists every omitted drink, and links only to the
  cart that was actually built.
- If no mapped item can be added, no handoff URL is shown.
- Repeating a successful build for unchanged rows reuses the saved source order.
  Editing any row clears the old handoff and requires a rebuild.

## Manifest

Kung Fu Tea's clone drops its per-item `for` and `notes` fields. The result
therefore stores and renders a separate manifest with person, drink, quantity,
modifiers, note, and line price next to the handoff link.

The returned run has these Task 5 fields:

```
handoff_url
manifest[]
cart.status              ready | partial | failed
cart.store.state_now     open | closed | paused | unavailable | unknown
cart.added[]
cart.failed[]
cart.skipped[]
cart.totals
```

## Verification

The unit suite uses a fake ordering API for full, partial, sold-out, closed, and
pickup-disabled cases. A live smoke test on 2026-08-25 built and read back one
Taro Slush while the Bay Ridge store was closed: one line added, subtotal $6.70,
total $7.29, handoff URL produced, and no token persisted.

`scripts/verify_handoff.py` also rebuilt and read back its two-drink source cart
successfully on 2026-08-25. Chromium still cannot start in this environment, so
the final browser clone remains verified from the storefront bundle and its
server-side clone call, as documented in Task 1.
