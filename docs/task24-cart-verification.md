# Task 24 — Cart Integrity Verification

After the cart is built, the app reads back what's actually in it and
compares that against what it intended to order — before the organizer pays.
It does not trust the build's own report of success.

## Where the true state comes from

Task 1 established a clone-on-open handoff: the build creates an anonymous
*source* order, and opening the handoff link makes Kung Fu Tea server-side
copy its line items into a new order owned by the organizer's browser. There
is no token in the link and the app never sees the organizer's session, so
their clone cannot be read back by us.

The true state we check is therefore the source order itself:
`GET api/v1/orders/{source_order_id}`, issued inside the same build call
that holds the anonymous access token (the token is still never returned or
persisted). This is sufficient because the clone is faithful where it
counts — verified live in Task 1 and re-checked 2026-09-21: cloning
preserves the menu item, options, quantities, and prices exactly, and drops
only per-item `for`/`notes`, by design, which our own manifest covers
instead. The one thing source verification cannot see is what the organizer
changes afterwards in their editable clone, which is why the preview tells
them to compare before paying rather than asserting the cart is correct.

No extra clone is created to verify: each clone is a real order, and a
clone made now could not catch a sell-out that happens later anyway.

## How entries are matched back

The site's representation does not align one-to-one with ours, so the
read-back is matched semantically (`app/verify.py`, pure — no network):

- Line-id continuity first: the server id from each add response identifies
  which line a row became — but its modifiers are still compared, not
  assumed. If the server ever splits or merges lines, matching falls back
  to the (drink, modifiers) signature with quantities aggregated, so one
  double line covers two identical rows and vice versa.
- Options are keyed by (group name, option name, quantity) — the same
  name-addressing the cart writer uses — compared case-insensitively with
  whitespace collapsed. The combined `"Group: Option"` display name is only
  a fallback when the structured fields are absent.
- Read-back quantities arrive as strings (`"2"`); add responses use
  integers. Both are coerced.
- Matching is person-blind on purpose: identical drinks for different
  people are indistinguishable in the cart (Task 1 §4). A shortfall is
  reported against the row that could not be covered.
- `for`/`notes` are ignored: they persist on the source order but are
  dropped by the clone, so they can never describe what gets paid for.

## What is caught

| Kind | Meaning |
|---|---|
| `missing` | None of the row is in the cart. The row leaves the manifest and joins the failure list (`verification_failed`), as before. |
| `short_quantity` | Part of the row verified; the rest is absent. Numbers named (`ordered 3 but the cart has only 1`). |
| `modifiers` | The drink is there but configured wrong — dropped or substituted modifiers, including topping counts. Both sides shown. |
| `unexpected` | The cart has a line nobody ordered. |
| `price` | A 1:1 line costs differently than when it was acknowledged. |
| `totals` | Only checked when every line is otherwise accounted for, so it means real price drift, not an explained absence. Differences at or below $0.015 are rounding noise. |

A row that is present but wrong stays in the manifest (it IS in the cart —
labels, costs, and the clone all agree on that) with its discrepancy
spelled out beside it. A build with any content mismatch is `partial`,
never `ready`, but stays reviewable with its handoff link: the organizer
can fix the rows and rebuild, or fix the editable Kung Fu Tea cart
directly — both actions are named in the report.

## When verification itself fails

`status` is `matched`, `mismatched`, or `unverified`. Anything that
prevents a real comparison — the read-back failed, the payload is missing,
`items` is not a list — yields `unverified`, never `matched`. The preview
renders that as an explicit "Cart check — not confirmed" panel plus a
warning: the manifest is shown but nothing about it is confirmed, and the
organizer must compare with the Kung Fu Tea cart before paying. Claiming
the cart is correct when it could not actually be confirmed is worse than
saying so plainly.

## Presentation

`run["cart"]["verification"]` carries `status`, `source`, `checked_at`,
`expected_drinks`/`confirmed_drinks`, and the `mismatches` list, so both
the page and API consumers see the same report. On the preview's cart
handoff view it renders between the reconciliation counts and the
person-by-person manifest — the moment before the "Open the cart at Kung
Fu Tea" link — with a headline that replaces the ready message while
differences are open ("Check the cart before you pay — N differences").

## Verification

- `tests/test_verify.py`: 17 cases over the matcher — exact and aggregated
  quantity matching, dropped/substituted/counted modifiers, line-id
  continuity checked-not-assumed, extras, per-line and subtotal drift,
  rounding tolerance, and every `unverified` shape.
- `tests/test_cart.py`: end-to-end through `cart.build` with a live-shaped
  fake — clean builds record `matched`, dropped modifiers / short
  quantities / rogue lines downgrade to `partial` with attributed reports,
  and an unreadable read-back stays `unverified` with its handoff.
- `tests/test_preview_js.py` (`CartCheckTests`): the real `preview.js`
  through the DOM shim — pass panel, mismatch panel with headline override,
  unconfirmed panel, and no panel for pre-verification carts.
- Live shape re-confirmed 2026-09-21 against the Bay Ridge store
  (`650c9c3cd73592bc0e0bd50a`): read-back items carry `menuitem`, string
  `quantity`, and per-option `group_name`/`option_name`/`quantity`; the
  server-side clone preserves all three and drops only `for`/`notes`.
  Throwaway carts only; nothing submitted, no payment touched.
