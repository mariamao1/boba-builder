"""One label per cup, so the right drink reaches the right person.

Once an order is finalized (a reviewable cart, or a saved snapshot of one),
the runner stands at a counter with a tray of visually similar cups. Each
placed cart line becomes one label per cup — a quantity of 2 is two identical
cups, so it gets two labels — carrying the person's name and the drink's full
spec. Two milk teas differing only in sugar level must read differently on
paper, so every modifier the cup was ordered with is on the label; anything
left at the store default is omitted because identical orders are identical
cups.

Labels are grouped by person (case-insensitive, alphabetically) with cart
order kept inside each group, and numbered overall so the runner can check
cups off. People with several cups get "cup i of n" on each label.
"""

from __future__ import annotations

import re

from . import menu as menu_module

UNLABELLED = "Unlabelled"
UNKNOWN_DRINK = "Unknown drink"

_SUGAR_RE = re.compile(r"(\d+)\s*%")

#: Manifest axes in the order they print on a label.
_AXIS_ORDER = ("size", "sugar", "ice", "toppings", "milk", "temperature")


def _display_size(name: str) -> str:
    """A size literal back to what is written on the cup: "Large .7" -> "Large"."""
    return menu_module.size_label(name or "")


def _display_sugar(name: str) -> str:
    """A sugar literal back to its percentage: "Half S 50%" -> "50%"."""
    found = _SUGAR_RE.search(name or "")
    return f"{found.group(1)}%" if found else (name or "").strip()


def _display_topping(name: str, quantity: int) -> str:
    text = (name or "").strip()
    return f"{text} ×{quantity}" if quantity > 1 else text


def _clean_person(value) -> str:
    return " ".join(str(value or "").split()) or UNLABELLED


def _clean_drink(value) -> str:
    return " ".join(str(value or "").split()) or UNKNOWN_DRINK


def _spec_parts(line: dict) -> dict:
    """The cup's modifiers by axis, in display (not cart-literal) form."""
    parts: dict[str, object] = {}
    for option in line.get("options") or []:
        axis = option.get("axis")
        if axis not in _AXIS_ORDER or axis == "toppings":
            continue
        name = str(option.get("name") or "").strip()
        if not name:
            continue
        if axis == "size":
            name = _display_size(name)
        elif axis == "sugar":
            name = _display_sugar(name)
        if name:
            parts[axis] = name
    toppings = [
        _display_topping(option.get("name"), int(option.get("quantity") or 1))
        for option in line.get("options") or []
        if option.get("axis") == "toppings" and str(option.get("name") or "").strip()
    ]
    if toppings:
        parts["toppings"] = toppings
    return parts


def spec_line(line: dict) -> str:
    """The full disambiguating spec for one placed line, for paper."""
    parts = _spec_parts(line)
    ordered = []
    for axis in _AXIS_ORDER:
        value = parts.get(axis)
        if not value:
            continue
        if axis == "sugar":
            ordered.append(f"{value} sugar")
        elif isinstance(value, list):
            ordered.append(", ".join(value))
        else:
            ordered.append(str(value))
    return " · ".join(ordered) or "Store defaults"


def build_labels(items: list[dict]) -> list[dict]:
    """Expand placed cart lines into one label per cup.

    ``items`` are manifest entries (``cart["added"]`` or a saved order's
    ``items``): each carries ``person``, ``drink``, ``quantity``,
    ``options``, and ``notes``. Failed and skipped lines never reach here,
    so every cup returned was actually ordered.
    """
    cups: list[dict] = []
    for line in items or []:
        try:
            quantity = int(line.get("quantity") or 1)
        except (TypeError, ValueError):
            quantity = 1
        quantity = max(1, quantity)
        cups.extend([line] * quantity)

    # Group by person so the runner hands one person all their cups at once.
    # The sort is stable, so cart order survives inside each group. Names that
    # differ only in case ("alice" vs "Alice") stay in one group under the
    # first spelling seen, the same way room summaries name each person.
    spellings: dict[str, str] = {}
    for line in cups:
        key = _clean_person(line.get("person")).casefold()
        spellings.setdefault(key, _clean_person(line.get("person")))
    cups.sort(key=lambda line: _clean_person(line.get("person")).casefold())

    totals: dict[str, int] = {}
    for line in cups:
        key = _clean_person(line.get("person")).casefold()
        totals[key] = totals.get(key, 0) + 1

    labels = []
    seen: dict[str, int] = {}
    for position, line in enumerate(cups, start=1):
        key = _clean_person(line.get("person")).casefold()
        person = spellings[key]
        seen[key] = seen.get(key, 0) + 1
        notes = " ".join(str(line.get("notes") or "").split())
        labels.append({
            "seq": position,
            "cups": len(cups),
            "person": person,
            "cup": seen[key],
            "person_cups": totals[key],
            "drink": _clean_drink(line.get("drink")),
            "spec": spec_line(line),
            "notes": notes,
            "row_number": line.get("row_number"),
        })
    return labels


def format_text(labels: list[dict], title: str | None = None) -> str:
    """Plain-text labels, one cup per line, for pasting into chat or printing."""
    heading = f"{title or 'Boba pickup'} ({len(labels)} cup{'s' if len(labels) != 1 else ''})"
    if not labels:
        return f"{heading}\nNo placed drinks — nothing to hand out."
    lines = [heading]
    current: str | None = None
    for label in labels:
        if label["person"] != current:
            current = label["person"]
            lines.append(f"{current}:")
        detail = f"  {label['cup']}/{label['person_cups']} {label['drink']} — {label['spec']}"
        if label["notes"]:
            detail += f" (note: {label['notes']})"
        lines.append(detail)
    return "\n".join(lines)
