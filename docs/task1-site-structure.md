# Task 1 — Site Structure & Menu Discovery

Spike on `kft.orderexperience.net`, covering menu browse → drink → modifiers →
add to cart → review screen. Checkout and payment are explicitly out of scope.

**Recommendation: automate against the JSON API, not the rendered UI.**

Target store: **5th Ave, Bk — 8625 5th Ave, Brooklyn NY 11209**
(`650c9c3cd73592bc0e0bd50a`), takeout + delivery, 11:00–19:55 daily,
`America/New_York`, 10 min prep lead. Snapshot in
`data/menu-650c9c3cd73592bc0e0bd50a.json` — 154 items.

---

## 1. What the site actually is

`kft.orderexperience.net` is a white-labelled **Paytronix OrderExperience**
storefront. The page title is literally `Paytronix`; Kung Fu Tea is a tenant.

The shell at `/` is 4.3 KB and contains an empty `<div id="__nuxt">`. It is a
**Nuxt 3 SPA with SSR disabled** (`data-ssr="false"`). Nothing is server
rendered — every item, price and modifier arrives over XHR after boot.

That single fact settles the API-vs-UI question. There is no HTML to scrape:
a scraper would have to boot a headless browser purely to watch it make the
API calls we can make directly.

### API surface

| | |
|---|---|
| Base URL | `https://oxb.pxsweb.com/` |
| Auth | `?key=49ace91d8c17daf4d13e61c05883ff3edbd02d1b` on every request |
| Transport | JSON responses; `multipart/form-data` request bodies on writes |
| App id | `64d157a1e0b9625e4d011c96` (slug `kft`) |

The key is **not a credential**. It is compiled into the public JS bundle and
served to every anonymous visitor. It identifies the client app, not a user,
and grants nothing an ordinary browser session doesn't already have.

Endpoints relevant to us, all confirmed live except where noted:

```
GET  api/v1/apps/get_by_slug/kft              app config + 441 restaurant ids
GET  api/v1/apps/restaurants/{app_id}         full store directory (2.1 MB)
GET  api/v1/restaurants/{id}/menu             entire menu, one call (1.1 MB)
GET  api/v1/menuitems/{id}                    single item detail
POST api/v1/orders                            create cart
POST api/v1/orders  {order_id, restaurant_id} CLONE a cart -> the handoff
POST api/v1/orders/{id}/items                 add line item
PUT  api/v1/orders/{id}/items/{item_id}       edit line item       [unverified]
DEL  api/v1/orders/{id}/items/{item_id}       remove line item     [unverified]
GET  api/v1/orders/{id}                       cart contents
GET  api/v1/orders/{id}/quote                 totals, tax, fees
--- boundary: everything below is out of scope ---
POST api/v1/orders/{id}/checkout
POST api/v1/orders/{id}/submit
```

The full extracted endpoint list is in `docs/api-endpoints.txt`.

### Why the UI alternative is worse

Browser automation would need Playwright plus a real Chromium, and would have
to survive a **PerimeterX** bot-defence integration (`px-cdn.net`,
`px-cloud.net`, `pxchk.net` in the CSP, and an
`api/v1/orders/{id}/px_challenge` endpoint in the bundle). Notably, that
challenge endpoint sits on the *checkout* path, not on browse or cart-build —
so the read and cart-assembly work we care about stays clear of it.

---

## 2. The flow, and its URLs

Front-end routes (Nuxt pages, for a human handoff link):

Extracted from the bundle's route table (`:rid` is the restaurant id):

| Step | Route |
|---|---|
| Store picker | `/:app?/locations` |
| Menu browse | `/:app?/:rid/menu/:category?/:menuitem?` |
| Drink + modifiers | `/:app?/:rid/menuitem` |
| Combos | `/:app?/:rid/combo` |
| Payment page | `/submit/:orderid` — reads the token from localStorage; **not** a handoff target |

**The handoff link is the menu route with a query param:**
`/{rid}/menu?order_id={order_id}` ← our stopping point. See
`task1-recommendation.txt` §2.

API sequence the automation will follow:

1. `GET api/v1/apps/get_by_slug/kft` → app id
2. `GET api/v1/apps/restaurants/{app_id}` → pick store, check it's open
3. `GET api/v1/restaurants/{store}/menu` → whole menu in one shot
4. `POST api/v1/orders` `{restaurant_id, type}` → `{order_id, token}`
5. `POST api/v1/orders/{id}/items` once per drink
6. `GET api/v1/orders/{id}/quote` → totals
7. Hand the user `/{rid}/menu?order_id={order_id}` and stop.

---

## 3. Session handling

There is **no login and no cookie**. Creating an order returns an `order_id`
and a `token`; that token is the entire session, replayed as `?access_token=`
on subsequent cart calls. The site keeps it in `localStorage` under `orders`.

