"""Build a live Kung Fu Tea cart and stop at the user-controlled handoff.

Task 1 established a clone-on-open handoff:

    https://kft.orderexperience.net/<restaurant>/menu?order_id=<source order>

This module creates that anonymous source order, adds every row that matches the
fresh menu, refreshes its quote, and returns the link plus a person-by-person
manifest.  It deliberately has no checkout or submit operation.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import matcher, menu as menu_module
from scripts import kft_api

ORDER_TYPE = "takeout"
_DAY_KEYS = ("m", "t", "w", "th", "f", "s", "su")


def _read(call, *args, attempts: int = 2):
    """Retry safe reads once when the client identifies a transient failure."""
    for attempt in range(attempts):
        try:
            return call(*args)
        except Exception as exc:
            if attempt + 1 >= attempts or not getattr(exc, "retryable", False):
                raise
            time.sleep(0.2)


def _message(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text or "the ordering service rejected this item"


def _minutes_label(value) -> str:
    minutes = int(value or 0)
    hour, minute = divmod(minutes, 60)
    suffix = "AM" if hour < 12 else "PM"
    shown = hour % 12 or 12
    return f"{shown}:{minute:02d} {suffix}"


def _local_now(store: dict, now: _dt.datetime | None = None) -> _dt.datetime:
    current = now or _dt.datetime.now(_dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_dt.timezone.utc)
    try:
        return current.astimezone(ZoneInfo(store.get("tz") or "America/New_York"))
    except ZoneInfoNotFoundError:
        return current


def _inside_hours(store: dict, local: _dt.datetime) -> bool:
    hours = store.get("hours") or {}
    key = _DAY_KEYS[local.weekday()]
    opened, closed = hours.get(f"{key}_open"), hours.get(f"{key}_close")
    if opened is None or closed is None:
        return False
    minute = local.hour * 60 + local.minute
    if int(closed) >= int(opened):
        return int(opened) <= minute < int(closed)
    return minute >= int(opened) or minute < int(closed)


def _next_open(store: dict, local: _dt.datetime) -> str | None:
    hours = store.get("hours") or {}
    for offset in range(8):
        day = local.date() + _dt.timedelta(days=offset)
        key = _DAY_KEYS[day.weekday()]
        opened = hours.get(f"{key}_open")
        closed = hours.get(f"{key}_close")
        if opened is None or closed is None or opened == closed:
            continue
        candidate = _dt.datetime.combine(
            day, _dt.time(), tzinfo=local.tzinfo
        ) + _dt.timedelta(minutes=int(opened))
        if candidate <= local:
            continue
        when = "today" if offset == 0 else "tomorrow" if offset == 1 else day.strftime("%A")
        return f"{when} at {_minutes_label(opened)}"
    return None


def _store_state(store: dict, live_menu: dict,
                 now: _dt.datetime | None = None) -> dict:
    can_order = live_menu.get("can_order")
    accepting = store.get("accepting_orders") if store else None
    takeout = store.get("takeout") if store else None
    local = _local_now(store, now)

    if store and (accepting is False or takeout is False):
        reason = "is not accepting pickup orders"
        kind = "unavailable"
    elif can_order is True:
        reason = "is open and accepting pickup orders"
        kind = "open"
    elif can_order is False and store and _inside_hours(store, local):
        reason = "is not accepting orders right now; check the Kung Fu Tea page before paying"
        kind = "paused"
    elif can_order is False:
        opening = _next_open(store, local) if store else None
        reason = f"is closed; it next opens {opening}" if opening else "is closed right now"
        kind = "closed"
    else:
        reason = "availability could not be confirmed; check the Kung Fu Tea page"
        kind = "unknown"

    return {
        "kind": kind,
        "can_order_now": can_order,
        "accepting_orders": accepting,
        "takeout": takeout,
        "message": f"{store.get('name') or live_menu.get('name') or 'This store'} {reason}.",
        "checked_at": local.isoformat(timespec="seconds"),
        "timezone": store.get("tz") if store else None,
        "lead_minutes": store.get("lead") if store else None,
    }


def _row_key(row: dict) -> int:
    return int(row.get("row_number") or 0)


def _row_stub(row: dict) -> dict:
    found = row.get("match") or {}
    item = found.get("item") or {}
    return {
        "row_number": _row_key(row),
        "person": row.get("person") or "Unlabelled",
        "drink": item.get("name") or row.get("drink") or "Unknown drink",
        "quantity": int(found.get("quantity") or row.get("quantity") or 1),
    }


def _manifest_entry(row: dict, added: dict | None = None) -> dict:
    found = row["match"]
    entry = {
        **_row_stub(row),
        "item_id": found["item"]["id"],
        "price_tier": found.get("price_tier"),
        "options": [
            {key: option.get(key) for key in ("group", "axis", "name", "quantity")}
            for option in found.get("options") or []
        ],
        "notes": row.get("notes") or "",
        "estimated_total": found.get("total"),
    }
    if isinstance(added, dict):
        entry["cart_item_id"] = added.get("id")
        entry["actual_total"] = added.get("total_price")
    return entry


def _skip_reason(row: dict) -> str:
    messages = [issue.get("message") for issue in row.get("issues") or []
                if issue.get("level") in ("error", "warning") and issue.get("message")]
    if messages:
        return messages[-1]
    status = (row.get("match") or {}).get("status")
    return "no menu drink was selected" if status == matcher.NEEDS_DRINK else "the row is not orderable"


def _unfulfilled(row: dict) -> set[tuple[str, str]]:
    found = row.get("match") or {}
    return {
        (entry.get("axis") or "option", entry.get("asked") or "requested choice")
        for key in ("unmapped", "dropped")
        for entry in found.get(key) or []
    }


def _fingerprint(run: dict) -> str:
    rows = []
    for row in run.get("rows") or []:
        found = row.get("match") or {}
        rows.append({
            "row": _row_key(row),
            "person": row.get("person") or "",
            "notes": row.get("notes") or "",
            "status": found.get("status"),
            "item": (found.get("item") or {}).get("id"),
            "tier": found.get("price_tier"),
            "quantity": found.get("quantity"),
            "options": found.get("options") or [],
        })
    encoded = json.dumps({
        "restaurant_id": (run.get("match") or {}).get("restaurant_id"),
        "rows": rows,
    }, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:24]


def _totals(order: dict) -> dict:
    fees = {}
    for key, value in order.items():
        if key.endswith("_fee") and isinstance(value, (int, float)) and value:
            fees[key] = round(float(value), 2)
    return {
        "subtotal": order.get("subtotal"),
        "tax": order.get("tax"),
        "fees": fees,
        "total": order.get("total_amount"),
        "currency": "USD",
    }


def _find_store(records, restaurant_id: str) -> dict:
    for record in records or []:
        if record.get("id") == restaurant_id:
            return record
    return {}


def build(matched: dict, api=None, now: _dt.datetime | None = None) -> dict:
    """Build all currently matched rows and return a safe review handoff.

    Expected operational failures are represented in ``run["cart"]`` so the
    preview can name the affected people and drinks.  The anonymous access
    token is used only inside this call and is never returned or persisted.
    """
    api = api or kft_api
    restaurant_id = (matched.get("match") or {}).get("restaurant_id")
    result = dict(matched)
    if not restaurant_id:
        result["cart"] = {
            "status": "failed", "review_ready": False,
            "error": "No Kung Fu Tea store is attached to this order.",
            "added": [], "failed": [], "skipped": [], "warnings": [],
        }
        result.pop("handoff_url", None)
        return result

    warnings: list[str] = []
    try:
        stores = _read(api.list_stores)
        store = _find_store(stores, restaurant_id)
        if not store:
            warnings.append("The store directory no longer lists this location.")
    except Exception as exc:
        store = {}
        warnings.append(f"Store hours could not be refreshed: {_message(exc)}")

    try:
        raw_menu = _read(api.get_menu, restaurant_id)
    except Exception as exc:
        result["cart"] = {
            "status": "failed", "review_ready": False,
            "error": f"The live menu could not be loaded: {_message(exc)}",
            "store": {"restaurant_id": restaurant_id, "name": store.get("name")},
            "added": [], "failed": [], "skipped": [], "warnings": warnings,
        }
        result.pop("handoff_url", None)
        return result

    live_store = menu_module.live_store_menu(raw_menu, restaurant_id)
    live_matched = matcher.match(matched, store=live_store)
    result = dict(live_matched)
    fingerprint = _fingerprint(live_matched)
    state = _store_state(store, raw_menu, now)
    store_summary = {
        "restaurant_id": restaurant_id,
        "name": store.get("name") or raw_menu.get("name") or live_store.store,
        "address": store.get("address"),
        "city": store.get("city"),
        "state": store.get("state"),
        "zip": store.get("zip"),
        "order_type": ORDER_TYPE,
        "state_now": state,
    }

    existing = matched.get("cart") or {}
    if (existing.get("status") == "ready"
            and existing.get("input_fingerprint") == fingerprint
            and matched.get("handoff_url")):
        cart = dict(existing)
        cart["store"] = store_summary
        cart["warnings"] = list(dict.fromkeys((cart.get("warnings") or []) + warnings))
        result["cart"] = cart
        result["manifest"] = list(cart.get("added") or [])
        result["handoff_url"] = matched["handoff_url"]
        return result

    previous = {_row_key(row): row for row in matched.get("rows") or []}
    eligible: list[dict] = []
    failed: list[dict] = []
    skipped: list[dict] = []
    for row in live_matched.get("rows") or []:
        old_row = previous.get(_row_key(row), {})
        if row.get("match", {}).get("status") == matcher.READY:
            newly_missing = _unfulfilled(row) - _unfulfilled(old_row)
            if ((old_row.get("match") or {}).get("status") == matcher.READY
                    and newly_missing):
                entry = _row_stub(row)
                choices = ", ".join(asked for _axis, asked in sorted(newly_missing))
                entry.update({
                    "code": "menu_changed",
                    "reason": f"the live menu no longer supports {choices}",
                })
                failed.append(entry)
                continue
            eligible.append(row)
            continue
        entry = _row_stub(row)
        entry["reason"] = _skip_reason(row)
        was_ready = (old_row.get("match") or {}).get("status") == matcher.READY
        if was_ready:
            old_item = (old_row.get("match") or {}).get("item") or {}
            same_id = live_store.by_id(old_item.get("id"))
            unavailable = (same_id if same_id and not same_id.available else None)
            unavailable = unavailable or live_store.unorderable(old_item.get("name"))
            entry["code"] = ("sold_out" if unavailable and unavailable.sold_out
                             else "unavailable" if unavailable else "menu_changed")
            failed.append(entry)
        else:
            entry["code"] = "not_mapped"
            skipped.append(entry)

    counts = {
        "rows_total": len(live_matched.get("rows") or []),
        "mapped_rows": len(eligible),
        "mapped_drinks": sum(_row_stub(row)["quantity"] for row in eligible),
        "added_rows": 0,
        "added_drinks": 0,
        "failed_rows": len(failed),
        "skipped_rows": len(skipped),
    }

    def finish(status: str, error: str | None = None, order_id: str | None = None,
               added: list[dict] | None = None, order: dict | None = None) -> dict:
        manifest = added or []
        cart = {
            "status": status,
            "review_ready": bool(order_id and manifest),
            "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "input_fingerprint": fingerprint,
            "source_order_id": order_id,
            "store": store_summary,
            "counts": counts,
            "added": manifest,
            "failed": failed,
            "skipped": skipped,
            "warnings": warnings,
            "totals": _totals(order or {}),
        }
        if error:
            cart["error"] = error
        result["cart"] = cart
        result["manifest"] = manifest
        if cart["review_ready"]:
            result["handoff_url"] = api.handoff_url(restaurant_id, order_id)
        else:
            result.pop("handoff_url", None)
        return result

    if not eligible:
        return finish("failed", "No mapped drinks are currently available to add.")

    if state["kind"] == "unavailable":
        for row in eligible:
            entry = _row_stub(row)
            entry.update({"code": "store_unavailable", "reason": state["message"]})
            failed.append(entry)
        counts["failed_rows"] = len(failed)
        return finish("failed", state["message"])

    try:
        created = api.create_order(restaurant_id, ORDER_TYPE)
        order_id, token = created["order_id"], created["token"]
    except Exception as exc:
        return finish("failed", f"The cart could not be created: {_message(exc)}")

    added_manifest: list[dict] = []
    for row in eligible:
        found = row["match"]
        options: dict[str, list[dict]] = {}
        for option in found.get("options") or []:
            options.setdefault(option["group"], []).append({
                "name": option["name"], "quantity": option.get("quantity") or 1,
            })
        try:
            added = api.add_item(
                order_id, token, found["item"]["id"],
                size=found.get("price_tier"), options=options,
                quantity=found.get("quantity") or 1,
                notes=row.get("notes") or None,
                for_name=row.get("person") or None,
            )
            entry = _manifest_entry(row, added)
            added_manifest.append(entry)
            counts["added_rows"] += 1
            counts["added_drinks"] += entry["quantity"]
        except Exception as exc:
            entry = _row_stub(row)
            entry.update({"code": "add_failed", "reason": _message(exc)})
            failed.append(entry)

    counts["failed_rows"] = len(failed)
    if not added_manifest:
        return finish("failed", "Kung Fu Tea rejected every mapped drink.", order_id)

    # Quote is a read-only pre-check.  Its current response is an empty 200; the
    # refreshed totals are read back from the order immediately afterwards.
    try:
        _read(api.quote_order, order_id, token)
    except Exception as exc:
        warnings.append(f"The final quote could not be refreshed: {_message(exc)}")

    try:
        order = _read(api.get_order, order_id, token)
    except Exception as exc:
        # Every add was acknowledged, so the handoff is still useful.  Say that
        # its totals could not be verified instead of discarding the cart.
        order = {}
        warnings.append(f"The cart was built, but its totals could not be verified: {_message(exc)}")

    status = "ready" if not failed else "partial"
    return finish(status, order_id=order_id, added=added_manifest, order=order)
