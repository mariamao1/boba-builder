"""Person-by-person cost allocation for estimated and finished orders.

Drink prices belong to the person who ordered them.  Costs that apply to the
whole cart (tax, tip, service/delivery fees, discounts, and any other gap
between the line items and the final total) are split in proportion to each
person's drink subtotal.  All allocation happens in integer cents, with the
remaining cents assigned by largest remainder, so the shares always reconcile
exactly to the displayed group total.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def _cents(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not amount.is_finite():
        return None
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _amount(cents: int) -> float:
    return float(Decimal(cents) / 100)


def _allocate(total: int, weights: list[int]) -> list[int]:
    """Allocate signed ``total`` cents using largest-remainder rounding."""
    if not weights:
        return []
    sign = -1 if total < 0 else 1
    remaining = abs(total)
    positive = [max(0, weight) for weight in weights]
    denominator = sum(positive)
    if not denominator:
        positive = [1] * len(weights)
        denominator = len(weights)
    numerators = [remaining * weight for weight in positive]
    result = [value // denominator for value in numerators]
    leftover = remaining - sum(result)
    order = sorted(range(len(result)),
                   key=lambda index: (-(numerators[index] % denominator), index))
    for index in order[:leftover]:
        result[index] += 1
    return [sign * value for value in result]


def _person_key(value) -> str:
    return " ".join(str(value or "Unlabelled").split()).casefold()


def breakdown(lines: list[dict], *, totals: dict | None = None,
              source: str = "menu_estimate", estimated: bool = True) -> dict:
    """Return a reconciled split for priced lines.

    Each line supplies ``person``, ``quantity``, and ``amount``. ``totals`` may
    contain the cart's subtotal, tax, tip, fees, and total.  The final total is
    authoritative when present; otherwise the known components are added.
    """
    totals = totals or {}
    people: dict[str, dict] = {}
    unpriced_drinks = 0
    for line in lines:
        quantity = max(1, int(line.get("quantity") or 1))
        cents = _cents(line.get("amount"))
        if cents is None:
            unpriced_drinks += quantity
            continue
        name = " ".join(str(line.get("person") or "Unlabelled").split()) or "Unlabelled"
        key = _person_key(name)
        entry = people.setdefault(key, {
            "person": name, "drinks": 0, "lines": 0, "subtotal_cents": 0,
        })
        entry["drinks"] += quantity
        entry["lines"] += 1
        entry["subtotal_cents"] += cents

    entries = list(people.values())
    line_subtotal = sum(entry["subtotal_cents"] for entry in entries)
    tax = _cents(totals.get("tax")) or 0
    tip = _cents(totals.get("tip")) or 0
    fee_values = {
        str(name): cents
        for name, value in (totals.get("fees") or {}).items()
        if (cents := _cents(value)) is not None and cents != 0
    }
    known_shared = tax + tip + sum(fee_values.values())
    authoritative_total = _cents(totals.get("total"))
    group_total = (authoritative_total if authoritative_total is not None
                   else line_subtotal + known_shared)
    shared_total = group_total - line_subtotal
    adjustment = shared_total - known_shared
    shares = _allocate(shared_total, [entry["subtotal_cents"] for entry in entries])

    by_person = []
    for entry, shared in zip(entries, shares):
        by_person.append({
            "person": entry["person"],
            "drinks": entry["drinks"],
            "lines": entry["lines"],
            "subtotal": _amount(entry["subtotal_cents"]),
            "shared": _amount(shared),
            "total": _amount(entry["subtotal_cents"] + shared),
        })

    return {
        "source": source,
        "estimated": bool(estimated),
        "currency": totals.get("currency") or "USD",
        "allocation": "proportional",
        "subtotal": _amount(line_subtotal),
        "tax": _amount(tax),
        "tip": _amount(tip),
        "fees": {name: _amount(value) for name, value in fee_values.items()},
        "adjustment": _amount(adjustment),
        "shared_total": _amount(shared_total),
        "total": _amount(group_total),
        "priced_drinks": sum(entry["drinks"] for entry in entries),
        "unpriced_drinks": unpriced_drinks,
        "by_person": by_person,
    }


def from_rows(rows: list[dict]) -> dict:
    lines = []
    for row in rows or []:
        matched = row.get("match") or {}
        lines.append({
            "person": row.get("person"),
            "quantity": matched.get("quantity") or row.get("quantity") or 1,
            "amount": matched.get("total") if matched.get("status") == "ready" else None,
        })
    return breakdown(lines, source="menu_estimate", estimated=True)


def from_cart(cart: dict, *, tip=None, total_paid=None) -> dict:
    lines = [{
        "person": line.get("person"),
        "quantity": line.get("quantity") or 1,
        "amount": (line.get("actual_total") if line.get("actual_total") is not None
                   else line.get("estimated_total")),
    } for line in cart.get("added") or []]
    totals = dict(cart.get("totals") or {})
    if tip is not None:
        totals["tip"] = tip
    if total_paid is not None:
        totals["total"] = total_paid
    elif tip is not None and _cents(totals.get("total")) is not None:
        totals["total"] = _amount((_cents(totals["total"]) or 0) + (_cents(tip) or 0))
    return breakdown(lines, totals=totals, source="cart_total", estimated=False)