Practically: the automation creates an anonymous guest cart, builds it up, and
hands over a URL. The human opens it, sees the cart, and pays. No credentials
of any kind are needed — and none should be collected.

## 4. Pickup / delivery, hours, and store state

`type` on order creation is one of `takeout` / `delivery` / `dinein` /
`curbside`. Across the 318 customer-visible stores:

- `takeout` — 318/318
- `delivery` — 305/318
- `dinein`, `curbside` — 0/318

So **pickup (`takeout`) is the safe default**, and it's also each store's
`default_type`.

**Hours** are `minutes from local midnight`, per weekday, in the store's `tz`:

```json
{"m_open": 660, "m_close": 1195}   // Bay Ridge: 11:00–19:55, same all week
```

Two flags that are easy to confuse, and the distinction matters:

- `accepting_orders` / `takeout` — *static capability*. 309 and 321 stores.
- `can_order` — *live, right now*. When I sampled at 09:50 ET, *2 of 441*
  stores returned `can_order: true`, because the rest hadn't opened yet.

Bay Ridge demonstrates this exactly: it returned `can_order: false` at 10:13 ET
while `accepting_orders` was `true`, because it opens at 11:00. The flag was
correct and transient.

Automation must branch on `can_order` at build time and fail gracefully with
"this store opens at 11:00" rather than treating it as a permanent property.
Also relevant at Bay Ridge: `lead` (10 min prep) and `advance_days: 1`
(orders can be scheduled one day ahead).

---

## 5. The menu model — and the part that will bite Task 3

One `GET .../menu` returns everything: **154 items** across 15 categories for
Bay Ridge. `menu` is a flat item list, each item carrying a `category`;
`hierarchy` holds the display ordering.

Each item has `option_groups`, and they map cleanly onto the four axes a group
order spreadsheet talks about:

| Group name | Axis | Shape |
|---|---|---|
| `Choose A Size` | size | `min 1, max 1` — required |
| `Choose Topping(s)` | toppings | `multiselect, max 0` (= unlimited), `quantities: true` |
| `Sugar Level` | sugar | `min 0, max 1` — **optional** |
| `Ice Level` | ice | `min 0, max 1` — **optional** |

Not every item has all four. Gift cards, cans and food have no size or ice
group at all, so Task 3 must treat missing groups as normal, not as an error.

There is also a **fifth axis the brief didn't name**: `Milk Alternative`
(`Soy Milk`, +$0.50) on 37 Bay Ridge items. People do write "oat milk" in a
boba spreadsheet, so `fetch_menu.py` routes it to its own `milk` axis rather
than burying it in `other`. Two further groups are genuine one-offs and stay
unmapped, and they differ per store: a stray `Milk Tea` group at Bay Ridge,
`Tajin Option` at Philadelphia.

### The trap: option vocabularies are per item, not global

This is the single most important finding for Task 3.

```
Bay Ridge:  14 distinct size option-sets, 14 sugar, 6 ice
```

The upcharge is **baked into the option name**, so one logical size has many
literal spellings, and the correct string differs per drink:

```
['Medium', 'Large .7']                x31
['Medium', 'Large .5', 'Hot .5']      x30
['Medium', 'Large 1']                 x20
['Medium', 'Large .6', 'Hot .6']      x17
```

Worse, the suffix has **five inconsistent spellings** — `Large .5`, `Large 1`,
`Large 1.5`, `Large 0.45`, and `Large.3` with no space. Across both stores I
sampled, 23 distinct size literals collapse to just four real labels:
**Medium, Large, Hot, Cold**. `fetch_menu.py` strips the suffix only when it
matches the option's own `price` field, so a size legitimately containing a
digit is never mangled.

A spreadsheet cell saying `Large` cannot be mapped to a constant. It has to be
resolved against *that item's* size list. Concrete consequences:

- **`Hot` is a size, not a temperature.** "Hot Thai Tea" is the Hot option
  on the iced item, not a separate drink.
- **66 Bay Ridge items offer no `Regular Ice` option at all** — their ice list
  is `['Less Ice', 'No Ice', 'More Ice']`. "Regular ice" means *send no ice
  modifier*, not *select an option*. Same for sugar: 31 items have no
  `Regular Sugar 100%`.
- Sugar and ice are `min: 0`. Omitting them is legal and means store default.
- `max: 0` on toppings means **unlimited**, not none.
- Sugar labels are inconsistent English (`Regular Sugar 100%` but `Less S 70%`),
  so match on the percentage, not the words.

`scripts/fetch_menu.py` already normalises each item into a resolved
`size`/`sugar`/`ice`/`toppings` vocabulary with a `canonical` map
(`{"Large": "Large .9"}`) so Task 3 matches per item by construction.

### Item names are not unique

At Bay Ridge, **14 names map to two items each** (28 of 154). These are
cross-listings — the same drink appearing in both its base category and a
promotional one:

```
'Matcha Milk'   65afe5c212be70fcd20eee87  cat='Milk Strike'
                6a062b03d439213e2f0fbbf6  cat='Matcha Series'
```

