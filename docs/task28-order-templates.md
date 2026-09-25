# Task 28 — Saved Order Templates (Whole Orders with Names)

A recurring group — the same six people getting roughly the same drinks every
Friday — shouldn't rebuild the order from scratch each time. A template saves
a complete order as a named, reusable starting point: each person's name
paired with their drink and full modifier set (size, sugar, ice, milk,
temperature, toppings, quantity, notes). Loading one never finishes anything;
people tweak, add, or drop entries before committing, through the same preview
and cart handoff as every other path.

## Where templates live

In the browser, next to favorites (Task 15), deliberately account-free: JSON
records in `localStorage` under `boba-builder:order-templates:v1`, scoped per
store by `restaurant_id`, up to 10 templates per store and 60 entries each.
Clearing site data clears them. The server never stores a template — it only
validates entries when one is loaded, so a stale or hand-edited template gets
the same checks as any other collection path. `app/static/order-templates.js`
(`window.BobaOrderTemplates`) is the store; `app/order_templates.py` is the
load-time validator.

Saving happens on the preview page, because that is where all three
collection paths converge: **Save as an order template** captures the checked
rows (preferring matched canonical values over what was typed) under a name.
Spreadsheet uploads resolve to the default store, matching the importer.

## What "loading" means per path

- **Solo** (`/solo`): restoring your own drinks. Entries load into the draft
  with names left behind — one person is placing this order — except that a
  single-owner template fills the empty name field. Entries the current menu
  no longer understands are skipped with their reason, since the draft has no
  edit flow to fix them in.
- **Spreadsheet** (entry page): an alternative to uploading a file at all.
  **Use template →** posts the entries to `POST /api/template-runs`, which
  validates every entry (each needs a name and a drink, quantities 1–20) and
  returns a normal `order_template` run. The preview then re-checks each row
  against the live menu before anything reaches a cart.
- **Group link**: the interesting one. The organizer loads a template into the
  room as **expected orders** — visible to everyone with the link, but pending
  confirmation, never submitted. Each person then confirms as-is, changes
  first (the editor pre-fills, submitting confirms the edited values), or
  drops their entry if they're out. The organizer can also remove stale
  entries.

## The pending-vs-submitted decision

Pre-filled entries **do not count as submitted**. The alternative — seeding
real orders in everyone's name — would silently buy a drink for someone who
is out that day, and there is no way to tell "hasn't opened the link yet"
from "isn't coming". Concretely, suggestions are excluded from the room
summary, per-person costs, budget-cap accounting, and finalization (an
unconfirmed room is still an empty room); confirming converts one suggestion
into a real order with its own edit token, subject to the same availability
and budget checks as a fresh submission. The organizer dashboard groups them
under a "waiting on" heading, and the participant page states outright that
nothing is ordered until they confirm.

## API routes

```text
POST   /api/template-runs
POST   /api/group-orders/<room_id>/suggestions            (organizer; {entries, mode})
POST   /api/group-orders/<room_id>/suggestions/<id>/confirm  (anyone, while open; edits ok)
DELETE /api/group-orders/<room_id>/suggestions/<id>       (organizer, or anyone while open)
```

Rooms read as before plus a `suggestions` list; rooms created before this
change simply report none. Seeding replaces by default (`mode: "append"`
accumulates) and is organizer-only, allowed while paused but never once
closed. Confirming requires an open room. Finalization is untouched — it
already snapshots only submitted orders.

Organizer fast path: **Submit all as ordered** (`POST .../suggestions/submit-all`)
converts every expected order in one click, after re-validating each entry for
availability, budget, and room cap (all-or-nothing). It exists for the "load
the orders and finalize" flow: the click is the organizer explicitly ordering
for everyone listed, including anyone unconfirmed. The finalize button enables
as soon as real orders exist.
