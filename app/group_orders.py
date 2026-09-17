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
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from . import costs, importer, matcher, menu, runs

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
MAX_BUDGET_CAP = 1000

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


class DeadlinePassed(RoomExpired):
    """The room still exists, but its organizer-set cutoff has elapsed."""

    status = 409
    code = "deadline_passed"


class OrderNotFound(GroupOrderError):
    status = 404
    code = "order_not_found"


class RoomFull(RoomNotOpen):
    code = "room_full"


class EmptyRoom(RoomNotOpen):
    code = "empty_room"


class BudgetExceeded(GroupOrderError):
    status = 409
    code = "budget_exceeded"


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


def _deadline(room: dict) -> dt.datetime:
    """Read the explicit deadline, falling back for rooms created before v3."""
    return _parse_timestamp(room.get("deadline_at") or room["expires_at"])


def _clean_deadline(deadline_at, expires_in_hours, current: dt.datetime) -> dt.datetime:
    if deadline_at not in (None, ""):
        if not isinstance(deadline_at, str):
            raise GroupOrderError("deadline_at must be an ISO 8601 timestamp")
        try:
            deadline = dt.datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise GroupOrderError("deadline_at must be an ISO 8601 timestamp") from exc
        if deadline.tzinfo is None:
            raise GroupOrderError("deadline_at must include a time zone")
        deadline = deadline.astimezone(dt.timezone.utc)
    else:
        if expires_in_hours is None:
            raise GroupOrderError("deadline_at is required")
        try:
            ttl = float(expires_in_hours)
        except (TypeError, ValueError) as exc:
            raise GroupOrderError("expires_in_hours must be a number") from exc
        if not math.isfinite(ttl) or ttl <= 0 or ttl > MAX_TTL_HOURS:
            raise GroupOrderError(
                f"expires_in_hours must be greater than 0 and at most {MAX_TTL_HOURS}")
        deadline = current + dt.timedelta(hours=ttl)

    if deadline <= current:
        raise GroupOrderError("deadline must be in the future")
    if deadline > current + dt.timedelta(hours=MAX_TTL_HOURS):
        raise GroupOrderError(f"deadline must be within {MAX_TTL_HOURS // 24} days")
    return deadline


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