I checked all 14: price and every option group are **identical**, so either id
is safe to order. But a name → item lookup is ambiguous, and Task 3 should
break the tie deterministically (lowest id) so the same spreadsheet produces
the same cart twice running.

### Menus are genuinely per-store

Comparing Bay Ridge against the Philadelphia store I first sampled:

- 114 items shared, 26 Bay-Ridge-only, 49 Philly-only
- **111 of the 114 shared items differ in price** (Caramel Milk Tea $5.45 vs
  $4.75; Black Tea Wow Milk Cap $7.10 vs $5.35)

So a menu snapshot is per-store and must never be reused across locations, and
prices quoted to the group have to come from the target store's own menu.
Bay Ridge also sells **gift cards as menu items** (`$10 Gift Card` … `Custom
Amount Gift Card`); those should be excluded from drink matching.

---

## 6. Cart write path

**Verified live 2026-08-17.** See `task1-recommendation.txt` for the full run.

`POST api/v1/orders/{order_id}/items?access_token={token}`, form-encoded:

| Field | Notes |
|---|---|
| `id` | menu item id |
| `size` | the **price tier** from `item.prices[].name` — `Regular` for all 154 Bay Ridge items. **Not** the size option: posting `Large .5` here returns `400 Invalid size`. "Choose A Size" is an ordinary option group. |
| `quantity` | line quantity |
| `options[<group name>][<i>][name]` | option name |
| `options[<group name>][<i>][quantity]` | per-option quantity (double toppings) |
| `options[<group name>][<i>][level]` | only for groups allowing levels |

Note the addressing: options carry **no ids** (`id` is `null` on every one).
Both the group and the option are referenced **by their exact display name**,
and size is passed as its own top-level field rather than as an option group.
Name-based addressing means a menu rename silently breaks the cart, so Task 3
should re-fetch the menu per run rather than caching option strings.

### Per-drink names survive on our order, but not through the handoff

Corrected 2026-08-17 by live test. The API **does** accept and persist per-item
`for` and `notes`, despite this store's UI flags being off (`allow_for`
null, `allow_notes` false) — a GET on our order returns both intact.

What kills it is the handoff. The handoff is a **clone** (§7), and the clone
strips both fields:

```
source cart : Taro Slush for="ALICE" | Thai Tea Milk Cap for="BOB"
cloned cart : Taro Slush for=null    | Thai Tea Milk Cap for=null
```

So the cart the user actually pays from cannot carry names, and identical drinks
for different people will collapse into indistinguishable line items. The
name↔drink mapping has to live in our own output — a printed manifest alongside
the cart link. Set `for`/`notes` on our source order anyway: it is free and makes
our own order readable while debugging.

Native group ordering is separately unavailable — genuinely disabled server-side,
not merely UI-gated:

```
POST api/v1/orders  order_kind=group
-> 403 "Restaurant does not support group orders."
```

---

## 7. Recommendation

Use the JSON API. It is stable, complete, unauthenticated for our purposes, and
returns an entire store menu in one request. The UI path costs a headless
browser and a bot-defence fight for strictly less data.

Stop at `GET .../quote`, then hand the user a **clone-on-open deep link**:

```
https://kft.orderexperience.net/{restaurant_id}/menu?order_id={order_id}
```

Opening it makes the user's browser create its own order that server-side copies
our line items, so they end up owning an ordinary, editable cart. No token in the
link, no controlled browser. Verified live; full analysis and the ruled-out
alternatives are in `task1-recommendation.txt`.

The automation should never touch `checkout`/`submit`, never handle card data,
and never store an `access_token` beyond the life of one run.

Load is negligible if we cache the store directory (it changes rarely) and
fetch one menu per run.

---

## 8. Status and caveats

- §1–§6 are **verified live** against the API (re-confirmed 2026-08-17,
  including the cart writes the first pass could not execute).
- The one unexecuted step is the **client side** of the handoff: no browser will
  launch on this machine, so nobody has actually clicked the deep link. The
  `?order_id=` behaviour is read from the shipped bundle and its server call is
  proven. Clicking it once should be the first thing Task 2 does — that is where
  this spike is most likely to be wrong.
- The target snapshot is **Bay Ridge** (`650c9c3cd73592bc0e0bd50a`). The
  Philadelphia snapshot (`650c9c52d73592bc0e0bd5a7`) is kept only as the
  comparison case behind the "menus are per-store" numbers above; Task 3 should
  use Bay Ridge.
- Menu, store list, prices and hours are a point-in-time read (2026-08-17).
  Because options are addressed by name, re-fetch the menu each run rather than
  pinning these files.
- This uses the public ordering API the site itself calls, at human-scale
  request volume, and stops before payment. Anything beyond that — bulk
  scraping all 441 menus, or automating submission — is a different
  conversation and should be checked against Kung Fu Tea's terms first.
