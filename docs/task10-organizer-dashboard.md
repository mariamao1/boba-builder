# Task 10 — Organizer Dashboard & Live Aggregation

The private organizer view is `/group-order/<room_id>/organizer`. A creation
response includes an `organizer_url` with the one-time organizer token in its
URL fragment. The page stores that token locally, removes it from the visible
URL, and uses it only in the `X-Organizer-Token` header.

The dashboard polls every five seconds while visible and refreshes immediately
when its tab returns to the foreground. It shows totals for people, drinks, and
order lines; groups full configurations by person; and separately rolls up
quantities by drink. Exact duplicate configurations are flagged for review.
The organizer can remove any line while a room is open or paused. Pausing first
provides a stable moderation window without admitting participant changes.

`POST /api/group-orders/<room_id>/finalize` snapshots the room under its lock,
normalizes the lines through the standard importer, saves a normal run, and
permanently closes submissions. It returns `/preview/<run_id>`, handing the
result to the existing correction, cart-build, manifest, and Kung Fu Tea
handoff flow. The endpoint is idempotent while that run is retained.

The public room response never exposes the finalized preview URL. An organizer
can recover it after a reload through authenticated
`GET /api/group-orders/<room_id>/organizer`.
