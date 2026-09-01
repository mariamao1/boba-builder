# Boba Builder

Turn a group's order spreadsheet into one Kung Fu Tea cart, ready to pay for in
your own browser.

```
python3 -m app.server          # http://127.0.0.1:8000
```

Python 3.11+, standard library only — nothing to install.

## Where things are

| | |
| --- | --- |
| `app/` | the web app: import, editable preview, reconciliation, JSON API |
| `app/options.py` | the user's words → the store's option set (Task 3) |
| `app/menu.py` | the menu as objects: items and their own option lists (Task 4) |
| `app/matcher.py` | rows → exact menu items and modifier strings (Task 4) |
| `app/cart.py` | live menu refresh, cart build, manifest, and safe handoff (Task 5) |
| `app/group_orders.py` | persistent shared rooms, order aggregation, and lifecycle (Task 8) |
| `data/mapping.json` | every synonym and alias, as data — edit this, not the code |
| `scripts/kft_api.py` | client for the Kung Fu Tea ordering API (Task 1) |
| `scripts/fetch_menu.py` | capture and normalise a store's menu |
| `data/menu-*.json` | menu snapshots; `650c9c3cd73592bc0e0bd50a` is 5th Ave, Bk |
| `data/sample-group-order.csv` | a realistic messy group order, for trying it out |
| `docs/` | site/API notes, endpoint list, per-task design notes |
| `task1-recommendation.txt` | the cart-handoff spike and its findings — read first |

## Status

- [x] **Task 1** — cart handoff feasibility + menu capture
      (`docs/task1-site-structure.md`)
- [x] **Task 2** — import & upload page (`docs/task2-import-page.md`)
- [x] **Task 3** — spreadsheet import & parsing (`docs/task3-parsing.md`)
- [x] **Task 4** — menu mapping & normalization (`docs/task4-matching.md`)
- [x] **Task 5** — build the cart and hand it to the Kung Fu Tea site
      (`docs/task5-cart-handoff.md`)
- [x] **Task 6** — in-app order preview, correction, and cart reconciliation
      (`docs/task6-preview-reconciliation.md`)
- [x] **Task 8** — anonymous shared group-order sessions
      (`docs/task8-group-order-sessions.md`)
- [x] **Task 9** — mobile participant order entry using the captured menu
      (`docs/task9-participant-order-entry.md`)

`app/pipeline.py` documents the stage contract. `python3 -m app.pipeline` prints
what is wired.

## Tests

```
python3 -m unittest discover -s tests -t .
python3 -m app.mapping        # does the vocabulary still match the live menu?
```

27 of those drive the preview page's own JavaScript through a small DOM shim, to
catch the wiring bugs Python can't see. They need a JavaScript engine on the
machine — macOS ships one — and skip themselves when there isn't one.

## What this does not do

It stops before checkout. The last step hands over a normal Kung Fu Tea cart
link; the person pays for it themselves, in their own browser. No payment
endpoint is called and none is wrapped.
