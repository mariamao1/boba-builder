"""The seam between Task 2 and Tasks 3-4.

Task 2 stops at a normalised order list. This module is where the next two
tasks plug in, and it is deliberately the only place the web layer knows about
them — nothing in server.py imports a matcher or a cart builder directly.

THE HANDOFF CONTRACT
--------------------
Tasks 3-4 receive one dict, the saved run:

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
          "drink":      "Taro Slush",       # verbatim, not matched to the menu
          "size":       "Large",            # verbatim: "Large", not "Large .7"
          "sugar":      "50%",
          "ice":        "Less ice",
          "toppings":   ["Boba"],
          "milk":       "Soy",
          "temperature":"",                 # a "hot or iced" column, if they had one
          "quantity":   1,
          "notes":      "",
          "extra":      {"Venmo": "@alice"},# unmapped columns, kept not dropped
          "issues":     [...],
          "ok":         true                # false = do not try to order this row
        }
      ]
    }

Values are the user's own words. Resolving them is Task 3's job and cannot be
done here: size/sugar/ice vocabularies are per menu item, so "Large" only means
something once the drink is known (Task 1 recommendation, §5).

TO PLUG IN
----------
Task 3: add app/matcher.py with  match(run: dict) -> dict
Task 4: add app/cart.py    with  build(matched: dict) -> dict

Both are picked up automatically by status() and process() below; no change to
this file or the server is needed.
"""

from __future__ import annotations

import json
import sys

STAGES = [
    ("import", "app.importer", None, "Read the sheet and normalise the rows"),
    ("match", "app.matcher", "match", "Resolve each row against the live menu"),
    ("cart", "app.cart", "build", "Build the cart and produce the handoff link"),
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
    for name, module_name, attribute, description in STAGES:
        report.append({
            "name": name,
            "module": module_name,
            "description": description,
            "ready": _load(module_name, attribute) is not None,
        })
    return report


def next_stage() -> dict | None:
    for stage in status():
        if not stage["ready"]:
            return stage
    return None


def process(run: dict) -> dict:
    """Run the saved import through every stage that exists.

    Returns the run with whatever downstream stages produced merged in. Raises
    PipelineNotReady if the next stage is not built, so callers can show the
    import result and say what is missing rather than pretend.
    """
    result = dict(run)
    for name, module_name, attribute, _description in STAGES:
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
