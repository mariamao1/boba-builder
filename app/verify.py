"""Cart integrity verification (Task 24).

Don't trust the build's own report of success — read back what's actually in
the cart and compare it against what the app intended to order.

HOW THE TRUE STATE IS OBTAINED
------------------------------
Task 1 established a clone-on-open handoff: our build creates an anonymous
*source* order, and opening the handoff link makes Kung Fu Tea server-side
copy its line items into a new order owned by the organizer's browser. There
is no token in the link and we never see the organizer's session, so their
clone cannot be read back by us. The true state we CAN check is the source
order itself, via ``GET api/v1/orders/{source_order_id}`` immediately after
the build, inside the same call that holds the anonymous access token.

That is sufficient because the clone is faithful where it counts: verified
live (Task 1, re-checked 2026-09-21), cloning preserves the menu item,
options, quantities, and prices exactly, and drops only per-item ``for`` /
``notes`` — by design, and covered by our own manifest instead. Source
verification therefore catches everything that can go wrong on our side
(missing items, dropped/substituted modifiers, wrong quantities, extras,
price drift); the organizer's editable clone is where they confirm anything
that changed afterwards, which is why the preview tells them to compare
before paying rather than asserting the cart is correct.

HOW ENTRIES ARE MATCHED BACK
----------------------------
The site's representation does not align one-to-one with ours:

- Line ids are the server's (``cart_item_id`` from each add response), but a
  read-back is matched semantically, not by id: the server may split or merge
  lines, so quantities are aggregated per (drink, modifiers) signature.
- Quantities arrive as strings (``"2"``) on a read-back and integers on an
  add response; both are coerced.
- Options are keyed by (group name, option name, quantity) — the same
  name-addressing the cart writer uses — compared case-insensitively with
  whitespace collapsed. The combined ``"Group: Option"`` display name is only
  a fallback when the structured fields are absent.
- Identical drinks for different people are indistinguishable in the cart
  (Task 1 §4), so matching is intentionally person-blind: two matching rows
  for one actual double line verify cleanly, and a shortfall is reported
  against the row that could not be covered.
- ``for`` / ``notes`` are deliberately ignored: they persist on the source
  order but are dropped by the clone, so they can never describe what the
  organizer pays for. Names ride in our manifest.

POSTURE
-------
``status`` is ``matched``, ``mismatched``, or ``unverified``. Anything that
prevents a real comparison — no order, an unreadable read-back, items that
are not a list — yields ``unverified``, never ``matched``. Claiming the cart
is correct when it could not actually be confirmed is worse than saying so
plainly, so the preview renders ``unverified`` as an explicit warning, not
as a pass.
"""

from __future__ import annotations

import datetime as _dt

#: A price difference at or below this is rounding noise, not drift.
PRICE_TOLERANCE = 0.015


def _norm(text) -> str:
    return " ".join(str(text or "").split()).lower()


def _coerce_qty(value, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError, AttributeError):
        return default


def _coerce_money(value):
    try:
        return round(float(str(value).strip()), 2)
    except (TypeError, ValueError, AttributeError):
        return None


def _option_signature(group, name, quantity) -> tuple[str, str, int, str]:
    """(normalized group, normalized name, quantity, display name).

    Matching uses the first three; the display name keeps the store's own
    spelling for messages shown to the organizer.
    """
    display = " ".join(str(name or "").split())
    return (_norm(group), _norm(name), _coerce_qty(quantity, default=1), display)


def _actual_options(item: dict) -> list[tuple[str, str, int]]:
    """One (group, option, quantity) tuple per modifier on a read-back line."""
    found = []
    for option in item.get("options") or []:
        if not isinstance(option, dict):
            continue
        group = option.get("group_name")
        name = option.get("option_name")
        if (not group or not name) and option.get("name"):
            # Fall back to the combined "Group: Option" display name.
            combined = str(option["name"])
            if ":" in combined:
                group, _, name = combined.partition(":")
                name = name.rsplit(" x", 1)[0] if " x" in name else name
        if not name:
            continue
        found.append(_option_signature(group, name, option.get("quantity")))
    return found


def _expected_options(entry: dict) -> list[tuple[str, str, int]]:
    found = []
    for option in entry.get("options") or []:
        if not isinstance(option, dict):
            continue
        found.append(_option_signature(
            option.get("group"), option.get("name"), option.get("quantity")))
    return found


def _item_key(menu_id, name) -> str:
    if menu_id:
        return f"id:{_norm(menu_id)}"
    return f"name:{_norm(name)}"


def _sig_key(options) -> frozenset:
    """The matchable identity of a modifier set: normalized, order-free."""
    return frozenset((group, name, quantity) for group, name, quantity, _ in options)


def _describe(signatures) -> str:
    """Human-readable modifier list, e.g. ``Large .7 · Boba ×2 · Half S 50%``."""
    seen: dict[tuple[str, str, int], str] = {}
    for group, name, quantity, display in signatures:
        seen.setdefault((group, name, quantity), display)
    parts = []
    for (_group, _name, quantity), display in sorted(
            seen.items(), key=lambda item: item[1].lower()):
        parts.append(f"{display} ×{quantity}" if quantity > 1 else display)
    return " · ".join(parts)


