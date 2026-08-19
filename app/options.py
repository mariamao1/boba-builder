"""The user's words -> this store's option set. Task 3.

`schema.py` does the structural half of the import: find the header, work out
which column is which, split toppings, pull "x2" out of a drink name. It leaves
the values exactly as they were typed, on purpose.

This module does the vocabulary half. "LARGE " becomes `Large`, "half sweet"
becomes `50%`, "oreo" becomes `OREO®`, and anything this store simply does not
sell — oat milk, 25% sugar — is flagged on the row instead of being passed
downstream to fail a match nobody can explain.

TWO LEVELS, AND WHY WE STOP AT THE FIRST
----------------------------------------
Task 1 §5 found that option *literals* are per item, not per store:

    user text  ->  store canonical label  ->  that item's literal
    "lg"           "Large"                   "Large .7" | "Large 1" | "Large.3"

Only the first arrow can be walked before the drink is known — Bay Ridge has 16
distinct size literals collapsing to 4 labels, and which one is right depends on
the item. So this module resolves to the canonical label and stops. Task 4 walks
the second arrow using each item's own `canonical` map.

Sugar is the exception worth naming: the store's names are inconsistent English
("Regular Sugar 100%" but "Less S 70%"), so per Task 1 we canonicalise to the
percentage — `50%` — and let the matcher find the option carrying it.

The raw text is never overwritten. `row.size` stays as the user typed it and the
resolved value lands in `row.canonical`, so a wrong guess here is always
recoverable downstream and visible in the preview.

WHERE THE WORDS COME FROM
-------------------------
The synonym tables are `data/mapping.json`, loaded by `app/mapping.py` — not
constants in this file. Adding "boba pearls" to the topping list is an edit to
that file, and `python3 -m app.mapping` says whether every value in it still
names something this store sells. See Task 4.
"""

from __future__ import annotations

import difflib
import functools
import re

from . import mapping, template
from .menu import norm, squash  # the comparison keys, shared with the matcher
from .schema import Issue, OrderRow

# Statuses a single value can come back with. Only "ok" is silent.
OK = "ok"                    # matched an option this store sells
ASSUMED = "assumed"          # matched, but we filled in a judgement call
BLANK = "blank"              # nothing there — means "store default", not an error
UNKNOWN = "unknown"          # we could not tell what this is
UNAVAILABLE = "unavailable"  # we know what it is; this store doesn't sell it
AMBIGUOUS = "ambiguous"      # matches several real options


class Resolved:
    """One value's outcome. Falsy when there is nothing usable."""

    __slots__ = ("value", "status", "message", "suggestions", "quantity")

    def __init__(self, value: str = "", status: str = BLANK, message: str = "",
                 suggestions: list[str] | None = None, quantity: int = 1):
        self.value = value
        self.status = status
        self.message = message
        # Real menu names to offer instead. Never applied automatically.
        self.suggestions = suggestions or []
        # Toppings only: "2x pudding" is two puddings, not one (Task 1 §6).
        self.quantity = quantity

    def __bool__(self) -> bool:
        return bool(self.value)

    def __repr__(self) -> str:  # for test failure output
        return f"Resolved({self.value!r}, {self.status!r})"


# --- the vocabulary ---------------------------------------------------------
#
# The synonym tables live in data/mapping.json, keyed on norm() output. They are
# the words people actually put in a boba spreadsheet, not a thesaurus, and they
# are a file rather than code so that a menu change is an edit and not a patch.
#
#   size.words            "lg" -> "Large"
#   size.nearest          sizes this store lacks, and the nearest it has
#   size.means_default    words that mean "no size modifier" ("iced")
#   size.temperature      a Hot/Iced column -> the size axis (Task 1 §5)
#   sugar.words           "half sweet" -> 50; canonicalised to a percentage
#   ice.words             "easy on the" -> "less"
#   milk.words / .not_sold / .means_default
#   toppings.words        "oreo" -> "OREO®"
#   toppings.ambiguous    families too vague to guess between ("popping boba")

# "2x boba", "x2 boba", "double pudding" — the topping is what we need for the
# match; the count rides along on the Resolved and is spent by the cart writer,
# because options carry their own quantity field (Task 1 §6).
# Stripping is always tried *alongside* the untouched text, never instead of it,
# so "extra shot" can still reach the synonym table as itself.
_TOPPING_QTY = re.compile(
    r"^\s*(?:\d{1,2}\s*[x×*]?|[x×*]\s*\d{1,2}|double|triple|extra)\s+(?=\D)"
    r"|[\s(]*[x×*]\s*\d{1,2}\s*\)?\s*$",
    re.IGNORECASE)

