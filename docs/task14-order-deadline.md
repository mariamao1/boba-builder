# Task 14 — Order Deadline & Countdown

Every group order has an organizer-selected deadline. The creation form uses a
local `datetime-local` control (defaulting to roughly two hours ahead), converts
that choice to an ISO 8601 instant, and allows a cutoff no more than seven days
away. The server returns both `deadline_at` and `server_now`; countdowns use the
offset between those values and the browser clock so a mis-set participant
clock does not decide whether ordering is open.

## Automatic locking

The persisted room remains `open`, but its effective public status becomes
`locked` at the deadline with `lock_reason: "deadline"`,
`deadline_passed: true`, and `accepting_orders: false`. Every add, edit, and
participant delete checks the deadline again while holding the room lock, so a
stale page cannot submit late. Those requests return HTTP 409 with code
`deadline_passed`. Organizer moderation and finalization remain available.

Participant and organizer pages update their countdown once per second and
switch immediately to the locked presentation at zero, then refresh from the
server. Participants keep the submitted-order list but lose the order editor;
the message tells late arrivals to contact the organizer.

## Organizer controls

The dashboard shows the countdown and exact local cutoff prominently. The
organizer can enter a new local date/time or extend by 15, 30, or 60 minutes.
It calls:

`PATCH /api/group-orders/<room_id>/deadline`

```json
{"deadline_at": "2026-09-10T19:30:00Z"}
```

The organizer token is required. Moving an elapsed deadline into the future
reopens its automatic lock. Changing a deadline never clears a manual pause;
the organizer must explicitly reopen that room. A finalized or otherwise
permanently closed room cannot have its deadline changed.

`expires_at` remains in the response as a compatibility alias for clients and
saved rooms from Task 8; new clients use `deadline_at`.
