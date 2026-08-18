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
| `app/` | the web app: import & upload page, preview, JSON API (Task 2) |
| `scripts/kft_api.py` | client for the Kung Fu Tea ordering API (Task 1) |
| `scripts/fetch_menu.py` | capture and normalise a store's menu |
| `data/menu-*.json` | menu snapshots; `650c9c3cd73592bc0e0bd50a` is 5th Ave, Bk |
| `docs/` | site/API notes, endpoint list, Task 2 design notes |
| `task1-recommendation.txt` | the cart-handoff spike and its findings — read first |

## Status

- [x] **Task 1** — cart handoff feasibility + menu capture
- [x] **Task 2** — import & upload page (`docs/task2-import-page.md`)
- [ ] **Task 3** — match rows to menu items → `app/matcher.py: match(run)`
- [ ] **Task 4** — build the cart → `app/cart.py: build(matched)`
- [ ] **Task 5** — preview and hand over the link

Tasks 3 and 4 drop in as modules; `app/pipeline.py` documents the contract and
picks them up automatically. `python3 -m app.pipeline` prints what's wired.

## Tests

```
python3 -m unittest discover -s tests -t .
```

## What this does not do

It stops before checkout. The last step hands over a normal Kung Fu Tea cart
link; the person pays for it themselves, in their own browser. No payment
endpoint is called and none is wrapped.
