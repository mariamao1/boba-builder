# Task 15 — Favorites / “My Usual”

Favorites are intentionally account-free. They are JSON records in the current
browser's `localStorage` under `boba-builder:favorites:v1`; no favorite is sent
to a separate account service. The most recently used participant name is also
kept browser-wide so a repeat participant can open a new room and use a
favorite immediately. It is sent with a drink only when that drink is added to
the shared room. Clearing site data clears both.

Each favorite has its own generated id and is tied to the selected store by
`restaurant_id`. The provider's menu item id and canonical drink name identify
the drink. Its canonical size, sugar, ice, milk, temperature, toppings,
quantity, and notes are preserved. A person can keep up to eight favorites per
store and give each one a short display name such as “My usual.”

The participant page shows favorites for the room's store above the menu. A
compatible favorite has a one-tap **Add** action. Before submitting, the page
compares every saved choice with the room's current captured menu and rebuilds
the payload from that menu. If a drink or modifier has changed, it is never
silently substituted: the favorite is marked for review, where it can be
edited, renamed, and saved. Removed drinks remain visible but unavailable, so
the stale favorite can still be deleted directly from the list.

A favorite can be created while adding or editing a group-order drink by
selecting **Save as a usual**. Saving happens only after the group-order change
succeeds, avoiding a favorite for an order that was rejected by the room.