def _clean_budget_cap(value) -> float | None:
    """Return an optional positive, cent-rounded per-person limit."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise GroupOrderError("budget cap must be a dollar amount")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise GroupOrderError("budget cap must be a dollar amount") from exc
    if not amount.is_finite() or amount <= 0 or amount > MAX_BUDGET_CAP:
        raise GroupOrderError(
            f"budget cap must be greater than $0 and at most ${MAX_BUDGET_CAP:,.2f}")
    try:
        amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise GroupOrderError("budget cap must be a dollar amount") from exc
    if amount <= 0:
        raise GroupOrderError("budget cap must be at least $0.01")
    return float(amount)


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
    if room.get("status") == "locked":
        return "locked"
    if now >= _deadline(room):
        return "locked"
    return "open"


def _public_order(order: dict) -> dict:
    return {key: value for key, value in order.items() if not key.endswith("_hash")}


def _rows_payload(orders: list[dict]) -> list[dict]:
    fields = ("person",) + ORDER_FIELDS
    return [
        {field: (", ".join(order.get(field) or []) if field == "toppings"
                 else order.get(field, ""))
         for field in fields}
        for order in orders
    ]


def _price_orders(orders: list[dict], restaurant_id: str) -> tuple[list[dict], dict]:
    """Price room lines from its pinned captured menu, never client input."""
    public = [_public_order(order) for order in orders]
    if not orders:
        return public, costs.breakdown([])
    try:
        imported = importer.import_json(
            json.dumps({"rows": _rows_payload(orders)}), restaurant_id=restaurant_id)
        priced = matcher.match(imported.as_dict())
        for order, row in zip(public, priced.get("rows") or []):
            found = row.get("match") or {}
            order["estimated_total"] = (
                found.get("total") if found.get("status") == matcher.READY else None)
            order["price_status"] = found.get("status") or matcher.SKIPPED
        return public, priced.get("costs") or costs.from_rows(priced.get("rows") or [])
    except Exception:
        # Room reading and moderation must remain available if a captured menu
        # is temporarily unreadable. The UI names any such lines as unpriced.
        fallback = [{"person": order.get("person"), "quantity": order.get("quantity"),
                     "amount": None} for order in orders]
        return public, costs.breakdown(fallback)


def _priced_person_totals(orders: list[dict], restaurant_id: str
                          ) -> tuple[dict[str, int], dict[str, str]]:
    """Return canonical captured-menu totals by case-insensitive person name."""
    priced, _cost_summary = _price_orders(orders, restaurant_id)
    totals: dict[str, int] = {}
    names: dict[str, str] = {}
    for order in priced:
        cents = costs._cents(order.get("estimated_total"))
        if cents is None:
            # An unrecognized or temporarily unpriceable line cannot safely be
            # compared to a dollar cap. It remains visible but is exempt.
            continue
        name = " ".join(str(order.get("person") or "").split())
        key = name.casefold()
        names.setdefault(key, name)
        totals[key] = totals.get(key, 0) + cents
    return totals, names


def _enforce_budget(room: dict, candidate_orders: list[dict], *,
                    previous_orders: list[dict] | None = None) -> None:
    """Reject new spend above the room's combined per-person hard cap.

    Existing lines are grandfathered when a cap is lowered. Participant edits
    may keep or reduce that spend, but cannot make any over-cap person's priced
    total grow. Organizer moderation deliberately bypasses this check.
    """
    cap = room.get("budget_cap")
    if cap is None:
        return
    restaurant_id = room.get("restaurant_id") or menu.TARGET_STORE
    candidate, names = _priced_person_totals(candidate_orders, restaurant_id)
    previous = ({} if previous_orders is None else
                _priced_person_totals(previous_orders, restaurant_id)[0])
    cap_cents = costs._cents(cap)
    if cap_cents is None:
        return
    for key, total in candidate.items():
        if total <= cap_cents:
            continue
        if previous_orders is not None and total <= previous.get(key, 0):
            continue
        person = names.get(key) or "This person"
        raise BudgetExceeded(
            f"{person}'s drinks would total ${total / 100:.2f}, over the "
            f"${cap_cents / 100:.2f} per-person limit")


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
    status = _effective_status(room, current)
    deadline_at = _timestamp(_deadline(room))
    deadline_passed = current >= _deadline(room)
    lock_reason = None
    if status == "locked":
        lock_reason = "deadline" if deadline_passed else "manual"
    restaurant_id = room.get("restaurant_id") or menu.TARGET_STORE
    orders, cost_summary = _price_orders(room.get("orders") or [], restaurant_id)
    store = menu.store_summary(restaurant_id)
    return {
        "id": room["id"],
        "title": room["title"],
        "organizer_name": room["organizer_name"],
        "restaurant_id": restaurant_id,
        "store_name": room.get("store_name") or ((store or {}).get("name")),
        "budget_cap": room.get("budget_cap"),
        "status": status,
        "accepting_orders": status == "open",
        "lock_reason": lock_reason,
        "deadline_passed": deadline_passed,
        "deadline_at": deadline_at,
        "server_now": _timestamp(current),
        "created_at": room["created_at"],
        "updated_at": room["updated_at"],
        # Retained as a compatibility alias for rooms and clients from Task 8.
        "expires_at": room["expires_at"],
        "locked_at": (deadline_at if lock_reason == "deadline" else room.get("locked_at")),
        "closed_at": room.get("closed_at"),
        "orders": orders,
        "summary": _summary(orders),
        "costs": cost_summary,
    }


def create(*, title: str = "", organizer_name: str = "",
           restaurant_id: str | None = None,
           budget_cap=None,
           deadline_at: str | None = None,
           expires_in_hours: float = DEFAULT_TTL_HOURS,
           now: dt.datetime | None = None) -> tuple[dict, str]:
    """Create a room and return ``(public_room, organizer_token)``."""
    current = _as_utc(now)
    organizer_name = _clean_string(organizer_name, "organizer_name")
    title = _clean_string(title, "title")
    if not title:
        title = f"{organizer_name}'s boba order" if organizer_name else "Boba group order"
    restaurant_id = restaurant_id or menu.TARGET_STORE
    store = menu.store_summary(restaurant_id)
    if store is None:
        raise GroupOrderError("choose an available Kung Fu Tea store")
    budget_cap = _clean_budget_cap(budget_cap)
    deadline = _clean_deadline(deadline_at, expires_in_hours, current)

    room_id = secrets.token_urlsafe(18)
    organizer_token = secrets.token_urlsafe(32)
    created_at = _timestamp(current)
    room = {
        "version": 4,
        "id": room_id,
        "title": title,
        "organizer_name": organizer_name,
        "restaurant_id": restaurant_id,
        "store_name": store["name"],
        "budget_cap": budget_cap,
        "budget_updated_at": created_at,
        "status": "open",
        "created_at": created_at,
        "updated_at": created_at,
        "deadline_at": _timestamp(deadline),
        # Compatibility alias. New code should use deadline_at.
        "expires_at": _timestamp(deadline),
        "deadline_updated_at": created_at,
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
        finalized_run = runs.load(run_id) if run_id else None
        result["finalized_at"] = room.get("finalized_at")
        result["preview_url"] = (
            f"/preview/{run_id}" if finalized_run is not None else None)
        cart = (finalized_run or {}).get("cart") or {}
        if cart.get("review_ready"):
            result["costs"] = costs.from_cart(cart)
            for line in cart.get("added") or []:
                index = int(line.get("row_number") or 0) - 2
                if 0 <= index < len(result["orders"]):
                    result["orders"][index]["actual_total"] = (
                        line.get("actual_total") if line.get("actual_total") is not None
                        else line.get("estimated_total"))
        return result


def _require_open(room: dict, now: dt.datetime) -> None:
    status = _effective_status(room, now)
    if status == "closed":
        raise RoomNotOpen("this group order is closed")
    if now >= _deadline(room):
        raise DeadlinePassed("the order deadline has passed; ask the organizer to extend it")
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
        _enforce_budget(room, [*(room.get("orders") or []), order])
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
        organizer_can_manage = _matches_secret(
            organizer_token, room.get("organizer_token_hash"))
        if not organizer_can_manage:
            existing_orders = room.get("orders") or []
            candidate_orders = [
                ({**saved, **values} if saved is order else saved)
                for saved in existing_orders
            ]
            _enforce_budget(
                room, candidate_orders, previous_orders=existing_orders)
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
        if status == "closed":
            raise RoomNotOpen("this group order is closed")
        if current >= _deadline(room) and not organizer_can_manage:
            raise DeadlinePassed("the order deadline has passed; ask the organizer to extend it")
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
        existing = room.get("finalized_run_id")
        if existing and runs.load(existing) is not None:
            return get_for_organizer(room_id, organizer_token, now=current), existing

        orders = room.get("orders") or []
        if not orders:
            raise EmptyRoom("add at least one drink before finalizing this group order")

        rows_payload = _rows_payload(orders)
        restaurant_id = room.get("restaurant_id") or menu.TARGET_STORE
        result = importer.import_json(
            json.dumps({"rows": rows_payload}), restaurant_id=restaurant_id)
        finalized_at = _timestamp(current)
        result.source = {
            "kind": "group_order",
            "session_id": room_id,
            "title": room["title"],
            "restaurant_id": restaurant_id,
            "store": room.get("store_name") or (menu.store_summary(restaurant_id) or {}).get("name"),
            "budget_cap": room.get("budget_cap"),
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
        if room.get("status") == "closed" and status != "closed":
            raise RoomNotOpen("a closed group order cannot be reopened")
        if status == "open" and current >= _deadline(room):
            raise DeadlinePassed("extend the deadline before reopening this group order")
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


def update_deadline(room_id: str, deadline_at, organizer_token: str | None, *,
                    now: dt.datetime | None = None) -> dict:
    """Move a room's cutoff. Extending an automatic lock reopens the room.

    A manual pause is preserved: changing its deadline does not accidentally
    admit participants while the organizer is reviewing submissions.
    """
    current = _as_utc(now)
    with _room_lock(room_id):
        room = _read(room_id)
        if not _matches_secret(organizer_token, room.get("organizer_token_hash")):
            raise Forbidden("the organizer token is missing or invalid")
        if room.get("status") == "closed":
            raise RoomNotOpen("a closed group order cannot change its deadline")
        deadline = _clean_deadline(deadline_at, None, current)
        timestamp = _timestamp(deadline)
        room["deadline_at"] = timestamp
        room["expires_at"] = timestamp
        room["deadline_updated_at"] = _timestamp(current)
        room["updated_at"] = _timestamp(current)
        _write(room)
        return public_room(room, now=current)


def set_budget_cap(room_id: str, budget_cap, organizer_token: str | None, *,
                   now: dt.datetime | None = None) -> dict:
    """Set or clear the combined per-person hard cap for future changes.

    Lowering the cap never deletes submitted drinks. Those lines stay in the
    room and can be reduced or edited without increasing their priced total.
    """
    current = _as_utc(now)
    with _room_lock(room_id):
        room = _read(room_id)
        if not _matches_secret(organizer_token, room.get("organizer_token_hash")):
            raise Forbidden("the organizer token is missing or invalid")
        if room.get("status") == "closed":
            raise RoomNotOpen("a closed group order cannot change its budget cap")
        room["budget_cap"] = _clean_budget_cap(budget_cap)
        room["budget_updated_at"] = _timestamp(current)
        room["updated_at"] = _timestamp(current)
        _write(room)
        return public_room(room, now=current)


def prune(*, now: dt.datetime | None = None, keep: int = KEEP_SESSIONS) -> None:
    """Discard old rooms and cap the closed/deadline-passed archive by age."""
    current = _as_utc(now)
    cutoff = current - dt.timedelta(days=RETENTION_DAYS)
    files = sorted(_directory().glob("*.json"),
                   key=lambda path: path.stat().st_mtime, reverse=True)
    archived = 0
    for path in files:
        remove = False
        try:
            room = json.loads(path.read_text(encoding="utf-8"))
            expires_at = _deadline(room)
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
