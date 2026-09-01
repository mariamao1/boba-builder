# Task 8 — Group Order Session Backend

Group orders are anonymous, persistent rooms. Creating one returns a public,
unguessable room ID plus an organizer token. The ID is the share credential:
anyone with it can read the room and add a drink while it is open. No account or
cookie is required.

## Lifecycle and persistence

- Rooms are JSON files under `.group-orders/`, written atomically so a server
  restart does not lose the room. Per-room locks prevent concurrent additions
  in the threaded server from overwriting each other.
- Room IDs carry roughly 144 bits of randomness. Organizer and order-edit
  tokens are returned once and only SHA-256 hashes are persisted.
- Rooms expire after 24 hours by default. Creation may request any positive TTL
  up to seven days. Expired rooms remain readable as `status: "expired"` for a
  seven-day grace period, but cannot be changed.
- The organizer can lock and reopen a room. Locking makes it read-only while the
  organizer checks the order. Closing is permanent. Both closed and locked rooms
  remain readable.
- Pruning keeps all live rooms, keeps at most 200 recent closed/expired rooms,
  and removes rooms more than seven days past expiration. Each room accepts at
  most 200 order lines.

This file store is appropriate for the current single-process app. A deployment
with multiple server processes should put the same API behind a transactional
database instead.

## API

### Create a room

`POST /api/group-orders`

```json
{
  "title": "Monday tea",
  "organizer_name": "Mariam",
  "expires_in_hours": 24
}
```

Returns `201` with `session_id`, `share_url`, `api_url`, the public `session`,
and an `organizer_token`. The client must retain the token; it is not recoverable
from a later room read.

### Read a room

`GET /api/group-orders/<room_id>`

The session contains its order lines plus totals for orders, drinks, and people,
including a `by_person` rollup. Secret hashes and raw tokens are never included.

`GET /group-order/<room_id>` is the participant-facing HTML page built in Task
9. It reads this API in the browser.

### Add an order

`POST /api/group-orders/<room_id>/orders`

```json
{
  "person": "Alice",
  "drink": "Taro Slush",
  "size": "Large",
  "sugar": "50%",
  "ice": "Less Ice",
  "toppings": ["Boba"],
  "milk": "",
  "temperature": "",
  "quantity": 1,
  "notes": "no straw"
}
```

`person` and `drink` are required. The response includes a one-time
`order_token`, which lets that contributor edit or delete the line. Each line
also carries a public random `participant_id` and the submitted display name;
the token, not the name, is the proof of ownership.

### Edit or delete a line

- `PATCH /api/group-orders/<room_id>/orders/<order_id>` with changed fields
- `DELETE /api/group-orders/<room_id>/orders/<order_id>`

Send the returned token in `X-Order-Token`. The organizer may manage any line
with `X-Organizer-Token`. Mutations are only allowed while the room is open.

### Organizer controls

- `POST /api/group-orders/<room_id>/lock`
- `POST /api/group-orders/<room_id>/reopen`
- `POST /api/group-orders/<room_id>/close`

Send `X-Organizer-Token`. A Bearer token or the matching token in a JSON body is
also accepted. Locking is reversible; closing and expiration are terminal.

Errors are JSON with `ok: false`, a human-readable `error`, and a stable `code`.
Missing rooms return 404, bad tokens 403, locked/closed writes 409, and expired
writes 410.
