# Task 9 — Participant Order Entry

The public `/group-order/<room_id>` link is a mobile-first order form. It loads
the room from `/api/group-orders/<room_id>` and the captured store menu from
`/api/menu`.

## Menu contract

`GET /api/menu` returns only available, orderable drinks. Each item contains its
own option groups, including required/min/max rules and current option prices.
The option `label` is Boba Builder's canonical value (`Large`, `50%`, `Boba`);
`store_value` retains the ordering provider's literal value when it differs.
This keeps participant orders exact without free-text or fuzzy matching.

## Participant flow

- Search the real menu or filter it by the store's category order.
- Pick only the size, sugar, ice, topping, and milk options that the chosen
  drink actually offers. Required groups are enforced before submission.
- Add a name, quantity, and optional note, then submit directly to the room.
- Add another drink without re-entering the participant name.
- See every submitted drink, grouped in one live room roster with the person's
  display name and selected modifiers. The roster refreshes automatically and
  also has a manual refresh control.
- Edit or remove drinks submitted from the same browser while the room is open.

The one-time order edit tokens are kept in `localStorage`, scoped to the room.
They are never placed in the URL or returned by later room reads. Clearing site
data removes that browser's ability to edit prior submissions; the organizer
can still manage them with the organizer token.

Locked, closed, and expired rooms remain readable, but the order form and edit
controls are disabled. Everyone's submitted drinks remain visible; edit/remove
controls appear only on entries owned by the current browser.
