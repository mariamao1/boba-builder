"""Whole-order templates, server side.

Templates themselves live in the browser (see ``app/static/order-templates.js``
and ``docs/task28-order-templates.md``): the server never stores them. This
module only validates a template's entries when one is loaded as a starting
point for a new run — the spreadsheet-path alternative to uploading a file —
and turns them into a normal pipeline run through ``importer.import_json``,
exactly the entry point solo orders and group finalization use. A run whose
``source.kind`` is ``"order_template"`` is matched, edited, and handed off
exactly like any other run.
"""

from __future__ import annotations

import datetime as dt
import json

from . import importer, menu, runs

#: Fields a template entry accepts. Mirrors solo.SOLO_AXES plus the person's
#: name: a template entry is one editable row with its owner attached.
TEMPLATE_AXES = ("size", "sugar", "ice", "milk", "temperature")
MAX_QUANTITY = importer.MAX_QUANTITY
MAX_ENTRIES = 60
MAX_TEMPLATE_NAME = 80


class OrderTemplateError(ValueError):
    """A template's entries as sent cannot become a run. Carries HTTP status."""

    status = 400


class UnknownTemplateStore(OrderTemplateError):
    status = 404


def _text(value, limit: int = 160) -> str:
    return " ".join(str(value or "").split())[:limit]


def clean_entry(entry: dict, *, index: int) -> dict:
    """Validate one template entry into a normalized row dict (with person)."""
    prefix = f"entry {index}: "
    if not isinstance(entry, dict):
        raise OrderTemplateError(prefix + "send one drink as a JSON object")
    person = _text(entry.get("person"), 80)
    if not person:
        raise OrderTemplateError(prefix + "each drink needs the name of who ordered it")
    drink = _text(entry.get("drink"), 160)
    if not drink:
        raise OrderTemplateError(prefix + "pick a drink first")
    try:
        quantity = int(str(entry.get("quantity", 1)).strip() or 1)
    except (ValueError, AttributeError):
        raise OrderTemplateError(prefix + "that quantity isn't a number") from None
    if quantity < 1:
        raise OrderTemplateError(prefix + "a quantity of nothing isn't an order")
    if quantity > MAX_QUANTITY:
        raise OrderTemplateError(
            prefix + f"at most {MAX_QUANTITY} of one drink per entry")

    toppings = entry.get("toppings", [])
    if isinstance(toppings, str):
        toppings = toppings.split(",")
    if not isinstance(toppings, list):
        raise OrderTemplateError(prefix + "toppings must be a list of names")
    cleaned_toppings = [_text(item, 100) for item in toppings]
    cleaned_toppings = [item for item in cleaned_toppings if item][:20]

    row = {
        "person": person,
        "drink": drink,
        "quantity": quantity,
        "notes": _text(entry.get("notes"), 500),
        "toppings": ", ".join(cleaned_toppings),
    }
    for axis in TEMPLATE_AXES:
        row[axis] = _text(entry.get(axis), 80)
    return row


def clean_entries(entries) -> list[dict]:
    """Validate a template's entry list into normalized row dicts."""
    if not isinstance(entries, list) or not entries:
        raise OrderTemplateError("a template needs at least one named drink")
    if len(entries) > MAX_ENTRIES:
        raise OrderTemplateError(
            f"a template holds at most {MAX_ENTRIES} drinks")
    return [clean_entry(entry, index=position)
            for position, entry in enumerate(entries, 1)]


def _timestamp(now: dt.datetime | None = None) -> str:
    value = now or dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def create_run(payload: dict, restaurant_id: str | None = None,
               *, now: dt.datetime | None = None) -> tuple[str, dict]:
    """Turn a template's entries into a saved pipeline run.

    Returns (run_id, run payload including run_id). Every row keeps its own
    person, because a template is the whole group's order — unlike a solo
    order, which is one person's drinks. Everything downstream treats the
    run like any other run, so entries can still be tweaked, added, or
    dropped on the preview before anything reaches a cart.
    """
    if not isinstance(payload, dict):
        raise OrderTemplateError("send a template as a JSON object")
    rows = clean_entries(payload.get("entries"))
    template_name = _text(payload.get("template_name"), MAX_TEMPLATE_NAME)
    store_id = (restaurant_id or payload.get("restaurant_id") or menu.TARGET_STORE)
    store_id = _text(store_id, 100)
    summary = menu.store_summary(store_id) if store_id else None
    if summary is None:
        raise UnknownTemplateStore("that store menu is not available")

    result = importer.import_json(json.dumps({"rows": rows}), restaurant_id=store_id)
    result.source = {
        "kind": "order_template",
        "template_name": template_name,
        "restaurant_id": store_id,
        "store": summary.get("name"),
        "ordered_at": _timestamp(now),
    }
    saved = result.as_dict()
    run_id = runs.save(saved)
    saved["run_id"] = run_id
    return run_id, saved
