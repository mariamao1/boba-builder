"""Solo ordering: one person ordering just for themselves.

Alongside spreadsheet import (Task 3) and the group link (Task 8), this is the
third entry path. It reuses the exact same pipeline as everything else —
`importer.import_json` for normalization, `runs` for persistence,
`pipeline.enrich`/`pipeline.process` for matching and the cart handoff — just
for one person's drinks. A run whose `source.kind` is "solo" is matched, edited,
and handed off exactly like any other run.

The defensible advantage over the official Kung Fu Tea app is a usuals-first
reorder, not a menu-first browse: the solo page opens on the browser-local
favorites this app already keeps (Task 15, complete modifier sets, no account)
plus the browser's own saved-order history (Task 12), so a repeat solo order is
a one-tap reorder straight to the preview instead of rebuilding every modifier.
See docs/task27-solo-order.md for the full justification and tap count.
"""

from __future__ import annotations

import datetime as dt
import json

from . import importer, menu, runs

#: Fields the solo endpoint accepts on a drink. Mirrors importer.EDITABLE_FIELDS
#: minus nothing — a solo drink is one editable row — but "drink" is required
#: here while a spreadsheet row may legitimately be blank.
SOLO_AXES = ("size", "sugar", "ice", "milk", "temperature")
MAX_QUANTITY = importer.MAX_QUANTITY
DEFAULT_PERSON = "You"


class SoloOrderError(ValueError):
    """The solo drink as sent cannot become a run. Carries an HTTP status."""

    status = 400


class UnknownSoloStore(SoloOrderError):
    status = 404


def _text(value, limit: int = 160) -> str:
    return " ".join(str(value or "").split())[:limit]


def clean_drink(entry: dict, *, index: int | None = None) -> dict:
    """Validate one solo drink into a normalized row dict (without person).

    `index` is the 1-based position in a multi-drink order; when given, errors
    name the drink so the page can say what to fix instead of showing a blank
    preview.
    """
    prefix = f"drink {index}: " if index is not None else ""
    if not isinstance(entry, dict):
        raise SoloOrderError(prefix + "send one drink as a JSON object")
    drink = _text(entry.get("drink"), 160)
    if not drink:
        raise SoloOrderError(prefix + "pick a drink first")
    try:
        quantity = int(str(entry.get("quantity", 1)).strip() or 1)
    except (ValueError, AttributeError):
        raise SoloOrderError(prefix + "that quantity isn't a number") from None
    if quantity < 1:
        raise SoloOrderError(prefix + "a quantity of nothing isn't an order")
    if quantity > MAX_QUANTITY:
        raise SoloOrderError(
            prefix + f"at most {MAX_QUANTITY} of one drink per solo order")

    toppings = entry.get("toppings", [])
    if isinstance(toppings, str):
        toppings = [toppings]
    if not isinstance(toppings, list):
        raise SoloOrderError(prefix + "toppings must be a list of names")
    cleaned_toppings = [_text(item, 100) for item in toppings]
    cleaned_toppings = [item for item in cleaned_toppings if item][:20]

    row = {
        "drink": drink,
        "quantity": quantity,
        "notes": _text(entry.get("notes"), 500),
        "toppings": ", ".join(cleaned_toppings),
    }
    for axis in SOLO_AXES:
        row[axis] = _text(entry.get(axis), 80)
    return row


def clean_payload(payload: dict) -> dict:
    """Validate a single-drink solo payload into one normalized row dict.

    Kept for the one-drink shape; multi-drink orders go through clean_order.
    """
    row = clean_drink(payload)
    row["person"] = _text(payload.get("person"), 80) or DEFAULT_PERSON
    return row


def clean_order(payload: dict) -> tuple[str, list[dict]]:
    """Validate a solo order into (person, row dicts).

    Accepts either one drink (`{"drink": ..., ...}`) or several
    (`{"person": ..., "drinks": [{...}, ...]}`); every row carries the same
    person, because a solo order is one person's drinks.
    """
    if not isinstance(payload, dict):
        raise SoloOrderError("send one drink as a JSON object")
    if "drinks" not in payload:
        row = clean_payload(payload)
        return row["person"], [row]
    drinks = payload["drinks"]
    if not isinstance(drinks, list) or not drinks:
        raise SoloOrderError("add at least one drink to a solo order")
    person = _text(payload.get("person"), 80) or DEFAULT_PERSON
    rows = []
    for position, entry in enumerate(drinks, 1):
        row = clean_drink(entry, index=position)
        row["person"] = person
        rows.append(row)
    return person, rows


def _timestamp(now: dt.datetime | None = None) -> str:
    value = now or dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def create_run(payload: dict, restaurant_id: str | None = None,
               *, now: dt.datetime | None = None) -> tuple[str, dict]:
    """Turn one person's solo drinks into a saved pipeline run.

    Returns (run_id, run payload including run_id). The run's source.kind is
    "solo"; everything downstream treats it like any other run.
    """
    _person, rows = clean_order(payload)
    store_id = (restaurant_id or payload.get("restaurant_id") or menu.TARGET_STORE)
    store_id = _text(store_id, 100)
    summary = menu.store_summary(store_id) if store_id else None
    if summary is None:
        raise UnknownSoloStore("that store menu is not available")

    result = importer.import_json(json.dumps({"rows": rows}), restaurant_id=store_id)
    result.source = {
        "kind": "solo",
        "restaurant_id": store_id,
        "store": summary.get("name"),
        "ordered_at": _timestamp(now),
    }
    saved = result.as_dict()
    run_id = runs.save(saved)
    saved["run_id"] = run_id
    return run_id, saved
