"""Persistent, anonymous group-order rooms.

Rooms use an unguessable public id instead of an account.  The public id grants
read/add access while the room is open; separate one-time secrets let the
organizer change the room state and let a contributor edit their own order.

One JSON file per room keeps deployment stdlib-only and survives server
restarts.  Per-room locks make read/modify/write operations safe under the
threaded web server.  This is deliberately a small single-process store, not a
replacement for a database when Boba Builder is deployed behind many workers.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import math
import os
import secrets
import threading
from pathlib import Path

from . import importer, runs

SESSION_DIR = Path(os.environ.get(
    "BOBA_GROUP_ORDER_DIR",
    Path(__file__).resolve().parent.parent / ".group-orders",
))

DEFAULT_TTL_HOURS = 24
MAX_TTL_HOURS = 7 * 24
RETENTION_DAYS = 7
KEEP_SESSIONS = 200
MAX_ORDERS = 200
MAX_QUANTITY = 20

ORDER_FIELDS = (
    "drink", "size", "sugar", "ice", "toppings", "milk", "temperature",
    "quantity", "notes",
)
_STRING_LIMITS = {
    "title": 120,
    "organizer_name": 80,
    "person": 80,
    "drink": 160,
    "size": 80,
    "sugar": 80,
    "ice": 80,
    "milk": 80,
    "temperature": 80,
    "notes": 500,
    "topping": 100,
}

_locks_guard = threading.Lock()
_room_locks: dict[str, threading.RLock] = {}


class GroupOrderError(Exception):
    """Base error carrying the HTTP status and stable API error code."""

    status = 400
    code = "invalid_request"


class RoomNotFound(GroupOrderError):
    status = 404
    code = "room_not_found"


class Forbidden(GroupOrderError):
    status = 403
    code = "forbidden"


class RoomNotOpen(GroupOrderError):
    status = 409
    code = "room_not_open"


class RoomExpired(RoomNotOpen):
    status = 410
    code = "room_expired"


class OrderNotFound(GroupOrderError):
    status = 404
    code = "order_not_found"


class RoomFull(RoomNotOpen):
    code = "room_full"


class EmptyRoom(RoomNotOpen):
    code = "empty_room"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _as_utc(value: dt.datetime | None) -> dt.datetime:
    value = value or _utcnow()
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _timestamp(value: dt.datetime) -> str:
    return _as_utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _matches_secret(value: str | None, expected_hash: str | None) -> bool:
    if not isinstance(value, str) or not value or not expected_hash:
        return False
    return hmac.compare_digest(_secret_hash(value), expected_hash)


def _room_lock(room_id: str) -> threading.RLock:
    with _locks_guard:
        return _room_locks.setdefault(room_id, threading.RLock())


def _directory() -> Path:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return SESSION_DIR


def _valid_id(value: str) -> bool:
    return (20 <= len(value) <= 64
            and all(char.isascii() and (char.isalnum() or char in "-_") for char in value))


def path_for(room_id: str) -> Path:
    if not _valid_id(room_id):
        raise ValueError("bad group-order id")
    return _directory() / f"{room_id}.json"


def _read(room_id: str) -> dict:
    try:
        path = path_for(room_id)
    except ValueError as exc:
        raise RoomNotFound("that group order does not exist") from exc
    if not path.exists():
        raise RoomNotFound("that group order does not exist")
    try:
        room = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RoomNotFound("that group order could not be read") from exc
    if not isinstance(room, dict) or room.get("id") != room_id:
        raise RoomNotFound("that group order could not be read")
    return room


def _write(room: dict) -> None:
    path = path_for(room["id"])
    temporary = path.with_suffix(f".{secrets.token_hex(4)}.tmp")
    try:
        temporary.write_text(json.dumps(room, indent=1), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _clean_string(value, field: str, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise GroupOrderError(f"{field.replace('_', ' ')} must be text")
    value = " ".join(value.split()) if field != "notes" else value.strip()
    if required and not value:
        raise GroupOrderError(f"{field.replace('_', ' ')} is required")
    if len(value) > _STRING_LIMITS[field]:
        raise GroupOrderError(
            f"{field.replace('_', ' ')} is too long (maximum {_STRING_LIMITS[field]} characters)"
        )
    return value


def _clean_toppings(value) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        raise GroupOrderError("toppings must be a list")
    if len(value) > 20:
        raise GroupOrderError("an order cannot have more than 20 toppings")
    toppings = []
    for topping in value:
        cleaned = _clean_string(topping, "topping")
        if cleaned:
            toppings.append(cleaned)
    return toppings


def _clean_quantity(value) -> int:
    if value in (None, ""):
        return 1
    if isinstance(value, bool):
        raise GroupOrderError("quantity must be a whole number")
    try:
        quantity = int(value)
    except (TypeError, ValueError) as exc:
        raise GroupOrderError("quantity must be a whole number") from exc
    if isinstance(value, float) and value != quantity:
        raise GroupOrderError("quantity must be a whole number")
    if quantity < 1 or quantity > MAX_QUANTITY:
        raise GroupOrderError(f"quantity must be between 1 and {MAX_QUANTITY}")
    return quantity


def _order_values(payload: dict, current: dict | None = None) -> dict:
    if not isinstance(payload, dict):
        raise GroupOrderError("order must be a JSON object")
    nested = payload.get("order")
    if nested is not None:
        if not isinstance(nested, dict):
            raise GroupOrderError("order must be a JSON object")
        payload = {**nested, **({"person": payload["person"]} if "person" in payload else {})}

    source = current or {}
    person = _clean_string(payload.get("person", source.get("person")),
                           "person", required=True)
    drink = _clean_string(payload.get("drink", source.get("drink")),
                          "drink", required=True)
    result = {"person": person, "drink": drink}
    for field in ("size", "sugar", "ice", "milk", "temperature", "notes"):
        result[field] = _clean_string(payload.get(field, source.get(field, "")), field)
    result["toppings"] = _clean_toppings(payload.get("toppings", source.get("toppings", [])))
    result["quantity"] = _clean_quantity(payload.get("quantity", source.get("quantity", 1)))
    return result


def _effective_status(room: dict, now: dt.datetime) -> str:
    if room.get("status") == "closed":
        return "closed"
    if now >= _parse_timestamp(room["expires_at"]):
        return "expired"
    return room.get("status") or "open"


def _public_order(order: dict) -> dict:
    return {key: value for key, value in order.items() if not key.endswith("_hash")}


def _summary(orders: list[dict]) -> dict:
    people: dict[str, dict] = {}
    for order in orders:
        key = order["person"].casefold()
        entry = people.setdefault(key, {
            "person": order["person"], "orders": 0, "drinks": 0,
        })
        entry["orders"] += 1
        entry["drinks"] += order["quantity"]
    return {
        "orders": len(orders),
        "drinks": sum(order["quantity"] for order in orders),
        "people": len(people),
        "by_person": list(people.values()),
    }


def public_room(room: dict, *, now: dt.datetime | None = None) -> dict:
    current = _as_utc(now)
    orders = [_public_order(order) for order in room.get("orders") or []]
    status = _effective_status(room, current)
    return {
        "id": room["id"],
        "title": room["title"],
        "organizer_name": room["organizer_name"],
        "status": status,
        "accepting_orders": status == "open",
        "created_at": room["created_at"],
        "updated_at": room["updated_at"],
        "expires_at": room["expires_at"],
        "locked_at": room.get("locked_at"),
        "closed_at": room.get("closed_at"),
        "orders": orders,
        "summary": _summary(orders),
    }


def create(*, title: str = "", organizer_name: str = "",
           expires_in_hours: float = DEFAULT_TTL_HOURS,
           now: dt.datetime | None = None) -> tuple[dict, str]:
    """Create a room and return ``(public_room, organizer_token)``."""
    current = _as_utc(now)
    organizer_name = _clean_string(organizer_name, "organizer_name")
    title = _clean_string(title, "title")
    if not title:
        title = f"{organizer_name}'s boba order" if organizer_name else "Boba group order"
    try:
        ttl = float(expires_in_hours)
    except (TypeError, ValueError) as exc:
        raise GroupOrderError("expires_in_hours must be a number") from exc
    if not math.isfinite(ttl) or ttl <= 0 or ttl > MAX_TTL_HOURS:
        raise GroupOrderError(f"expires_in_hours must be greater than 0 and at most {MAX_TTL_HOURS}")

    room_id = secrets.token_urlsafe(18)
    organizer_token = secrets.token_urlsafe(32)
    created_at = _timestamp(current)
    room = {
        "version": 1,
        "id": room_id,
        "title": title,
        "organizer_name": organizer_name,
        "status": "open",
        "created_at": created_at,
        "updated_at": created_at,
        "expires_at": _timestamp(current + dt.timedelta(hours=ttl)),
        "locked_at": None,
        "closed_at": None,
        "organizer_token_hash": _secret_hash(organizer_token),
        "orders": [],
    }
    with _room_lock(room_id):
        _write(room)
    prune(now=current)
    return public_room(room, now=current), organizer_token


def get(room_id: str, *, now: dt.datetime | None = None) -> dict:
    with _room_lock(room_id):
        return public_room(_read(room_id), now=now)


def get_for_organizer(room_id: str, organizer_token: str | None, *,
                      now: dt.datetime | None = None) -> dict:
    """Return organizer-only room state after checking its bearer token."""
    with _room_lock(room_id):
        room = _read(room_id)
        if not _matches_secret(organizer_token, room.get("organizer_token_hash")):
            raise Forbidden("the organizer token is missing or invalid")
        result = public_room(room, now=now)
        run_id = room.get("finalized_run_id")
        result["finalized_at"] = room.get("finalized_at")
        result["preview_url"] = (
            f"/preview/{run_id}" if run_id and runs.load(run_id) is not None else None)
        return result


def _require_open(room: dict, now: dt.datetime) -> None:
    status = _effective_status(room, now)
    if status == "expired":
        raise RoomExpired("this group order has expired")
    if status != "open":
        raise RoomNotOpen(f"this group order is {status}")


def add_order(room_id: str, payload: dict, *, now: dt.datetime | None = None
              ) -> tuple[dict, str, dict]:
    """Add one drink order; return ``(order, edit_token, public_room)``."""
    current = _as_utc(now)
    values = _order_values(payload)
    with _room_lock(room_id):
        room = _read(room_id)
        _require_open(room, current)
        if len(room.get("orders") or []) >= MAX_ORDERS:
            raise RoomFull(f"this group order has reached its {MAX_ORDERS}-order limit")
        edit_token = secrets.token_urlsafe(24)
        order = {
            "id": secrets.token_urlsafe(15),
            "participant_id": secrets.token_urlsafe(9),
            **values,
            "created_at": _timestamp(current),
            "updated_at": _timestamp(current),
            "edit_token_hash": _secret_hash(edit_token),
        }
        room.setdefault("orders", []).append(order)
        room["updated_at"] = _timestamp(current)
        _write(room)
        return _public_order(order), edit_token, public_room(room, now=current)


def _find_order(room: dict, order_id: str) -> dict:
    for order in room.get("orders") or []:
        if order.get("id") == order_id:
            return order
    raise OrderNotFound("that order does not exist in this group order")


def _can_manage_order(room: dict, order: dict, order_token: str | None,
                      organizer_token: str | None) -> bool:
    return (_matches_secret(order_token, order.get("edit_token_hash"))
            or _matches_secret(organizer_token, room.get("organizer_token_hash")))


def update_order(room_id: str, order_id: str, payload: dict, *,
                 order_token: str | None = None,
                 organizer_token: str | None = None,
                 now: dt.datetime | None = None) -> tuple[dict, dict]:
    current = _as_utc(now)
    with _room_lock(room_id):
        room = _read(room_id)
        _require_open(room, current)
        order = _find_order(room, order_id)
        if not _can_manage_order(room, order, order_token, organizer_token):
            raise Forbidden("the order edit token is missing or invalid")
        values = _order_values(payload, current=order)
        order.update(values)
        order["updated_at"] = _timestamp(current)
        room["updated_at"] = _timestamp(current)
        _write(room)
        return _public_order(order), public_room(room, now=current)


def delete_order(room_id: str, order_id: str, *, order_token: str | None = None,
                 organizer_token: str | None = None,
                 now: dt.datetime | None = None) -> dict:
    current = _as_utc(now)
    with _room_lock(room_id):
        room = _read(room_id)
        status = _effective_status(room, current)
        organizer_can_manage = _matches_secret(
            organizer_token, room.get("organizer_token_hash"))
        if status == "expired":
            raise RoomExpired("this group order has expired")
        if status == "closed":
            raise RoomNotOpen("this group order is closed")
        # Locking freezes participant changes while the organizer reviews the
        # room. The organizer can still remove junk or an exact duplicate.
        if status != "open" and not organizer_can_manage:
            raise RoomNotOpen(f"this group order is {status}")
        order = _find_order(room, order_id)
        if not (organizer_can_manage
                or _matches_secret(order_token, order.get("edit_token_hash"))):
            raise Forbidden("the order edit token is missing or invalid")
        room["orders"].remove(order)
        room["updated_at"] = _timestamp(current)
        _write(room)
        return public_room(room, now=current)


def finalize(room_id: str, organizer_token: str | None, *,
             now: dt.datetime | None = None) -> tuple[dict, str]:
    """Close a room and turn its current orders into a normal pipeline run.

    The room lock covers both snapshotting the lines and recording the run id,
    so additions cannot race finalization and concurrent retries converge on
    the same preview. A previously closed room may still be finalized, which
    preserves the Task 8 close endpoint as a useful manual control.
    """
    current = _as_utc(now)
    with _room_lock(room_id):
        room = _read(room_id)
        if not _matches_secret(organizer_token, room.get("organizer_token_hash")):
            raise Forbidden("the organizer token is missing or invalid")
        if _effective_status(room, current) == "expired":
            raise RoomExpired("this group order has expired")

        existing = room.get("finalized_run_id")
        if existing and runs.load(existing) is not None:
            return get_for_organizer(room_id, organizer_token, now=current), existing

        orders = room.get("orders") or []
        if not orders:
            raise EmptyRoom("add at least one drink before finalizing this group order")

        fields = ("person",) + ORDER_FIELDS
        rows_payload = [
            {field: (", ".join(order.get(field) or []) if field == "toppings"
                     else order.get(field, ""))
             for field in fields}
            for order in orders
        ]
        result = importer.import_json(json.dumps({"rows": rows_payload}))
        finalized_at = _timestamp(current)
        result.source = {
            "kind": "group_order",
            "session_id": room_id,
            "title": room["title"],
            "finalized_at": finalized_at,
        }
        run_id = runs.new_id()
        runs.save(result.as_dict(), run_id)

        room["status"] = "closed"
        room["updated_at"] = finalized_at
        room["closed_at"] = room.get("closed_at") or finalized_at
        room["finalized_at"] = finalized_at
        room["finalized_run_id"] = run_id
        _write(room)
        return get_for_organizer(room_id, organizer_token, now=current), run_id


def set_status(room_id: str, status: str, organizer_token: str | None, *,
               now: dt.datetime | None = None) -> dict:
    """Lock, reopen, or permanently close a room."""
    if status not in ("open", "locked", "closed"):
        raise GroupOrderError("status must be open, locked, or closed")
    current = _as_utc(now)
    with _room_lock(room_id):
        room = _read(room_id)
        if not _matches_secret(organizer_token, room.get("organizer_token_hash")):
            raise Forbidden("the organizer token is missing or invalid")
        effective = _effective_status(room, current)
        if effective == "expired":
            raise RoomExpired("this group order has expired")
        if room.get("status") == "closed" and status != "closed":
            raise RoomNotOpen("a closed group order cannot be reopened")
        room["status"] = status
        room["updated_at"] = _timestamp(current)
        if status == "locked":
            room["locked_at"] = _timestamp(current)
        elif status == "open":
            room["locked_at"] = None
        if status == "closed":
            room["closed_at"] = _timestamp(current)
        _write(room)
        return public_room(room, now=current)


def prune(*, now: dt.datetime | None = None, keep: int = KEEP_SESSIONS) -> None:
    """Discard long-expired rooms and cap the closed/expired archive by age."""
    current = _as_utc(now)
    cutoff = current - dt.timedelta(days=RETENTION_DAYS)
    files = sorted(_directory().glob("*.json"),
                   key=lambda path: path.stat().st_mtime, reverse=True)
    archived = 0
    for path in files:
        remove = False
        try:
            room = json.loads(path.read_text(encoding="utf-8"))
            expires_at = _parse_timestamp(room["expires_at"])
            remove = expires_at < cutoff
            if not remove and (expires_at <= current or room.get("status") == "closed"):
                archived += 1
                remove = archived > keep
        except (KeyError, ValueError, json.JSONDecodeError, OSError):
            remove = True
        if remove:
            try:
                path.unlink()
            except OSError:
                pass
