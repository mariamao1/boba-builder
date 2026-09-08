"""Durable, browser-scoped snapshots of completed carts.

Runs are short-lived working files.  Saved orders are the organizer's archive:
they keep the exact cart manifest and totals that were shown at handoff time,
plus enough normalized input to start a new run later.  Access is scoped with
an opaque secret generated and retained by the browser; only its hash is ever
written to disk.
"""

from __future__ import annotations

import copy
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import secrets
import threading
from pathlib import Path

from . import runs

SAVED_ORDER_DIR = Path(os.environ.get(
    "BOBA_SAVED_ORDER_DIR",
    Path(__file__).resolve().parent.parent / ".saved-orders",
))
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
ORDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
MAX_LABEL_LENGTH = 120

_lock = threading.RLock()


class SavedOrderError(ValueError):
    status = 400


class SavedOrderUnauthorized(SavedOrderError):
    status = 401


class SavedOrderNotFound(SavedOrderError):
    status = 404


class OrderNotFinished(SavedOrderError):
    status = 409


def _directory() -> Path:
    SAVED_ORDER_DIR.mkdir(parents=True, exist_ok=True)
    return SAVED_ORDER_DIR


def _owner_hash(token: str | None) -> str:
    if not token or not TOKEN_RE.fullmatch(token):
        raise SavedOrderUnauthorized("this browser does not have a saved-orders key")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _path(order_id: str) -> Path:
    if not ORDER_ID_RE.fullmatch(order_id or ""):
        raise SavedOrderNotFound("that saved order was not found")
    return _directory() / f"{order_id}.json"


def _timestamp(now: dt.datetime | None = None) -> str:
    value = now or dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clean_label(value) -> str:
    label = " ".join(str(value or "").split())
    if not label:
        raise SavedOrderError("give this order a name")
    if len(label) > MAX_LABEL_LENGTH:
        raise SavedOrderError(f"order names must be {MAX_LABEL_LENGTH} characters or fewer")
    return label


def _clean_date(value, now: dt.datetime | None = None) -> str:
    if not value:
        current = now or dt.datetime.now(dt.timezone.utc)
        return current.date().isoformat()
    try:
        return dt.date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise SavedOrderError("order date must be a real date in YYYY-MM-DD format") from exc


