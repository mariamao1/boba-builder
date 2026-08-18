"""The shape Task 2 hands to Tasks 3-4, and the forgiving mapping that gets there.

Task 2 does STRUCTURAL normalisation only: find the header row, work out which
column is which, split toppings, pull quantities out of "Taro Slush x2", flag
anything suspicious. It deliberately does NOT resolve values against the menu —
size/sugar/ice vocabularies are per item (Task 1, §5), so "Large" can only be
resolved once the drink is matched. That is Task 3's job, and it gets the
user's original text plus our notes about what we changed.
"""

from __future__ import annotations

import datetime as _dt
import difflib
import functools
import re
from collections import Counter
from dataclasses import dataclass, field

# --- the field contract -----------------------------------------------------
#
# Each entry: (field name, required?, human label, header synonyms).
# Synonyms are matched after normalising to lowercase alphanumerics, so
# "Sugar Level", "sugar_level" and "SUGAR LEVEL!" are all the same key.

FIELDS: list[tuple[str, bool, str, tuple[str, ...]]] = [
    ("person", True, "Name", (
        "name", "person", "who", "whos", "whoisitfor", "for", "orderedby",
        "ordername", "customer", "employee", "teammate", "firstname", "yourname",
        "namefirstlast", "guest",
    )),
    ("drink", True, "Drink", (
        "drink", "item", "order", "beverage", "tea", "drinkname", "whatdoyouwant",
        "drinkorder", "menuitem", "product", "whichdrink", "drinkchoice",
    )),
    ("size", False, "Size", (
        "size", "cupsize", "drinksize", "sizechoice", "chooseasize", "sm", "largeormedium",
    )),
    ("sugar", False, "Sugar", (
        "sugar", "sugarlevel", "sweetness", "sweetnesslevel", "sweet", "sugarpercent",
        "howsweet", "sugaramount",
    )),
    ("ice", False, "Ice", (
        "ice", "icelevel", "iceamount", "howmuchice", "icechoice",
    )),
    ("toppings", False, "Toppings", (
        "topping", "toppings", "addon", "addons", "addins", "extras", "extra",
        "boba", "choosetoppings", "choosetoppingss", "toppingsaddons",
    )),
    ("milk", False, "Milk", (
        "milk", "milkalternative", "milkoption", "milktype", "dairy", "nondairy",
        "milksubstitute", "alternativemilk",
    )),
    ("temperature", False, "Hot / Iced", (
        "temp", "temperature", "hotoriced", "hoticed", "hotorcold", "servedhotoriced",
        "hot", "iced",
    )),
    ("quantity", False, "Qty", (
        "qty", "quantity", "count", "howmany", "number", "num", "amount", "drinks",
    )),
    ("notes", False, "Notes", (
        "note", "notes", "comment", "comments", "specialinstructions", "instructions",
        "requests", "specialrequests", "other", "anythingelse",
    )),
]

FIELD_LABELS = {name: label for name, _req, label, _syn in FIELDS}
REQUIRED_FIELDS = [name for name, req, _label, _syn in FIELDS if req]
OPTIONAL_FIELDS = [name for name, req, _label, _syn in FIELDS if not req]

_SYNONYMS: dict[str, str] = {}
for _name, _req, _label, _syns in FIELDS:
    _SYNONYMS[_name] = _name
    for _syn in _syns:
        _SYNONYMS.setdefault(_syn, _name)

# Sorted-letter index, for catching transposed-letter typos in headers.
_ANAGRAMS: dict[str, str] = {}
for _syn, _name in _SYNONYMS.items():
    if len(_syn) >= 4:
        _ANAGRAMS.setdefault("".join(sorted(_syn)), _name)

# Columns a Google Form or a shared sheet adds that are never part of an order.
IGNORED_HEADERS = {
    "timestamp", "emailaddress", "email", "submittedat", "date", "paid", "venmo",
    "price", "total", "cost", "subtotal", "id", "rownumber", "row",
}

# Values that mean "nothing here" rather than a real choice. Kept deliberately
# small: "none" in an Ice column means No Ice, which is a real choice, so it is
# only treated as empty for toppings.
BLANKISH = {"", "-", "--", "—", "n/a", "na", "none needed", "nil", "null", "tbd", "?"}
BLANKISH_TOPPINGS = BLANKISH | {"none", "no", "nope", "no toppings", "plain", "nothing", "0"}

