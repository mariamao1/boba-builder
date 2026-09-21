# Task 22 — Edit Window After Submitting

A participant who has already submitted a drink can change or remove it
without going through the organizer, up until the session stops accepting
participant changes.

## Identity: per-order edit token in this browser

Each submitted line returns a one-time `order_token`. Only its SHA-256 hash
is persisted; the raw token never appears in later room reads or URLs. The
participant page keeps a `{order_id: {token}}` map in `localStorage`, scoped
to the room. `PATCH` and `DELETE`
`/api/group-orders/<room_id>/orders/<order_id>` send it as `X-Order-Token`
(or `order_token` in the JSON body). The organizer token in
`X-Organizer-Token` can manage any line. The display name is a label, never
proof: two people typing "Alice" do not gain access to each other's lines,
and renaming a line still requires the original line's token.

Tradeoffs versus the alternatives:

- Name match alone is simple and survives browser wipes, but anyone can
  claim any name, so it lets people edit each other's orders. Rejected.
- A per-person link (one token per name) would survive multi-drink edits
  more gracefully, but it introduces account-like name ownership and
  squatting ("who owns Alice?") without solving device loss either.
- Browser accounts/cookies would be stronger, but this app is
  account-free by design: the share link is the only credential.
- The chosen per-order token is the smallest secret that matches the
  threat: unguessable (`secrets.token_urlsafe(24)`), scoped to one line,
  and never shared. Its limits are honest: clearing site data or switching
  browsers loses the ability to edit, and forwarding the token forwards
  the edit right. In both cases the organizer can still moderate the line.

## Lifecycle

Participant edits and removals require an open room: they are rejected
with 409 (`room_not_open`, or `deadline_passed` past the cutoff) once the
room is manually locked, the deadline passes, or the room is closed or
finalized. Locking therefore freezes participant changes while the
organizer reviews. The organizer may still remove a line while the room is
open or locked — including past the deadline — to clear junk or duplicates
before finalizing, but cannot edit line contents while locked and cannot
change a closed room. Finalizing closes the room permanently and points at
`/preview/<run_id>`, so post-handoff changes go through the normal cart
review instead of the room.

## Organizer visibility

Every line carries `created_at` and `updated_at`. An edit keeps
`created_at` and advances `updated_at`; the room's `updated_at` advances
too, and the dashboard's five-second poll picks it up. The organizer
dashboard renders `Added <time>` for untouched lines and
`Added <time> · Edited <time>` once a line has changed, so a late edit is
visible in place rather than silently replacing the original. The
participant roster shows a matching `Edited` badge next to the name.