def _read_file(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write(order: dict) -> None:
    path = _path(order["id"])
    tmp = path.with_suffix(f".{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(order, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)


def _source_summary(source: dict, store: dict | None = None) -> dict:
    kind = source.get("kind") or "upload"
    original_kind = source.get("original_kind") if kind == "saved_order" else kind
    store = store or {}
    return {
        "type": "group_link" if original_kind == "group_order" else "sheet",
        "kind": kind,
        "original_kind": original_kind,
        "title": source.get("title"),
        "filename": source.get("filename"),
        "sheet": source.get("sheet"),
        "session_id": source.get("session_id"),
        "restaurant_id": source.get("restaurant_id") or store.get("restaurant_id"),
        "store": source.get("store") or store.get("name"),
    }


def _base_run(run: dict) -> dict:
    """Keep editable input, but no old cart URL or derived menu payload."""
    rows = []
    row_keys = (
        "row_number", "person", "drink", "size", "sugar", "ice", "toppings",
        "milk", "temperature", "quantity", "notes", "extra", "canonical",
        "suggestions", "issues", "ok",
    )
    for original in run.get("rows") or []:
        rows.append({key: copy.deepcopy(original[key]) for key in row_keys if key in original})
    return {
        "source": copy.deepcopy(run.get("source") or {}),
        "column_map": copy.deepcopy(run.get("column_map") or {}),
        "stats": copy.deepcopy(run.get("stats") or {}),
        "issues": copy.deepcopy(run.get("issues") or []),
        "rows": rows,
    }


def _public(order: dict, *, detail: bool) -> dict:
    keys = (
        "id", "label", "order_date", "saved_at", "source", "store", "counts",
        "totals", "status", "source_run_id",
    )
    result = {key: copy.deepcopy(order.get(key)) for key in keys}
    if detail:
        result.update({
            "items": copy.deepcopy(order.get("items") or []),
            "failed": copy.deepcopy(order.get("failed") or []),
            "skipped": copy.deepcopy(order.get("skipped") or []),
        })
    return result


def save_finished(run: dict, owner_token: str | None, label, order_date=None,
                  *, now: dt.datetime | None = None) -> dict:
    owner = _owner_hash(owner_token)
    cart = run.get("cart") or {}
    if not cart.get("review_ready") or cart.get("status") not in ("ready", "partial"):
        raise OrderNotFinished("build a reviewable cart before saving this order")

    saved_at = _timestamp(now)
    source_run_id = str(run.get("run_id") or "")
    stats = run.get("stats") or {}
    cart_counts = cart.get("counts") or {}
    items = copy.deepcopy(cart.get("added") or run.get("manifest") or [])
    requested = sum(int(row.get("quantity") or 1) for row in run.get("rows") or [])
    placed = sum(int(item.get("quantity") or 1) for item in items)
    not_placed = sum(int(item.get("quantity") or 1)
                     for key in ("failed", "skipped") for item in cart.get(key) or [])
    record = {
        "id": secrets.token_urlsafe(18),
        "owner_hash": owner,
        "label": _clean_label(label),
        "order_date": _clean_date(order_date, now),
        "saved_at": saved_at,
        "source_run_id": source_run_id,
        "source": _source_summary(run.get("source") or {}, cart.get("store") or {}),
        "store": copy.deepcopy(cart.get("store") or {}),
        "status": cart.get("status"),
        "counts": {
            "people": int(stats.get("people") or 0),
            "order_lines": int(stats.get("rows") or len(run.get("rows") or [])),
            "requested_drinks": int(cart_counts.get("requested_drinks", requested)),
            "placed_drinks": int(cart_counts.get("added_drinks", placed)),
            "not_placed_drinks": int(cart_counts.get("not_added_drinks", not_placed)),
        },
        "totals": copy.deepcopy(cart.get("totals") or {}),
        "items": items,
        "failed": copy.deepcopy(cart.get("failed") or []),
        "skipped": copy.deepcopy(cart.get("skipped") or []),
        "snapshot": _base_run(run),
    }

    # Saving the same completed run twice updates its label/date instead of
    # creating indistinguishable archive entries after a double click.
    with _lock:
        for path in _directory().glob("*.json"):
            existing = _read_file(path)
            if (existing and existing.get("owner_hash") == owner
                    and source_run_id and existing.get("source_run_id") == source_run_id):
                record["id"] = existing["id"]
                record["saved_at"] = existing.get("saved_at") or saved_at
                break
        _write(record)
    return _public(record, detail=True)


def list_orders(owner_token: str | None) -> list[dict]:
    owner = _owner_hash(owner_token)
    with _lock:
        records = [value for path in _directory().glob("*.json")
                   if (value := _read_file(path)) and value.get("owner_hash") == owner]
    records.sort(key=lambda value: (value.get("order_date") or "", value.get("saved_at") or ""),
                 reverse=True)
    return [_public(record, detail=False) for record in records]


def get(order_id: str, owner_token: str | None) -> dict:
    owner = _owner_hash(owner_token)
    with _lock:
        record = _read_file(_path(order_id))
    if not record or record.get("owner_hash") != owner:
        raise SavedOrderNotFound("that saved order was not found in this browser")
    return _public(record, detail=True)


def _private(order_id: str, owner_token: str | None) -> dict:
    owner = _owner_hash(owner_token)
    with _lock:
        record = _read_file(_path(order_id))
    if not record or record.get("owner_hash") != owner:
        raise SavedOrderNotFound("that saved order was not found in this browser")
    return record


def repeat(order_id: str, owner_token: str | None) -> str:
    saved = _private(order_id, owner_token)
    payload = copy.deepcopy(saved.get("snapshot") or {})
    saved_source = saved.get("source") or {}
    payload["source"] = {
        "kind": "saved_order",
        "saved_order_id": saved["id"],
        "label": saved["label"],
        "original_kind": saved_source.get("original_kind") or saved_source.get("kind"),
        "restaurant_id": saved_source.get("restaurant_id"),
        "store": saved_source.get("store"),
    }
    return runs.save(payload)


def export_csv(order_id: str, owner_token: str | None) -> bytes:
    saved = _private(order_id, owner_token)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "Name", "Drink", "Size", "Sugar", "Ice", "Toppings", "Milk",
        "Qty", "Notes", "Cart status",
    ])
    placed_rows = {int(item.get("row_number") or 0) for item in saved.get("items") or []}
    for row in (saved.get("snapshot") or {}).get("rows") or []:
        canonical = row.get("canonical") or {}
        topping_counts = canonical.get("topping_quantities") or {}
        topping_names = canonical.get("toppings") or row.get("toppings") or []
        toppings = ", ".join(
            f"{topping_counts.get(name)}x {name}" if topping_counts.get(name, 1) > 1 else name
            for name in topping_names
        )
        row_number = int(row.get("row_number") or 0)
        writer.writerow([
            _csv_cell(row.get("person")),
            _csv_cell(canonical.get("drink") or row.get("drink")),
            _csv_cell(canonical.get("size") or row.get("size") or row.get("temperature")),
            _csv_cell(canonical.get("sugar") or row.get("sugar")),
            _csv_cell(canonical.get("ice") or row.get("ice")),
            _csv_cell(toppings),
            _csv_cell(canonical.get("milk") or row.get("milk")),
            row.get("quantity") or 1,
            _csv_cell(row.get("notes")),
            "Placed" if row_number in placed_rows else "Not placed",
        ])
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def _csv_cell(value) -> str:
    """Keep user-entered text from becoming a spreadsheet formula on open."""
    text = str(value or "")
    if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text