# Only a written number or an unmistakable word counts. "extra boba" is left at
# one: "extra" is also half of "extra shot", and reading it as a count would
# quietly charge somebody for a topping they didn't ask twice for.
_COUNT_WORDS = {"double": 2, "triple": 3}


def topping_count(text: str) -> int:
    """How many of a topping "2x pudding" asks for. 1 unless it plainly says."""
    found = _TOPPING_QTY.search(text or "")
    if not found:
        return 1
    marker = found.group(0).strip().lower()
    word = _COUNT_WORDS.get(marker)
    if word:
        return word
    digits = re.search(r"\d{1,2}", marker)
    if not digits:
        return 1
    count = int(digits.group(0))
    return count if 1 <= count <= 20 else 1


class StoreOptions:
    """One store's option vocabulary, built from the Task 1 menu snapshot."""

    def __init__(self, hints: dict, config: mapping.Mapping | None = None):
        # The vocabulary is configuration; the option lists are the store's.
        self.config = config or mapping.load()
        self.store = hints.get("store")
        self.restaurant_id = hints.get("restaurant_id")

        self.sizes: list[str] = list(hints.get("sizes") or [])
        self.ice: list[str] = list(hints.get("ice") or [])
        self.toppings: list[str] = list(hints.get("toppings") or [])
        self.milk: list[str] = list(hints.get("milk") or [])
        self.drinks: list[str] = list(hints.get("drinks") or [])

        # Sugar: the store's names are inconsistent English, so key on the number.
        # "Less S 70%" and "Regular Sugar 100%" both reduce to their percentage.
        self.sugar_levels: list[int] = []
        for name in hints.get("sugar") or []:
            found = re.search(r"(\d+)\s*%", name)
            if found:
                level = int(found.group(1))
                if level not in self.sugar_levels:
                    self.sugar_levels.append(level)
        self.sugar_levels.sort()

        self._size_by_key = {norm(name): name for name in self.sizes}
        self._ice_by_key = {norm(name): name for name in self.ice}
        self._milk_by_key = {norm(name): name for name in self.milk}
        self._topping_by_key = {norm(name): name for name in self.toppings}
        self._drink_by_key: dict[str, str] = {}
        self._drink_by_squash: dict[str, set[str]] = {}
        for name in self.drinks:
            self._drink_by_key.setdefault(norm(name), name)
            self._drink_by_squash.setdefault(squash(name), set()).add(name)
        self._drink_aliases = {norm(key): value
                               for key, value in self.config.drink_aliases.items()}

        # "No Mango Jelly" is a removal option. It is orderable, so it stays in
        # the vocabulary, but it must never win a fuzzy match against
        # "mango jelly" — that would invert the request.
        self._fuzzy_toppings = [name for name in self.toppings
                                if not norm(name).startswith("no ")]

    # -- helpers -------------------------------------------------------------

    def sugar_label(self, level: int) -> str:
        return f"{level}%"

    def _ice_for(self, level: str) -> str:
        """'less' -> the store's own 'Less Ice'."""
        exact = self._ice_by_key.get(f"{level} ice")
        if exact:
            return exact
        for key, name in self._ice_by_key.items():
            if key.startswith(level):
                return name
        return ""

    # -- per-axis resolution -------------------------------------------------

    def size(self, text: str, temperature: str = "") -> Resolved:
        key = norm(text)
        if not key and temperature:
            # "Hot" is a size at Kung Fu Tea, not a temperature (Task 1 §5), so a
            # Hot/Iced column feeds the size axis. An empty mapping value means
            # "iced" — the default on an iced drink, so the right order sends no
            # size modifier at all; only 4 of 154 items have a Cold option.
            temp = norm(temperature)
            if temp in self.config.size_temperature:
                value = self.config.size_temperature[temp]
                if value and value in self.sizes:
                    return Resolved(value, ASSUMED, f"read '{temp}' as the {value} size")
            return Resolved("", BLANK)
        if not key:
            return Resolved("", BLANK)

        if key in self._size_by_key:
            return Resolved(self._size_by_key[key], OK)
        if key in self.config.size_default:
            return Resolved("", BLANK)
        if key in self.config.size_words and self.config.size_words[key] in self.sizes:
            return Resolved(self.config.size_words[key], OK)
        if key in self.config.size_nearest:
            value, why = self.config.size_nearest[key]
            if value in self.sizes:
                return Resolved(value, ASSUMED, why)
        # "large cup", "size: large"
        for word, value in self.config.size_words.items():
            if len(word) > 1 and re.search(rf"\b{re.escape(word)}\b", key) and value in self.sizes:
                return Resolved(value, OK)
        return Resolved("", UNKNOWN,
                        f"\"{text}\" isn't a size here — this store has "
                        f"{_join(self.sizes)}")

    def sugar(self, text: str) -> Resolved:
        key = norm(text)
        if not key:
            return Resolved("", BLANK)
        # Drop the noise words so "50% sugar" and "half sweetness" both reduce.
        stripped = _drop_words(key, self.config.sugar_noise)

        found = re.search(r"(\d+)\s*%?", stripped)
        if found:
            level = int(found.group(1))
            if level in self.sugar_levels:
                return Resolved(self.sugar_label(level), OK)
            return Resolved("", UNKNOWN,
                            f"this store's sugar levels are "
                            f"{_join([f'{n}%' for n in self.sugar_levels])}, not {level}%")

        for candidate in (stripped, key):
            if candidate in self.config.sugar_words:
                level = self.config.sugar_words[candidate]
                if level in self.sugar_levels:
                    return Resolved(self.sugar_label(level), OK)
        for word, level in self.config.sugar_words.items():
            if re.search(rf"\b{re.escape(word)}\b", stripped) and level in self.sugar_levels:
                return Resolved(self.sugar_label(level), OK)
        return Resolved("", UNKNOWN,
                        f"\"{text}\" isn't a sugar level — this store has "
                        f"{_join([f'{n}%' for n in self.sugar_levels])}")

    def ice_level(self, text: str) -> Resolved:
        key = norm(text)
        if not key:
            return Resolved("", BLANK)
        if key in self._ice_by_key:
            return Resolved(self._ice_by_key[key], OK)

        stripped = _drop_words(key, self.config.ice_noise)
        if not stripped:
            # The cell said only "ice" — that is the store default, not a level.
            return Resolved("", BLANK)

        for candidate in (stripped, key):
            if candidate in self.config.ice_words:
                name = self._ice_for(self.config.ice_words[candidate])
                if name:
                    return Resolved(name, OK)
        for word, level in self.config.ice_words.items():
            if re.search(rf"\b{re.escape(word)}\b", stripped):
                name = self._ice_for(level)
                if name:
                    return Resolved(name, OK)
        return Resolved("", UNKNOWN,
                        f"\"{text}\" isn't an ice level — this store has {_join(self.ice)}")

    def milk_option(self, text: str) -> Resolved:
        key = norm(text)
        if not key:
            return Resolved("", BLANK)
        if key in self._milk_by_key:
            return Resolved(self._milk_by_key[key], OK)
        if key in self.config.milk_words and self.config.milk_words[key] in self.milk:
            return Resolved(self.config.milk_words[key], OK)
        if key in self.config.milk_default:
            return Resolved("", BLANK)
        for word, value in self.config.milk_words.items():
            if re.search(rf"\b{re.escape(word)}\b", key) and value in self.milk:
                return Resolved(value, OK)
        for word in self.config.milk_not_sold:
            if word in key:
                offer = _join(self.milk) if self.milk else "no milk alternatives"
                return Resolved("", UNAVAILABLE,
                                f"this store doesn't have {text.strip()} — it offers {offer}")
        return Resolved("", UNKNOWN, f"\"{text}\" isn't a milk option we recognise")

    def topping(self, text: str) -> Resolved:
        """One topping, plus how many of it "2x pudding" asked for."""
        resolved = self._topping(text)
        if resolved.value:
            resolved.quantity = topping_count(text)
        return resolved

    def _topping(self, text: str) -> Resolved:
        raw = text.strip()
        # Two candidates: the text as typed, and the text with any count removed.
        # Trying both means "extra shot" still finds Espresso Shot while
        # "extra boba" still finds Boba.
        keys = [norm(raw)]
        without_count = norm(_TOPPING_QTY.sub("", raw))
        if without_count and without_count not in keys:
            keys.append(without_count)
        keys = [key for key in keys if key]
        if not keys:
            return Resolved("", BLANK)

        for key in keys:
            if key in self._topping_by_key:
                return Resolved(self._topping_by_key[key], OK)
        for key in keys:
            if key in self.config.topping_ambiguous:
                choices = [name for name in self.config.topping_ambiguous[key]
                           if name in self.toppings]
                if len(choices) > 1:
                    return Resolved("", AMBIGUOUS,
                                    f"\"{raw}\" could be {_join(choices, 'or')} — say which")
                if len(choices) == 1:
                    return Resolved(choices[0], OK)
        for key in keys:
            if key in self.config.topping_words \
                    and self.config.topping_words[key] in self.toppings:
                return Resolved(self.config.topping_words[key], OK)
        for key in keys:
            for word, value in self.config.topping_words.items():
                if len(word) >= 4 and re.search(rf"\b{re.escape(word)}\b", key) \
                        and value in self.toppings:
                    return Resolved(value, OK)

        fuzzy = {norm(name): name for name in self._fuzzy_toppings}
        for key in keys:
            close = difflib.get_close_matches(key, list(fuzzy), n=1,
                                              cutoff=self.config.topping_cutoff)
            if close:
                name = fuzzy[close[0]]
                return Resolved(name, ASSUMED, f"read \"{raw}\" as {name}")
        return Resolved("", UNKNOWN, f"this store doesn't have a \"{raw}\" topping")

    def drink_suggestions(self, text: str, limit: int = 5) -> list[str]:
        """Real menu names close to what someone typed, best first.

        Two ways of being close, because they catch different mistakes:
        a character-level ratio finds typos ("Wintermelon" -> "Winter Melon"),
        and shared whole words find people who wrote part of the name
        ("matcha" -> "Matcha Milk"). The better of the two wins.
        """
        key = norm(text)
        if not key or not self.drinks:
            return []

        wanted = {word for word in key.split() if len(word) >= 3}
        scored: list[tuple[float, str]] = []
        for candidate_key, name in self._drink_by_key.items():
            ratio = difflib.SequenceMatcher(None, key, candidate_key).ratio()
            score = ratio
            if wanted:
                shared = wanted & set(candidate_key.split())
                # Capped below 1.0 so a real typo match always outranks a
                # merely-overlapping name.
                score = max(score, 0.9 * len(shared) / len(wanted))
            if score >= self.config.drink_cutoff:
                scored.append((score, name))

        # Name as the tiebreak, so the same sheet suggests the same things twice
        # running (Task 1 §5 makes the same point about duplicate item names).
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [name for _score, name in scored[:limit]]

    def search_drinks(self, text: str, limit: int = 6) -> list[str]:
        """Type-ahead over the menu: a few close drinks, best first.

        Different job from drink_suggestions(), which scores one finished
        spreadsheet cell against the menu. Here the text is half-typed, so a
        prefix counts for more than a character ratio does — "ta" should reach
        Taro Slush, which no similarity score would rank highly.
        """
        key = norm(text)
        if not key or not self.drinks:
            return []

        words = key.split()
        scored: list[tuple[float, str]] = []
        for candidate_key, name in self._drink_by_key.items():
            candidate_words = candidate_key.split()
            if candidate_key == key:
                score = 1.0
            elif candidate_key.startswith(key):
                score = 0.95
            elif key in candidate_key:
                score = 0.85
            elif all(any(word.startswith(part) for word in candidate_words)
                     for part in words):
                # Every word typed starts a word in the name: "hon oo" -> Honey Oolong.
                score = 0.8
            else:
                score = difflib.SequenceMatcher(None, key, candidate_key).ratio()
            if score >= 0.5:
                scored.append((score, name))

        scored.sort(key=lambda pair: (-pair[0], len(pair[1]), pair[1]))
        return [name for _score, name in scored[:limit]]

    def drink(self, text: str) -> Resolved:
        """The drink name, if it is a name this store uses. No guessing.

        Three ways of being the same name, all safe to apply: exactly as
        written, the same name spelled differently ("Wintermelon" for "Winter
        Melon"), or an alias from data/mapping.json. Anything looser comes back
        unresolved with suggestions attached — picking the wrong *drink* is the
        one mistake here that costs a whole drink, so the person decides.

        `app/matcher.py` finds the same names by the same three tiers; it goes
        on to pick the menu item, which needs the menu itself and not just a
        list of names.
        """
        key = norm(text)
        if not key:
            return Resolved("", BLANK)
        if not self.drinks:
            return Resolved("", BLANK)
        if key in self._drink_by_key:
            return Resolved(self._drink_by_key[key], OK)

        alias = self._drink_aliases.get(key)
        if alias and norm(alias) in self._drink_by_key:
            name = self._drink_by_key[norm(alias)]
            return Resolved(name, ASSUMED, f"read \"{text.strip()}\" as {name}")

        same = self._drink_by_squash.get(squash(text)) or set()
        if len(same) == 1:
            name = next(iter(same))
            return Resolved(name, ASSUMED, f"read \"{text.strip()}\" as {name}")
        if len(same) > 1:
            choices = sorted(same)
            return Resolved("", AMBIGUOUS,
                            f"\"{text.strip()}\" could be {_join(choices, 'or')} — say which",
                            choices)

        suggestions = self.drink_suggestions(text)
        missing = f"no drink called \"{text.strip()}\" on this store's menu"
        if suggestions:
            return Resolved("", UNKNOWN,
                            f"{missing} — did you mean {_join(suggestions[:2], 'or')}?",
                            suggestions)
        return Resolved("", UNKNOWN, missing)


