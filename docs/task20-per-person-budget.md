# Task 20 — Per-Person Budget Cap

The organizer can optionally set a dollar cap when creating a group order and
change or remove it from the organizer dashboard until the room is closed. The
value is rounded to cents, must be between $0.01 and $1,000.00, and is exposed
to participants as `budget_cap` on the room response.

## Policy

The cap is a **hard limit on each person's combined priced drinks**, not a
per-drink limit. Names are grouped case-insensitively with repeated whitespace
collapsed, matching the existing group summary behavior. Quantity, base price,
and priced modifiers all count. Tax, tip, and cart-wide fees do not count
because they are unknown while participants are choosing.

The server always recalculates the proposed total from the room's pinned,
captured menu. It never accepts a client-supplied price. A submission or
participant edit that would increase a person's total above the cap returns
HTTP 409 with code `budget_exceeded`. Unrecognized lines with no trustworthy
price remain visible and are exempt rather than assigned a guessed price.

## Participant experience

The participant page shows the cap before the menu. Once a name is entered, it
shows that person's submitted spend and remaining budget. Selecting a drink,
modifier, or quantity updates the combined total immediately. An over-budget
selection explains the amount and disables submission; the server repeats the
same check under the room lock to protect against stale pages and concurrent
orders. Favorite-drink shortcuts receive the same preflight and server check.

## Changing an existing cap

Lowering a cap never silently deletes or alters an accepted drink. Existing
over-budget people are flagged in participant and organizer views. They may
make notes-only or price-reducing edits, but participant changes cannot
increase their priced total. Organizer moderation can still override a line,
and removing the cap restores normal ordering. Closed rooms cannot change the
cap.

The organizer endpoint is:

`PATCH /api/group-orders/<room_id>/budget`

```json
{"budget_cap": 12.50}
```

Send `null` to remove the cap. The organizer token is required.
