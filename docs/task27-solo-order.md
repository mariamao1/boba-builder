# Task 27 — Solo Order Path

A third way to order, alongside spreadsheet import (Tasks 2–3) and the group
link (Task 8): one person placing their own order through the app. It reuses
the same menu, cart-build, and handoff pipeline as everything else — a solo
order is a normal run whose `source.kind` is `"solo"`, matched, edited, and
handed off exactly like any other run.

## Why this path exists

A solo page that is merely a worse-looking copy of the Kung Fu Tea app's own
ordering flow should not exist, so the advantage was picked first and the page
was built around it:

**Usuals-first one-tap reorder.** This app already keeps browser-local favorite
drinks with their *complete* modifier sets (Task 15: size, sugar, ice, milk,
temperature, toppings, quantity, notes — no account), plus the browser's own
finished-order archive with one-click repeat (Task 12). The official app
requires an install and an account, and a repeat order still means rebuilding
every modifier screen. The solo page therefore opens on the user's usuals and
recent orders, above the menu: a repeat solo order is **Reorder → Check the
order → Build the cart**, with every modifier already exactly as it was.

Candidates considered and rejected as the *lead* advantage:

- *Popular-drink data* (Task 21) and the *randomizer* (Task 16) are on the
  page as helpers, but they answer "what should I try?" — not a reason to
  choose this over the official app on their own.
- *Dietary filtering* does not exist in this app, so it cannot justify
  anything yet.
- *No install requirement* is real (this runs in a browser) but on its own is
  a deployment detail, not a page design. It is the enabler: one-tap reorder
  with no install and no account is the combination the official app cannot
  match.
- *Speed in general* is only true for repeats. A first-time solo order here —
  pick a store, find a drink, set modifiers, check, build — is honest parity
  with the official app. The page says so by leading with usuals rather than
  the menu: where there is no usual yet, the win is "no install", not "fewer
  taps".

A solo order holds as many drinks as one person wants — usuals and fresh
picks collect in a draft ("Drinks so far") and are checked once, as a single
run, so the preview and cart handoff stay exactly the shared flow.

Tap count for the case the page is designed around (repeat of a saved usual,
landing to built cart):

1. Open `/solo` (the store is remembered, so no store step).
2. **Reorder →** on the usual — added with every modifier intact, editor skipped.
3. **Check my order** — one check for however many drinks were added.
4. **Build the cart** on the preview.
5. Open the Kung Fu Tea handoff link and pay.

Each extra drink is one more tap in step 2. The official flow for the same
repeat — open the app, find the store, find the drink, re-pick size,
sweetness, ice, and each topping, add to cart, check out — is roughly ten
taps across five screens *per drink*, after installing the app and signing
in. First-time orders are about even; repeat orders are where the solo path
earns its place.

## How it is wired

```text
solo page ── POST /api/solo-orders ──┐
shared group link ── finalize ───────┤
                                     ├─ saved run → preview/reconcile → build cart → Kung Fu Tea
CSV/XLSX/Google Sheet ─ import ──────┘
```

- `app/solo.py` validates one drink (`clean_payload`: drink required,
  quantity 1–20, toppings as a list) and builds the run through
  `importer.import_json`, exactly the entry point group finalization uses.
  The run's source is `{"kind": "solo", "restaurant_id", "store",
  "ordered_at"}`. Unknown stores are a 404; anything else malformed is a 400
  naming the problem.
- `POST /api/solo-orders` takes
  `{restaurant_id, person?, drinks: [{drink, size?, sugar?, ice?, milk?,
  temperature?, toppings?, quantity?, notes?}, ...]}` — a bare single-drink
  object without `drinks` is still accepted — and returns `{run_id,
  preview_url}` plus the enriched run. Every row carries the same person.
  `GET /solo` serves the page.
- `app/static/solo.html` + `app/static/solo.js` are the usuals-first page:
  remembered store, **My usuals** with one-tap **Reorder →** (adds to the
  draft) and **Customize**, up to three recent saved orders with
  **Add to my order →** (their placed drinks join the same draft, checked
  against the current menu, instead of opening a separate preview),
  templates that load whole past orders the same way, popular picks,
  Surprise Me, then the full menu with the same per-drink option groups as
  the group flow. The editor's submit adds to the draft; **Check my order →**
  posts the whole draft as one run and lands on the same preview. An unknown
  favorite drink is never silently substituted — Reorder and Customize both
  report it — and switching stores clears the draft rather than posting
  another store's modifiers.
- The landing page (`/`) offers the solo path as a third card with its own
  panel; `preview.js` routes solo runs back to `/solo` with the step label
  "Solo order".
- Saving a usual after a successful solo submit follows the Task 15 rule:
  only after the run is created, never for a rejected drink.

If the browser has no usuals and no saved orders, the page is deliberately
just a clean menu with remembered store and name — parity, plus no install —
and every reorder from then on is the fast path above.
