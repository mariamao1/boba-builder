# Task 21 — Top 5 Most Popular Drinks

The landing page now shows the five most popular base drinks, and participant
order pages show the entries that are available on that room's pinned store
menu as quick-start buttons.

## Popularity policy

"Popular" means the **number of cups currently submitted to group-order rooms
in the rolling last seven days, across all organizers**. Quantity therefore
counts: a line with quantity three contributes three cups. A deleted line no
longer counts. Spreadsheet imports and private saved-order snapshots are not
included, which prevents double-counting a group order after it is finalized
and keeps browser-private history out of a public aggregate.

Drinks are grouped by base item, case-insensitively and with repeated spaces
collapsed. Size, sugar, ice, milk, toppings, temperature, and notes do not
split the ranking. Ties are resolved by the number of order lines and then by
drink name for a stable result. The seven-day window matches the existing room
retention period.

No participant name, room title, configuration, or organizer data is exposed.
The public endpoint is:

`GET /api/leaderboard[?restaurant_id=<captured-store-id>]`

It returns at most five entries plus aggregate cup, line, and contributing-room
counts. Unknown store ids return 404, and responses are marked `no-store`.

## Empty and low-data states

With no qualifying orders, the landing board says "No group orders yet" and
the participant quick-pick panel stays hidden. With one to four qualifying
drinks, the board simply shows the available ranks rather than inventing
defaults or padding the list.

## Quick picks

The participant page filters the ranking against that room's current menu.
Clicking a popular drink selects its base menu item and opens the normal option
editor. It does not submit immediately: popularity does not reveal a safe size,
sweetness, ice, or topping configuration, and required choices still need the
participant's input.