class _PoolLine:
    __slots__ = ("key", "name", "options", "remaining", "line_id", "total")

    def __init__(self, key, name, options, remaining, line_id, total):
        self.key = key
        self.name = name
        self.options = options
        self.remaining = remaining
        self.line_id = line_id
        self.total = total


def verify(expected: list[dict], order, *, order_id=None,
           now: _dt.datetime | None = None) -> dict:
    """Compare intended lines against a read-back cart.

    ``expected`` is the build manifest: each entry carries ``row_number``,
    ``person``, ``drink``, ``quantity``, ``item_id``, ``options`` (list of
    ``{group, name, quantity}``), ``cart_item_id``, and ``actual_total`` /
    ``estimated_total``. ``order`` is the ``GET order`` payload (or whatever
    came back — ``None`` and ``{}`` included). Never raises on odd shapes;
    anything uncomparable is ``unverified``.
    """
    checked_at = (now or _dt.datetime.now(_dt.timezone.utc)).isoformat(
        timespec="seconds")
    base = {
        "source": "source_order",
        "order_id": order_id,
        "checked_at": checked_at,
        "expected_drinks": sum(_coerce_qty(e.get("quantity"), 1) for e in expected),
        "confirmed_drinks": 0,
        "mismatches": [],
    }

    if not isinstance(order, dict) or not isinstance(order.get("items"), list):
        return {
            **base,
            "status": "unverified",
            "note": ("The cart contents could not be read back, so nothing "
                     "here is confirmed. Compare the manifest with the Kung Fu "
                     "Tea cart before paying — do not assume it is right."),
            "unverified_reason": "the source order could not be read",
        }

    pool: list[_PoolLine] = []
    for item in order["items"]:
        if not isinstance(item, dict):
            continue
        remaining = _coerce_qty(item.get("quantity"), default=0)
        if remaining <= 0:
            continue
        pool.append(_PoolLine(
            key=_item_key(item.get("menuitem"), item.get("name")),
            name=item.get("name") or "Unknown drink",
            options=_actual_options(item),
            remaining=remaining,
            line_id=item.get("id"),
            total=_coerce_money(item.get("total_price")),
        ))

    mismatches: list[dict] = []
    confirmed = 0
    # (entry, line, quantity) triples consumed exactly as ordered; used for
    # the per-line price check below.
    exact_consumption: list[tuple[dict, _PoolLine, int]] = []

    def _take_exact(entry, line, amount) -> None:
        nonlocal confirmed
        line.remaining -= amount
        confirmed += amount
        exact_consumption.append((entry, line, amount))

    def _take_wrong(line, amount, by_sig) -> None:
        nonlocal confirmed
        line.remaining -= amount
        confirmed += amount
        key = _sig_key(line.options)
        count, sample = by_sig.get(key, (0, line.options))
        by_sig[key] = (count + amount, sample)

    def _report_wrong(entry, wanted, by_sig) -> None:
        for _key, (taken, sample) in by_sig.items():
            mismatches.append(_mismatch(
                "modifiers", entry,
                expected=_describe(wanted) or "no modifiers",
                actual=_describe(sample) or "no modifiers",
                quantity=taken,
                detail=(f"ordered {_describe(wanted) or 'no modifiers'} "
                        f"but the cart has {_describe(sample) or 'no modifiers'} "
                        f"for {taken} of them")))

    for entry in expected:
        ordered = _coerce_qty(entry.get("quantity"), default=1)
        need = ordered
        if need <= 0:
            continue
        key = _item_key(entry.get("item_id"), entry.get("drink"))
        wanted = _expected_options(entry)
        wanted_sig = _sig_key(wanted)
        exact_taken = 0
        wrong_taken = 0
        by_sig: dict = {}

        # 0. Line-id continuity first: the server id from the add response is
        # the strongest signal for WHICH line this row became — but its
        # modifiers are still compared, not assumed.
        if entry.get("cart_item_id") is not None:
            for line in pool:
                if (line.line_id is None or line.remaining <= 0
                        or str(line.line_id) != str(entry["cart_item_id"])):
                    continue
                take = min(need, line.remaining)
                if _sig_key(line.options) == wanted_sig:
                    _take_exact(entry, line, take)
                    exact_taken += take
                else:
                    _take_wrong(line, take, by_sig)
                    wrong_taken += take
                need -= take
                break

        # 1. Consume stock that matches exactly (same drink, same modifiers).
        for line in pool:
            if need <= 0:
                break
            if line.key != key or line.remaining <= 0:
                continue
            if _sig_key(line.options) != wanted_sig:
                continue
            take = min(need, line.remaining)
            _take_exact(entry, line, take)
            need -= take
            exact_taken += take

        # 2. Same drink but configured differently — present yet wrong.
        if need > 0:
            for line in pool:
                if need <= 0:
                    break
                if line.key != key or line.remaining <= 0:
                    continue
                if _sig_key(line.options) == wanted_sig:
                    continue
                take = min(need, line.remaining)
                _take_wrong(line, take, by_sig)
                need -= take
                wrong_taken += take
        _report_wrong(entry, wanted, by_sig)

        # 3. Whatever is still uncovered is simply not in the cart. When part
        # of the row verified exactly, say so with numbers rather than
        # reporting only the absent remainder.
        if need > 0:
            if exact_taken > 0 and not wrong_taken:
                mismatches.append(_mismatch(
                    "short_quantity", entry,
                    expected=f"{ordered} ordered",
                    actual=f"cart has {exact_taken}",
                    quantity=need,
                    detail=(f"ordered {ordered} but the cart has "
                            f"only {exact_taken}")))
            else:
                absent = _mismatch(
                    "missing", entry,
                    expected=_describe(wanted) or "no modifiers",
                    actual="not in the cart",
                    quantity=need,
                    detail=(f"{need} {'is' if need == 1 else 'are'} not in the cart "
                            f"at all"))
                # Only a row with nothing at all in the cart leaves the
                # manifest. A partially covered row stays, with its shortfall
                # spelled out next to it.
                absent["absent"] = not exact_taken and not wrong_taken
                mismatches.append(absent)

    unexpected = [line for line in pool if line.remaining > 0]
    for line in unexpected:
        mismatches.append({
            "kind": "unexpected",
            "row_number": None,
            "person": None,
            "drink": line.name,
            "quantity": line.remaining,
            "expected": "nothing ordered",
            "actual": _describe(line.options) or "no modifiers",
            "detail": (f"the cart has an extra {line.remaining}× {line.name} "
                       f"that nobody ordered"),
        })

    # Per-line price check, only where one acknowledged line maps 1:1 onto one
    # read-back line (same server line id, same quantity): the totals the
    # build saw should be the totals the read-back shows.
    for entry, line, taken in exact_consumption:
        if taken != _coerce_qty(entry.get("quantity"), default=1):
            continue
        if entry.get("cart_item_id") is None or line.line_id is None:
            continue
        if str(entry["cart_item_id"]) != str(line.line_id):
            continue
        acked = _coerce_money(entry.get("actual_total"))
        seen = line.total
        if acked is None or seen is None:
            continue
        if abs(acked - seen) > PRICE_TOLERANCE:
            mismatches.append(_mismatch(
                "price", entry,
                expected=f"${acked:.2f}",
                actual=f"${seen:.2f}",
                quantity=taken,
                detail=(f"costs ${seen:.2f} in the cart but was "
                        f"${acked:.2f} when it was added")))

    # Totals check, only when every line is otherwise accounted for: then any
    # remaining drift is a real price change, not an explained absence.
    line_level = [m for m in mismatches if m["kind"] != "price"]
    if not line_level:
        subtotal = _coerce_money(order.get("subtotal"))
        intended = _coerce_money(sum(
            _coerce_money(e.get("actual_total")
                          if e.get("actual_total") is not None
                          else e.get("estimated_total")) or 0
            for e in expected))
        if subtotal is not None and intended is not None \
                and abs(subtotal - intended) > PRICE_TOLERANCE:
            mismatches.append({
                "kind": "totals",
                "row_number": None,
                "person": None,
                "drink": None,
                "quantity": None,
                "expected": f"${intended:.2f}",
                "actual": f"${subtotal:.2f}",
                "detail": (f"the cart subtotal is ${subtotal:.2f} but the "
                           f"placed drinks add up to ${intended:.2f}"),
            })

    if mismatches:
        kinds = sorted({m["kind"] for m in mismatches})
        return {
            **base,
            "status": "mismatched",
            "confirmed_drinks": confirmed,
            "mismatches": mismatches,
            "note": (f"Cart check found {len(mismatches)} "
                     f"difference{'s' if len(mismatches) != 1 else ''} "
                     f"({', '.join(kinds)}). Fix them below before paying — "
                     f"either edit the rows and rebuild, or fix the Kung Fu "
                     f"Tea cart directly."),
        }
    return {
        **base,
        "status": "matched",
        "confirmed_drinks": confirmed,
        "mismatches": [],
        "note": (f"Cart check passed: all {base['expected_drinks']} "
                 f"drink{'s' if base['expected_drinks'] != 1 else ''} are in "
                 f"the cart with the ordered modifiers and quantities, and "
                 f"the subtotal agrees."),
    }


def _mismatch(kind: str, entry: dict, *, expected, actual, quantity,
              detail: str) -> dict:
    person = entry.get("person") or "Unlabelled"
    drink = entry.get("drink") or "Unknown drink"
    return {
        "kind": kind,
        "row_number": entry.get("row_number"),
        "cart_item_id": entry.get("cart_item_id"),
        "person": person,
        "drink": drink,
        "quantity": quantity,
        "expected": expected,
        "actual": actual,
        "detail": (f"{person} — {quantity}× {drink}: {detail}. "
                   f"Edit the row and rebuild, or fix it in the Kung Fu Tea "
                   f"cart before paying."
                   if kind in ("missing", "short_quantity", "modifiers")
                   else detail),
    }
