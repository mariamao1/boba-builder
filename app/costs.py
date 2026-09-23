"""Person-by-person and payer-by-payer cost allocation.

Drink prices belong to the person who ordered them.  Costs that apply to the
whole cart (tax, tip, service/delivery fees, discounts, and any other gap
between the line items and the final total) are split in proportion to each
person's drink subtotal.  All allocation happens in integer cents, with the
remaining cents assigned by largest remainder, so the shares always reconcile
exactly to the displayed group total.

Several people may fund the same cart even though the store still receives one
payment.  A payer split is a second, optional layer over the person split: it
either rolls each person's complete share into an explicitly assigned payer or
divides the complete bill evenly.  Keeping that layer separate preserves the
original "who ordered what" accounting and lets final receipt numbers flow
through both views without two competing allocation policies.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


MAX_PAYERS = 20
MAX_PAYER_NAME_LENGTH = 80


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


def _clean_payer_name(value, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError("payer names must be text")
    name = " ".join(value.split())
    if not name and not allow_empty:
        raise ValueError("payer names cannot be empty")
    if len(name) > MAX_PAYER_NAME_LENGTH:
        raise ValueError(
            f"payer names must be {MAX_PAYER_NAME_LENGTH} characters or fewer")
    return name


def normalize_payer_config(value) -> dict:
    """Validate and canonicalize an optional multiple-payer configuration.

    A bare list is accepted as shorthand for ``{"payers": [...]}``. Person
    keys in ``assignments`` are normalized for the same case-insensitive
    matching used by the participant breakdown. Blank assignments are omitted
    so an organizer can leave a row on the documented first-payer default.
    """
    if value is None:
        value = {}
    if isinstance(value, list):
        value = {"payers": value}
    if not isinstance(value, dict):
        raise ValueError("payers must be an object or list")

    raw_payers = value.get("payers") or []
    if not isinstance(raw_payers, list):
        raise ValueError("payers must be a list")
    if len(raw_payers) > MAX_PAYERS:
        raise ValueError(f"an order cannot have more than {MAX_PAYERS} payers")

    payer_names: list[str] = []
    canonical: dict[str, str] = {}
    for raw_name in raw_payers:
        name = _clean_payer_name(raw_name)
        key = name.casefold()
        if key in canonical:
            raise ValueError("payer names must be unique")
        canonical[key] = name
        payer_names.append(name)

    mode = value.get("mode") or "assigned"
    if mode not in ("assigned", "even"):
        raise ValueError("payer split mode must be assigned or even")

    raw_assignments = value.get("assignments") or {}
    if not isinstance(raw_assignments, dict):
        raise ValueError("payer assignments must be an object")
    assignments: dict[str, str] = {}
    for raw_person, raw_payer in raw_assignments.items():
        person = _clean_payer_name(raw_person, allow_empty=True)
        payer = _clean_payer_name(raw_payer, allow_empty=True)
        if not person or not payer:
            continue
        matched = canonical.get(payer.casefold())
        if matched is None:
            raise ValueError(f'assigned payer "{payer}" is not in the payer list')
        assignments[person.casefold()] = matched

    raw_paid_by = value.get("paid_by")
    paid_by = None
    if raw_paid_by not in (None, ""):
        cleaned = _clean_payer_name(raw_paid_by)
        paid_by = canonical.get(cleaned.casefold())
        if paid_by is None:
            raise ValueError(f'checkout payer "{cleaned}" is not in the payer list')
    elif payer_names:
        paid_by = payer_names[0]

    return {
        "payers": payer_names,
        "mode": mode,
        "assignments": assignments,
        "paid_by": paid_by,
    }


def payer_split(cost_summary: dict, payer_config) -> dict | None:
    """Build payer shares and reimbursement directions over a person split."""
    config = normalize_payer_config(payer_config)
    payer_names = config["payers"]
    if not payer_names:
        return None

    payer_rows = [{
        "payer": name,
        "people": [],
        "subtotal_cents": 0,
        "shared_cents": 0,
    } for name in payer_names]
    payer_indexes = {name.casefold(): index for index, name in enumerate(payer_names)}
    group_total_cents = _cents(cost_summary.get("total")) or 0

    if config["mode"] == "even":
        total_shares = _allocate(group_total_cents, [1] * len(payer_rows))
        subtotal_shares = _allocate(
            _cents(cost_summary.get("subtotal")) or 0, [1] * len(payer_rows))
        for index, row in enumerate(payer_rows):
            row["subtotal_cents"] = subtotal_shares[index]
            row["shared_cents"] = total_shares[index] - subtotal_shares[index]
    else:
        default_payer = payer_names[0]
        for person in cost_summary.get("by_person") or []:
            person_name = " ".join(str(person.get("person") or "Unlabelled").split())
            payer = config["assignments"].get(
                _person_key(person_name), default_payer)
            row = payer_rows[payer_indexes[payer.casefold()]]
            row["people"].append(person_name)
            row["subtotal_cents"] += _cents(person.get("subtotal")) or 0
            row["shared_cents"] += _cents(person.get("shared")) or 0
        # Normal breakdowns already reconcile. This fallback keeps the payer
        # view exact if it is attached to a sparse legacy summary.
        assigned_total = sum(
            row["subtotal_cents"] + row["shared_cents"] for row in payer_rows)
        payer_rows[0]["shared_cents"] += group_total_cents - assigned_total

    public_rows = []
    totals_by_payer: dict[str, int] = {}
    for row in payer_rows:
        total_cents = row["subtotal_cents"] + row["shared_cents"]
        totals_by_payer[row["payer"].casefold()] = total_cents
        public_rows.append({
            "payer": row["payer"],
            "people": row["people"],
            "subtotal": _amount(row["subtotal_cents"]),
            "shared": _amount(row["shared_cents"]),
            "total": _amount(total_cents),
        })

    paid_by = config["paid_by"] or payer_names[0]
    settlement = []
    for row in public_rows:
        cents = totals_by_payer[row["payer"].casefold()]
        if row["payer"] != paid_by and cents > 0:
            settlement.append({
                "from": row["payer"],
                "to": paid_by,
                "amount": _amount(cents),
            })

    return {
        "mode": config["mode"],
        "paid_by": paid_by,
        "payers": public_rows,
        "settlement": settlement,
        "total": _amount(group_total_cents),
    }


def attach_payers(cost_summary: dict, payer_config) -> dict:
    """Attach an optional payer view without replacing ``by_person``."""
    split = payer_split(cost_summary, payer_config)
    if split is None:
        cost_summary.pop("payer_split", None)
    else:
        cost_summary["payer_split"] = split
    return cost_summary


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


def from_rows(rows: list[dict], *, payers=None) -> dict:
    lines = []
    for row in rows or []:
        matched = row.get("match") or {}
        lines.append({
            "person": row.get("person"),
            "quantity": matched.get("quantity") or row.get("quantity") or 1,
            "amount": matched.get("total") if matched.get("status") == "ready" else None,
        })
    return attach_payers(
        breakdown(lines, source="menu_estimate", estimated=True), payers)


def from_cart(cart: dict, *, tip=None, total_paid=None, payers=None) -> dict:
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
    return attach_payers(
        breakdown(lines, totals=totals, source="cart_total", estimated=False), payers)