MAX_ROWS = 400  # a group order past this is almost certainly a wrong file

_TOPPING_SPLIT = re.compile(r"\s*(?:,|;|/|\||\+|&|\band\b|\bplus\b)\s*", re.IGNORECASE)
_QTY_TRAILING = re.compile(r"[\s(\[]*\b[x×*]\s*(\d{1,2})\b[)\]]*\s*$", re.IGNORECASE)
_QTY_LEADING = re.compile(r"^\s*(\d{1,2})\s*[x×*]\s+", re.IGNORECASE)
_PAREN_QTY = re.compile(r"\((\d{1,2})\)\s*$")


def normalise_key(text) -> str:
    """Header text -> comparison key: lowercase, alphanumerics only."""
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def cell_text(value) -> str:
    """Any cell value -> a trimmed string, without float noise on integers."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat(sep=" ") if isinstance(value, _dt.datetime) else value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


@dataclass
class Issue:
    level: str  # "error" | "warning" | "info"
    message: str
    field: str | None = None
    row: int | None = None
    code: str | None = None  # e.g. "moved:ice>toppings", for grouping

    def as_dict(self) -> dict:
        return {"level": self.level, "message": self.message, "field": self.field,
                "row": self.row, "code": self.code}


@dataclass
class OrderRow:
    row_number: int
    person: str = ""
    drink: str = ""
    size: str = ""
    sugar: str = ""
    ice: str = ""
    toppings: list[str] = field(default_factory=list)
    milk: str = ""
    temperature: str = ""
    quantity: int = 1
    notes: str = ""
    extra: dict = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(issue.level == "error" for issue in self.issues)

    def as_dict(self) -> dict:
        data = {
            "row_number": self.row_number,
            "person": self.person,
            "drink": self.drink,
            "size": self.size,
            "sugar": self.sugar,
            "ice": self.ice,
            "toppings": list(self.toppings),
            "milk": self.milk,
            "temperature": self.temperature,
            "quantity": self.quantity,
            "notes": self.notes,
            "extra": dict(self.extra),
            "issues": [issue.as_dict() for issue in self.issues],
            "ok": self.ok,
        }
        return data


# --- header detection -------------------------------------------------------


def match_header(text: str) -> str | None:
    """Map one header cell to a field name, or None if it is not one of ours."""
    key = normalise_key(text)
    if not key or key in IGNORED_HEADERS:
        return None
    if key in _SYNONYMS:
        return _SYNONYMS[key]

    # Google Forms turns questions into headers: "What drink do you want?".
    for synonym, name in _SYNONYMS.items():
        if len(synonym) >= 4 and synonym in key:
            return name

    # Transposed letters ("Naem"). An anagram match is a much safer typo rule
    # than a loose similarity cutoff, which starts mapping "Team" to Temperature.
    anagram = _ANAGRAMS.get("".join(sorted(key)))
    if anagram:
        return anagram

    close = difflib.get_close_matches(key, list(_SYNONYMS), n=1, cutoff=0.86)
    return _SYNONYMS[close[0]] if close else None


def score_header_row(cells: list) -> tuple[int, dict[int, str]]:
    """How much a row looks like a header row, plus the mapping it would give."""
    mapping: dict[int, str] = {}
    for index, cell in enumerate(cells):
        name = match_header(cell)
        if name and name not in mapping.values():
            mapping[index] = name
    score = len(mapping)
    if "drink" in mapping.values():
        score += 2
    if "person" in mapping.values():
        score += 1
    return score, mapping


def find_header_row(rows: list[list], search_depth: int = 15) -> tuple[int, dict[int, str]]:
    """Locate the header row. Returns (index, {column index: field}).

    Sheets in the wild start with a title, a blank line, or an instructions
    block, so we scan rather than assuming row 0. Returns (-1, {}) if nothing
    in range looks like a header.
    """
    best_index, best_score, best_map = -1, 0, {}
    for index, cells in enumerate(rows[:search_depth]):
        score, mapping = score_header_row(cells)
        if score > best_score:
            best_index, best_score, best_map = index, score, mapping

    fields = set(best_map.values())
    # A drink column alone is enough; otherwise two recognised columns. Accepting
    # a header that lacks Name/Drink is deliberate — "missing the Drink column" is
    # a far more useful thing to tell someone than "no header row found".
    if "drink" in fields or len(fields) >= 2:
        return best_index, best_map
    return -1, {}


# --- value cleaning ---------------------------------------------------------


def split_toppings(text: str) -> list[str]:
    if normalise_key(text) in {normalise_key(b) for b in BLANKISH_TOPPINGS}:
        return []
    parts = [part.strip(" .\t") for part in _TOPPING_SPLIT.split(text)]
    return [part for part in parts if part and part.lower() not in BLANKISH_TOPPINGS]


def parse_quantity(text: str) -> int | None:
    """'2', '2 drinks', 'two' -> 2. Returns None when it cannot tell."""
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
             "seven": 7, "eight": 8, "nine": 9, "ten": 10, "a": 1, "an": 1, "single": 1,
             "double": 2}
    cleaned = text.strip().lower()
    if not cleaned:
        return None
    if cleaned in words:
        return words[cleaned]
    match = re.search(r"\d+", cleaned)
    if match:
        try:
            return int(match.group(0))
        except ValueError:
            return None
    return None


def extract_inline_quantity(drink: str) -> tuple[str, int | None]:
    """'Taro Slush x2' / '2x Taro Slush' / 'Taro Slush (2)' -> ('Taro Slush', 2)."""
    for pattern in (_QTY_TRAILING, _QTY_LEADING, _PAREN_QTY):
        match = pattern.search(drink)
        if match:
            try:
                quantity = int(match.group(1))
            except ValueError:
                continue
            if 1 <= quantity <= 20:
                return (drink[: match.start()] + drink[match.end():]).strip(" -,"), quantity
    return drink, None


def is_blank(text: str) -> bool:
    return text.strip().lower() in BLANKISH


# --- values in the wrong column ---------------------------------------------
#
# People put toppings in the Ice column, sugar in Notes, "Large" in Sugar. The
# columns are next to each other and everyone fills the sheet in a hurry. Rather
# than send that to Task 3 to fail a match on, work out what the value actually
# is and move it, saying so.
#
# Order matters below: "Ice Cream" and "Brown Sugar Boba" are toppings, so
# toppings are checked before the ice and sugar keywords they contain.

TOPPING_WORDS = (
    "boba", "pearl", "pudding", "jelly", "aloe", "red bean", "popping", "chia",
    "milk cap", "ice cream", "oreo", "espresso shot", "add on", "add-on", "nata",
    "crystal", "grass jelly", "coconut", "sago", "cheese foam", "taro ball",
)
SUGAR_WORDS = ("sugar", "sweet")
MILK_WORDS = ("soy", "oat milk", "almond", "lactose", "non-dairy", "nondairy",
              "whole milk", "skim", "2% milk")
SIZE_WORDS = ("medium", "large", "small", "grande", "venti")
# "Hot" and "Cold" are sizes here (Task 1, §5) but only on their own — "extra
# cold" in an Ice column is a note about ice, not a request for the Cold size.
SIZE_EXACT = ("hot", "cold")


@functools.lru_cache(maxsize=1)
def _menu_toppings() -> frozenset[str]:
    """Topping names from the menu snapshot, if there is one.

    Cached: this is called once per cell, and rebuilding it from the menu each
    time turns a 400-row import into thousands of passes over 150 items.
    """
    try:
        from . import template  # local: schema must work without the data files
        return frozenset(name.lower() for name in template.menu_hints().get("toppings", []))
    except Exception:
        return frozenset()


def classify_token(text: str) -> str | None:
    """Which column one value belongs in, or None if it is not clear."""
    token = text.strip().lower()
    if not token:
        return None

    for name in _menu_toppings():
        # Whole words only, in both directions: "boba" is inside "Brown Sugar
        # Boba", and "2 boba" contains it — but "hot" is not inside "Espresso
        # Shot" in any sense that matters.
        if token == name:
            return "toppings"
        if len(token) >= 4 and re.search(rf"\b{re.escape(token)}\b", name):
            return "toppings"
        if len(name) >= 4 and re.search(rf"\b{re.escape(name)}\b", token):
            return "toppings"
    if any(word in token for word in TOPPING_WORDS):
        return "toppings"
    if "%" in token or any(word in token for word in SUGAR_WORDS):
        return "sugar"
    if "ice" in token:
        return "ice"
    if any(word in token for word in MILK_WORDS):
        return "milk"
    if token in SIZE_EXACT or any(re.search(rf"\b{word}\b", token) for word in SIZE_WORDS):
        return "size"
    # "less", "no", "regular", "more" on their own could be ice or sugar. Leave
    # them where the user put them — the column is the only clue there is.
    return None


def classify_value(text: str) -> str | None:
    """Classify a whole cell, which may hold several comma-separated values."""
    parts = [part for part in _TOPPING_SPLIT.split(text) if part.strip()] or [text]
    votes = {classify_token(part) for part in parts}
    votes.discard(None)
    return votes.pop() if len(votes) == 1 else None


# How each field reads mid-sentence: "…looks like an ice level".
AS_A = {
    "size": "a size", "sugar": "a sugar level", "ice": "an ice level",
    "milk": "a milk option", "toppings": "a topping",
}


def reconcile_columns(row: "OrderRow") -> None:
    """Move values that are plainly in the wrong column, and say what moved."""
    def note(level, message, field, code):
        row.issues.append(Issue(level, message, field, row.row_number, code))

    handled: set[str] = set()
    for source in ("size", "sugar", "ice", "milk"):
        text = getattr(row, source)
        if not text or source in handled:
            continue
        target = classify_value(text)
        if target is None or target == source:
            continue

        if target == "toppings":
            row.toppings.extend(split_toppings(text))
            setattr(row, source, "")
        elif not getattr(row, target):
            setattr(row, target, text)
            setattr(row, source, "")
        elif classify_value(getattr(row, target)) == source:
            # Two columns filled in the wrong order — the common case. Swap them.
            other = getattr(row, target)
            setattr(row, target, text)
            setattr(row, source, other)
            handled.update({source, target})
            pair = ">".join(sorted((source, target)))
            note("info",
                 f"the {FIELD_LABELS[source]} and {FIELD_LABELS[target]} values were the "
                 f"wrong way round — swapped them", target, f"swapped:{pair}")
            continue
        else:
            note("warning",
                 f"the {FIELD_LABELS[source]} column says \"{text}\", which looks like "
                 f"{AS_A[target]} — left it where it was",
                 source, f"clash:{source}>{target}")
            continue

        note("info", f"moved \"{text}\" from {FIELD_LABELS[source]} to {FIELD_LABELS[target]}",
             target, f"moved:{source}>{target}")

    # And the other direction: an ice or sugar value sitting in Toppings.
    kept = []
    for topping in row.toppings:
        target = classify_token(topping)
        if target is None or target == "toppings":
            kept.append(topping)
        elif not getattr(row, target):
            setattr(row, target, topping)
            note("info", f"moved \"{topping}\" from Toppings to {FIELD_LABELS[target]}",
                 target, f"moved:toppings>{target}")
        else:
            kept.append(topping)
            note("warning",
                 f"\"{topping}\" in Toppings looks like {AS_A[target]}, but "
                 f"{FIELD_LABELS[target]} already says \"{getattr(row, target)}\" — left it in "
                 f"Toppings", "toppings", f"clash:toppings>{target}")
    row.toppings = kept


# --- row building -----------------------------------------------------------


def build_row(row_number: int, cells: list, mapping: dict[int, str], headers: list[str]) -> OrderRow:
    row = OrderRow(row_number=row_number)
    values: dict[str, str] = {}

    for index, cell in enumerate(cells):
        text = cell_text(cell)
        name = mapping.get(index)
        if name is None:
            # Not one of our fields — keep it anyway. A "Venmo" or "Paid?" column
            # is somebody's actual data, and dropping it silently is worse than
            # carrying it through to the preview.
            header = headers[index] if index < len(headers) else ""
            if text and header:
                row.extra[header] = text
            continue
        if name in values and values[name]:
            # Two columns mapped to the same field (e.g. "Topping 1"/"Topping 2").
            values[name] = f"{values[name]}, {text}" if text else values[name]
        else:
            values[name] = text

    row.person = "" if is_blank(values.get("person", "")) else values.get("person", "").strip()
    drink = "" if is_blank(values.get("drink", "")) else values.get("drink", "").strip()

    drink, inline_quantity = extract_inline_quantity(drink)
    row.drink = drink

    for name in ("size", "sugar", "ice", "milk", "temperature", "notes"):
        text = values.get(name, "")
        row.__setattr__(name, "" if is_blank(text) else text.strip())

    row.toppings = split_toppings(values.get("toppings", ""))
    reconcile_columns(row)

    column_quantity = parse_quantity(values.get("quantity", ""))
    quantity = column_quantity
    if quantity is None and inline_quantity:
        quantity = inline_quantity
        row.issues.append(Issue("info", f"read a quantity of {quantity} from the drink name",
                                "quantity", row_number))
    elif column_quantity and inline_quantity and column_quantity != inline_quantity:
        row.issues.append(Issue(
            "warning", f"the quantity column says {column_quantity} but the drink says "
                       f"x{inline_quantity}; using {column_quantity}", "quantity", row_number))

    if quantity is None:
        quantity = 1
    elif quantity < 1:
        row.issues.append(Issue("warning", "quantity below 1, treating as 1", "quantity", row_number))
        quantity = 1
    elif quantity > 20:
        row.issues.append(Issue("warning", f"quantity of {quantity} looks like a typo, capped at 20",
                                "quantity", row_number))
        quantity = 20
    row.quantity = quantity

    if not row.drink:
        row.issues.append(Issue("error", "no drink in this row", "drink", row_number))
    if not row.person:
        row.issues.append(Issue("warning", "no name, so this drink will be unlabelled",
                                "person", row_number))
    if row.temperature and not row.size:
        row.issues.append(Issue("info", "'hot' is a size at Kung Fu Tea, not a separate drink",
                                "temperature", row_number))

    return row


def is_blank_row(cells: list) -> bool:
    return not any(cell_text(cell) for cell in cells)


def parse_table(rows: list[list], source_label: str = "") -> tuple[list[OrderRow], dict, list[Issue]]:
    """Rows of cells -> (order rows, column map, sheet-level issues)."""
    issues: list[Issue] = []
    rows = [row for row in rows if row is not None]

    if not any(not is_blank_row(row) for row in rows):
        issues.append(Issue("error", f"{source_label or 'The sheet'} is empty".strip()))
        return [], {}, issues

    header_index, mapping = find_header_row(rows)
    if header_index < 0:
        issues.append(Issue(
            "error",
            "couldn't find a header row — the sheet needs a row of column titles "
            "including at least Name and Drink"))
        return [], {}, issues

    headers = [cell_text(cell) for cell in rows[header_index]]
    column_map = {FIELD_LABELS[name]: headers[index] for index, name in mapping.items()
                  if index < len(headers)}

    if header_index > 0:
        skipped = header_index
        issues.append(Issue("info", f"skipped {skipped} row{'s' if skipped != 1 else ''} "
                                    f"above the header"))

    missing = [FIELD_LABELS[name] for name in REQUIRED_FIELDS if name not in mapping.values()]
    if missing:
        issues.append(Issue("error", f"missing required column{'s' if len(missing) > 1 else ''}: "
                                     f"{', '.join(missing)}"))
        return [], column_map, issues

    unmapped = [FIELD_LABELS[name] for name in OPTIONAL_FIELDS if name not in mapping.values()]
    if unmapped:
        issues.append(Issue("info", f"no column for {', '.join(unmapped)} — "
                                    f"the store's default will be used"))

    order_rows: list[OrderRow] = []
    truncated = 0
    for offset, cells in enumerate(rows[header_index + 1:], start=header_index + 2):
        if is_blank_row(cells):
            continue
        if len(order_rows) >= MAX_ROWS:
            truncated += 1
            continue
        order_rows.append(build_row(offset, cells, mapping, headers))

    # If a whole column is mislabelled, say it once at the top rather than
    # leaving the user to notice the same note on every row.
    moves = Counter(issue.code for row in order_rows for issue in row.issues
                    if issue.code and issue.code.split(":")[0] in ("moved", "swapped"))
    for code, count in moves.items():
        if count < 2:
            continue
        kind, pair = code.split(":", 1)
        source, target = pair.split(">")
        if kind == "swapped":
            issues.append(Issue(
                "info", f"your {FIELD_LABELS[source]} and {FIELD_LABELS[target]} columns were "
                        f"the wrong way round on {count} rows — swapped them",
                None, None, code))
        else:
            issues.append(Issue(
                "info", f"your {FIELD_LABELS[source]} column held "
                        f"{FIELD_LABELS[target].lower()} on {count} rows — those moved to "
                        f"{FIELD_LABELS[target]}", None, None, code))

    if truncated:
        issues.append(Issue("warning", f"only the first {MAX_ROWS} orders were read; "
                                       f"{truncated} more were ignored"))
    if not order_rows:
        issues.append(Issue("error", "found the header but no order rows under it"))

    return order_rows, column_map, issues