def _drop_words(key: str, words) -> str:
    """Remove whole noise words from a comparison key: "50% sugar" -> "50%"."""
    if not words:
        return key
    pattern = "|".join(re.escape(word) for word in words)
    return re.sub(r"\s+", " ", re.sub(rf"\b(?:{pattern})\b", " ", key)).strip()


def _join(names, conjunction: str = "and") -> str:
    names = list(names)
    if not names:
        return "nothing"
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} {conjunction} {names[-1]}"


@functools.lru_cache(maxsize=1)
def store_options() -> StoreOptions:
    """The target store's vocabulary. Cached — the snapshot doesn't change."""
    return StoreOptions(template.menu_hints())


# --- applying it to a parsed sheet ------------------------------------------

# Level per status. Nothing here is an error: an unresolved sugar level does not
# stop a drink being ordered, it just means the store default is used, and the
# person reading the preview is the one who should decide.
_LEVEL = {UNKNOWN: "warning", UNAVAILABLE: "warning", AMBIGUOUS: "warning",
          ASSUMED: "info"}


def resolve_row(row: OrderRow, options: StoreOptions | None = None) -> dict:
    """Fill `row.canonical` and flag whatever this store can't honour.

    Returns the canonical dict, which is also set on the row. The raw values are
    left exactly as they were typed.
    """
    options = options or store_options()

    def flag(resolved: Resolved, field: str) -> None:
        level = _LEVEL.get(resolved.status)
        if level and resolved.message:
            row.issues.append(Issue(level, resolved.message, field, row.row_number,
                                    f"option:{resolved.status}:{field}"))

    canonical: dict = {}

    size = options.size(row.size, row.temperature)
    flag(size, "size")
    canonical["size"] = size.value

    sugar = options.sugar(row.sugar)
    flag(sugar, "sugar")
    canonical["sugar"] = sugar.value

    ice = options.ice_level(row.ice)
    flag(ice, "ice")
    canonical["ice"] = ice.value

    milk = options.milk_option(row.milk)
    flag(milk, "milk")
    canonical["milk"] = milk.value

    toppings: list[str] = []
    counts: dict = {}
    for text in row.toppings:
        resolved = options.topping(text)
        flag(resolved, "toppings")
        # Duplicates would order the same topping twice.
        if resolved.value and resolved.value not in toppings:
            toppings.append(resolved.value)
            if resolved.quantity > 1:
                counts[resolved.value] = resolved.quantity
    canonical["toppings"] = toppings
    # Only the ones somebody asked twice for; absent means one of each.
    if counts:
        canonical["topping_quantities"] = counts

    drink = options.drink(row.drink)
    flag(drink, "drink")
    canonical["drink"] = drink.value

    # What we'd offer instead. A row with no drink at all gets nothing to
    # suggest from, so the page offers the whole menu there instead.
    row.suggestions = {"drink": list(drink.suggestions)} if drink.suggestions else {}

    row.canonical = canonical
    return canonical


