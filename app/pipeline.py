"""The seam between importing and building the cart.

Importing stops at a structured order list. This module is where the matcher and
the cart builder plug in, and it is deliberately the only place the web layer
knows about them — nothing in server.py imports either directly.

THE HANDOFF CONTRACT
--------------------
Both downstream stages receive one dict, the saved run:

    {
      "run_id":     "9f2c...",              # hex, also the preview URL
      "created_at": 1755500000.0,
      "source":     {"kind": "upload" | "google_sheet" | "json", ...},
      "column_map": {"Name": "Who's it for", ...},   # our field -> their header
      "stats":      {"rows": 8, "drinks": 9, "people": 7, ...},
      "issues":     [{"level", "message", "field", "row"}, ...],   # sheet-level
      "rows": [
        {
          "row_number": 2,                  # the row in the user's sheet
          "person":     "Alice",
          "drink":      "taro slush ",      # verbatim, exactly as it was typed
          "size":       "LG",
          "sugar":      "half sweet",
          "ice":        "Less ice",
          "toppings":   ["boba"],
          "milk":       "Soy",
          "temperature":"",                 # a "hot or iced" column, if they had one
          "quantity":   1,
          "notes":      "",
          "extra":      {"Venmo": "@alice"},# unmapped columns, kept not dropped
          "canonical": {                    # the same values, in the store's words
            "drink":    "Taro Slush",       # "" when no menu item has that name
            "size":     "Large",            # a label, NOT the literal "Large .7"
            "sugar":    "50%",              # always a percentage
            "ice":      "Less Ice",
            "toppings": ["Boba"],
            "milk":     "",                 # "" = not offered here, see issues
            "topping_quantities": {"Pudding": 2}   # only when above one
          },
          "match": {                        # added by the match stage, below
            "status":   "ready",            # | "needs_drink" | "skipped"
            "matched_by": "exact",          # | normalised | alias | chosen
            "item":     {"id": "6a24...", "name": "Taro Slush", "price": 5.95, ...},
            "price_tier": "Regular",        # the cart's `size` field (Task 1 §6)
            "quantity": 1,
            "options": [                    # post verbatim, addressed by name
              {"group": "Choose A Size", "axis": "size",
               "name": "Large .7", "quantity": 1, "price": 0.7}
            ],
            "unit_price": 5.95, "extras_price": 0.7, "total": 6.65,
            "unmapped": [{"axis": "toppings", "asked": "...", "why": "..."}],
            "dropped":  [{"axis": "ice", "asked": "Less Ice", "why": "..."}],
            "choices":  {"toppings": ["Boba", "Pudding", ...]},   # only what went wrong
            "available": {"size": ["Medium", "Large"], "ice": [], ...}  # all of it
          },
          "issues":     [...],
          "ok":         true                # false = do not try to order this row
        }
      ]
    }

TWO FIELDS PER VALUE, AND WHICH ONE TO USE
------------------------------------------
The raw fields are the user's own words. `canonical` is those words resolved
against the store's option set by app/options.py — that is the one to build a
cart from.

`canonical` deliberately stops at the store-level label. Option *literals* are
per item (Task 1 §5): "Large" is "Large .7" on one drink and "Large 1" on
another, so the last step can only be taken once the drink is matched. Use each
item's own `canonical` map in data/menu-*.json to get there. Sugar is given as a
percentage because the store's own names are inconsistent English
("Regular Sugar 100%" but "Less S 70%") — match on the number.

An empty canonical value means "no choice made, use the store default". It never
means the row is broken; if we could not resolve something the reason is on
`row.issues` and the raw text is still there to fall back on.

`row.match` is the third level, and the one to build a cart from: the item id,
and the option strings exactly as that item spells them. Everything in
`match.options` is posted as-is — `options[<group>][<i>][name]` — and the item's
`price_tier` goes in the top-level `size` field, not the size option (Task 1 §6).
A row with `match.status != "ready"` has no line to post; it is waiting on a
person, and the preview says what for.

TO PLUG IN
----------
the cart stage: add app/cart.py with  build(matched: dict) -> dict

It is picked up automatically by status() and process() below; no change to this
file or the server is needed.
"""

from __future__ import annotations

import json
import sys

# name, module, entry point, description, pure?
#
# "Pure" means the stage only reads: same run in, same run out, nothing sent
# anywhere. Those stages run every time the preview is read, so the page always
# shows a match that agrees with the rows as they are right now. The cart stage
# is not pure — it creates a real order on the store's server — so it only runs
# when somebody presses the button.
STAGES = [
    ("import", "app.importer", None,
     "Read the sheet and resolve each row to the store's options", True),
    ("match", "app.matcher", "match", "Match each row to a live menu item", True),
    ("cart", "app.cart", "build", "Build the cart and produce the handoff link", False),
]


class PipelineNotReady(RuntimeError):
    """A downstream stage has not been built yet."""


def _load(module_name: str, attribute: str | None):
    try:
        module = __import__(module_name, fromlist=["*"])
    except ImportError:
        return None
    if attribute is None:
        return module
    return getattr(module, attribute, None)


def status() -> list[dict]:
    """Which stages exist right now. The preview page renders this honestly."""
    report = []
    for name, module_name, attribute, description, pure in STAGES:
        report.append({
            "name": name,
            "module": module_name,
            "description": description,
            "pure": pure,
            "ready": _load(module_name, attribute) is not None,
        })
    return report


def next_stage() -> dict | None:
    for stage in status():
        if not stage["ready"]:
            return stage
    return None


def enrich(run: dict) -> dict:
    """Run the read-only stages, skipping any that aren't built yet.

    Cheap, repeatable and safe to call on every read: it sends nothing anywhere
    and re-derives its own output, so a preview built from it can never disagree
    with the rows it is showing. Nothing is saved by this — the caller decides.
    """
    result = dict(run)
    for _name, module_name, attribute, _description, pure in STAGES:
        if attribute is None or not pure:
            continue
        entry = _load(module_name, attribute)
        if entry is not None:
            result = entry(result)
    return result


def process(run: dict) -> dict:
    """Run the saved import through every stage that exists.

    Returns the run with whatever downstream stages produced merged in. Raises
    PipelineNotReady if the next stage is not built, so callers can show the
    import result and say what is missing rather than pretend.
    """
    result = dict(run)
    for name, module_name, attribute, _description, _pure in STAGES:
        if attribute is None:
            continue
        entry = _load(module_name, attribute)
        if entry is None:
            raise PipelineNotReady(
                f"stage '{name}' is not built yet — expected {module_name}.{attribute}()")
        result = entry(result)
    return result


if __name__ == "__main__":  # python3 -m app.pipeline .runs/<id>.json
    if len(sys.argv) < 2:
        for stage in status():
            print(f"[{'x' if stage['ready'] else ' '}] {stage['name']:8} {stage['description']}")
        sys.exit(0)
    with open(sys.argv[1], encoding="utf-8") as handle:
        run_data = json.load(handle)
    try:
        print(json.dumps(process(run_data), indent=1, default=str))
    except PipelineNotReady as exc:
        print(f"pipeline stopped: {exc}", file=sys.stderr)
        sys.exit(2)
