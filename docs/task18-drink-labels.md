# Task 18 — Drink Labels / Pickup Slips

Once an order is finalized, the runner stands at a counter with a tray of
visually similar cups. Labels pair each person's name with their exact drink
so distribution is unambiguous: one label per cup, grouped by person, each
carrying the drink's full spec.

## Where they live

Labels appear in the two places a finalized order is ever looked at again:

- the cart review (`/preview/<run_id>`), once the cart is reviewable, and
- the saved-order detail (`/saved-orders/<id>`), which is the page open on
  pickup day.

Both show a collapsed **Pickup labels** block that fetches on demand, so
opening either page stays a single read. The organizer dashboard needs no
third copy: finalizing already lands on the cart review.

## One builder, three consumptions

`app/labels.py` is the single source of truth. It expands placed cart lines
(`cart["added"]`, or a saved order's `items`) into one label per cup — a
quantity of 2 is two identical cups, so it gets two labels — and returns
`{seq, cups, person, cup, person_cups, drink, spec, notes, row_number}`.
Failed and skipped lines never reach it, so every cup returned was actually
ordered; the endpoints also report how many drinks went unplaced and have no
label.

The same answer is consumed three ways, matching the physical reality of a
tray of cups and a phone:

- **On-screen checklist**, grouped by person (alphabetical, cart order within
  each group) with a checkbox per cup and a "n of m handed out" count. Tapping
  anywhere on a card toggles it — the whole interaction with a tray in one
  hand and a phone in the other.
- **Printable slips**: printing (with `printing-labels` on the body, see
  `app.css`) hides everything but the labels and renders each cup as its own
  dashed cut-out card in black on white.
- **Copyable plain text** (`format_text`), one grouped line per cup, for
  pasting into chat for the pickup announcement.

`app/static/labels.js` renders the answer identically on both pages.

## Staying distinguishable

Cart literals print as cup words: `Large .7` becomes `Large` (via
`menu.size_label`) and `Half S 50%` becomes `50% sugar`, so two milk teas
differing only in sugar level read differently on paper. Toppings keep their
`×n` counts, notes stay on the label, and a cup ordered entirely on store
defaults says `Store defaults`. People with several cups get `Cup i of n` on
each label, and every label carries its overall `#seq` so the runner can
check cups off against the tray.

API routes:

```text
GET  /api/runs/<run_id>/labels
GET  /api/saved-orders/<id>/labels
```

The run route answers 409 until the cart is reviewable; the saved-order
route uses the same browser token as the rest of the archive.