def summarise(rows: list[OrderRow], options: StoreOptions | None = None) -> list[Issue]:
    """The once-at-the-top notes, matching how parse_table reports a bad column.

    Separate from annotate() so it can be recomputed on its own after somebody
    corrects a row, without re-resolving the whole sheet.
    """
    options = options or store_options()
    if not options.drinks:
        # No snapshot on disk: say so rather than quietly resolving nothing.
        return [Issue("info", "no menu snapshot for this store, so the drinks and "
                              "options weren't checked against it", None, None,
                      "option:no-snapshot")]

    unmatched = sum(1 for row in rows if row.drink and not row.canonical.get("drink"))
    if not unmatched:
        return []
    return [Issue(
        "warning",
        f"{unmatched} drink{'s' if unmatched != 1 else ''} didn't match this store's "
        f"menu by name — pick the right one on the row, or leave it for the cart builder",
        "drink", None, "option:unmatched-drinks")]


# Sheet-level codes summarise() owns, and so must clear before recomputing.
SUMMARY_CODES = ("option:unmatched-drinks", "option:no-snapshot")


def annotate(rows: list[OrderRow], options: StoreOptions | None = None) -> list[Issue]:
    """Resolve every row, and summarise anything that went wrong sheet-wide."""
    options = options or store_options()
    for row in rows:
        resolve_row(row, options)
    return summarise(rows, options)
