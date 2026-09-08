# Task 11 — Unified Spreadsheet and Group-Link Flow

The home page at `/` is the single starting point. It asks the organizer how
the group wants to collect drinks and exposes both paths without making either
one a separate product:

1. **Start a group-order link.** Choose a visible takeout location from Kung Fu
   Tea's full store directory, enter an optional organizer name and order title,
   choose how long the room should remain open, and create the room. The chosen
   store's menu is fetched and cached before the browser opens the private
   organizer dashboard, where its participant link can be copied and shared.
   Participants see only drinks, modifiers, and prices from the selected store.
   After submissions are reviewed, **Finalize & review cart** creates a saved
   run and opens its preview.
2. **Import a spreadsheet.** Upload CSV/XLSX or paste a public Google Sheets
   URL. The import immediately creates a saved run and opens its preview.

The fork ends there. Both sources are normalized by `app.importer`, persisted
by `app.runs`, enriched by the same menu matcher, edited through the same run
API, and processed by `app.pipeline` into the same cart manifest and Kung Fu
Tea handoff URL. A run's `source.kind` records how it began but does not select
a different matching or cart-building implementation.

The selected group-order store is immutable. Its `restaurant_id` is persisted
on the room and copied into the saved run's source metadata. Participant menu
loading, normalization, preview matching and search, live-menu refresh, cart
creation, and the final handoff URL therefore all use the same location.
The full store directory is cached for six hours, and fetched store menus are
cached under `.menu-cache/` so rooms survive a server restart or later network
interruption. The two committed menu snapshots remain offline fallbacks.
The entry page keeps the directory behind a search combobox: typing a store
name, city, address, state, or ZIP opens at most ten matching results, with
arrow-key and Enter selection as well as pointer selection.

```text
shared group link ── finalize ─┐
                              ├─ saved run → preview/reconcile → build cart → Kung Fu Tea
CSV/XLSX/Google Sheet ─ import ┘
```

Preview copy and navigation use that source metadata. Spreadsheet runs return
to the import choice. Group-order runs return to the authenticated organizer
page; its token remains in that browser's local storage and is never added to
the preview URL.
