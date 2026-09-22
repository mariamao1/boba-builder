# Task 25 — Sold-Out & Availability Detection

Items and toppings go out of stock between the moment a participant picks
a drink and the moment the organizer builds the cart. This task makes
those moments deliberate instead of silent: check availability at both
points, tell the right person, suggest an alternative, and never stall
the whole run over one outage.

## When availability is checked: both points

Availability can change between selection and cart-build, so one check
is not enough:

- **At selection** (`group_orders.add_order` / `update_order`): the
  participant is right here and can re-choose instantly. A submission
  the room's captured menu already knows is unavailable is rejected
  with HTTP 409 — `sold_out` for a sold-out drink or modifier,
  `unavailable` for something the run can never include (e.g. a gift
  card) — naming what is still orderable. The participant page shows
  the message, reloads the menu so the sold-out choice stops being
  offered, and keeps everything else the participant typed.
- **At cart-build** (`cart.build` re-matches every row against the live
  menu just before posting): anything that sold out since selection
  fails its own line with `sold_out` (drink or modifier) while the rest
  of the run still builds. The organizer re-chooses or removes the line
  and rebuilds; nothing is ever substituted on anyone's behalf.

Only what the menu positively identifies is rejected. An unknown drink
name still passes selection and is flagged `needs_drink` at match time,
as before — the server does not guess what somebody wants to drink.

## What "unavailable" covers

- **Drinks**: `StoreMenu.drinks` already excluded sold-out items;
  `unorderable()` explains them. Selection reuses that distinction, and
  the matcher reports `"X is sold out today"` with near-miss
  suggestions instead of `"no drink called X"`.
- **Modifiers**: a disabled option used to vanish from the item and get
  misreported as `"doesn't take Boba"`. `MenuGroup` now keeps its
  disabled options alongside the orderable ones, and
  `MenuItem.unavailable(axis, label)` answers "sold out right now" with
  the disabled literal. The matcher reports `"Boba is sold out for Taro
  Slush right now — ordered without it for now; it still does …"` with
  the remaining options as the re-choose list, flagged in `unmapped`
  with `"sold_out": true`. A modifier the drink never offered keeps its
  old message; the two are never conflated.
- **Store-level hours** stay where they were: `cart._store_state`
  already refuses a store that disabled pickup (`store_unavailable`)
  and still builds a reviewable cart for a merely closed one.

## Who gets told, and who decides

| Moment | Told | Decision |
|---|---|---|
| Selection | The participant submitting, with alternatives in the message | They re-choose on the spot |
| Match / preview | The affected row, plus a clustered run issue (below) | The affected person edits, or the organizer moderates |
| Cart-build | The organizer, per failed line (person + drink + cause) | Organizer fixes the rows and rebuilds, or fixes the editable Kung Fu Tea cart directly |

A sold-out modifier at match/cart-build time keeps the drink ordered
without it — the drink is still a drink somebody gets. A sold-out
drink has no line to post and waits on a person. Neither is ever
dropped silently: both land in `unmapped` / `failed` with their reason.

Edits only re-check what the edit newly asks for. A drink that sold
out after it was submitted stays put — notes-only (or price-reducing)
edits on it are never blocked. Picking a newly sold-out drink or
modifier is rejected with alternatives. Changing the drink re-checks
every modifier with it.

## Clustering: one outage reads as one line

A popular topping running out touches many rows at once. Per-row notes
still name each affected person, and both stages additionally cluster:

- `run["match"]["availability"]` (`matcher._availability`): counts per
  sold-out drink and per sold-out modifier, with rows, people, and cup
  totals. A cause hitting two or more rows also gets one run-level
  warning issue (`match:sold-out-drink` / `match:sold-out-option`), so
  the preview shows one line instead of N.
- `run["cart"]["availability"]` (`cart._availability_summary`): the
  same clustering over the cart's failed rows, keyed off the structured
  `cause` each pre-add check attaches. Rejections at add time keep
  their server message verbatim and are not force-fit into a cause.

Both summaries are always present (empty when nothing sold out) so
readers need no version check.

## The run never stalls

Cart-build failures are per line by construction: the loop continues
past every failed row, `status` is `partial` while anything placed,
and the handoff link is still issued for what the store accepted. The
summary exists precisely so the organizer can act on one line and
rebuild without reading every failure.

## Verification

- `tests/test_availability.py`: 19 cases — disabled options remembered
  vs hidden, sold-out vs never-offered modifier messages, sold-out
  drink matching with suggestions, match-time clustering (paired rows
  cluster, single rows don't), selection-time 409s with alternatives,
  unknown names passing, grandfathered notes-only edits, changed-drink
  re-checks, cart-build `sold_out` vs `menu_changed` codes, cart
  clustering with the rest of the run still building, and empty
  summaries on clean builds.
- Existing suites untouched and green: `test_matcher` (whole-dict
  `unmapped == []` assertions are unaffected by the additive
  `sold_out` flag), `test_cart` (removed options still code
  `menu_changed`; only positively-disabled ones code `sold_out`),
  `test_group_orders` (no new rejections for healthy orders).
- `app/static/group-order.js` parse-checked with JavaScriptCore
  (executes to the expected `window` stop — no syntax change).
