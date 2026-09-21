# Task 19 — Order Status Board

The group link page now answers "is it here yet?" so participants stop
asking the organizer for updates. A shared five-stage board tracks the
order from submission through pickup, on the same page everyone already
has open — no dedicated URL and no push channel to operate.

## The stages

Fulfillment is a separate dimension from the collection status
(`open`/`locked`/`closed`), which only tracks whether participants can
still submit drinks:

1. `collecting` — submissions, moderation, finalization, and the cart
   review all happen here. There is deliberately no `cart_ready` stage:
   the dashboard already surfaces the preview URL, and a cart is still
   just collecting until money changes hands.
2. `ordered` — the organizer placed and paid on the Kung Fu Tea site.
3. `ready` — the store has the drinks ready for pickup.
4. `picked_up` — the runner has the cups and is bringing them over.
5. `distributed` — every cup has been handed out. Terminal in practice,
   but any stage (including this one) can be re-set to fix a mistake.

Each update carries an optional free-text note (280 characters, e.g.
"Ready around 5:30pm, pickup counter B"), a server timestamp, and an
append-only history (capped at 50 entries) so a glance shows both where
the order is and how it got there.

## Manual updates, with one inferred default

Every stage past `collecting` is a manual organizer update. Nothing is
inferred from the cart or the pipeline, because the app cannot observe
the actual checkout: payment happens in the organizer's own browser on
the store site, and no callback comes back. Inferring `ordered` from
"the cart looks built" would lie whenever someone builds the cart
without paying.

The one inference is the starting point: new rooms — and rooms saved
before this feature, which carry no fulfillment fields — read as
`collecting`. A finalized-but-unpaid room therefore honestly says
"Drinks are in — waiting on the order" rather than pretending the
store has it.

## Who can advance it

Only the organizer token (`X-Organizer-Token`, also accepted as a
Bearer [REDACTED] or JSON-body token like every other organizer control). The runner
is whoever holds the organizer dashboard; participants get a read-only
board and a 403 if they try to move it. Updates work at any collection
status, including after `finalize` closes the room — that is exactly
when the errand starts.

`POST /api/group-orders/<room_id>/fulfillment`

```json
{"fulfillment_status": "ready", "note": "Pickup counter B"}
```

Both `GET /api/group-orders/<room_id>` and its authenticated
`/organizer` sibling return the board as `session.fulfillment`
(`status`, `label`, `hint`, `note`, `updated_at`, `history`), so the
two pages can never disagree. Secret hashes and raw tokens are never
included.

## Where it is surfaced

- **Group link page** (`/group-order/<room_id>`): a status-board card
  directly under the room strip, above the deadline countdown — the
  participant's "is it here yet?" answer. It shows the current stage,
  a five-step stepper with completed stages marked, the organizer's
  note, and the last-update time. It refreshes with the page's existing
  15-second poll and manual Refresh; no new polling was added.
- **Organizer dashboard** (`/group-order/<room_id>/organizer`): a
  status-board card between the deadline manager and the totals, with
  the same stepper, a note field, one button per stage (the current
  stage reads "Current" and is disabled), and the recent trail
  ("Ordered → Ready"). Polling never overwrites a note being typed.
  After finalization the finalize card points here: pay on the store
  site, then mark Ordered.

Push was considered and skipped for the same reason as the dashboard's
existing polling: a stdlib single-process server with no WebSocket/SSE
infrastructure, and a 5–15 second poll is live enough for a boba run.
