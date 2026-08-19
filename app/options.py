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
"""

from __future__ import annotations

import difflib
import functools
import re

from . import template
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

    __slots__ = ("value", "status", "message", "suggestions")

    def __init__(self, value: str = "", status: str = BLANK, message: str = "",
                 suggestions: list[str] | None = None):
        self.value = value
        self.status = status
        self.message = message
        # Real menu names to offer instead. Never applied automatically.
        self.suggestions = suggestions or []

    def __bool__(self) -> bool:
        return bool(self.value)

    def __repr__(self) -> str:  # for test failure output
        return f"Resolved({self.value!r}, {self.status!r})"


def norm(text) -> str:
    """Comparison key: lowercase, punctuation to spaces, percent kept.

    Percent survives because it is the whole meaning of a sugar level, and the
    ® in OREO® has to go for "oreo" to match.
    """
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%]+", " ", str(text or "").lower())).strip()


# --- synonym tables ---------------------------------------------------------
#
# Keyed on norm() output. These are the words people actually put in a boba
# spreadsheet, not a thesaurus.

SIZE_WORDS = {
    "m": "Medium", "med": "Medium", "md": "Medium", "medium": "Medium",
    "regular": "Medium", "reg": "Medium", "standard": "Medium", "normal": "Medium",
    "l": "Large", "lg": "Large", "large": "Large", "big": "Large",
    "biggest": "Large", "largest": "Large",
    "hot": "Hot", "h": "Hot", "warm": "Hot",
    "cold": "Cold",
}

# "Iced" is not a size, wherever it is written. Iced is what these drinks
# already are — only 4 of 154 items even have a Cold option — so the correct
# order sends no size modifier. Recognised so it doesn't raise a flag.
SIZE_DEFAULT = {"iced", "ice", "cold drink", "as normal", "whatever", "any"}

# Sizes the store doesn't have, mapped to the nearest thing it does, because
# "small" is a real request and "we don't do small" is not a useful answer.
SIZE_NEAREST = {
    "s": ("Medium", "this store's smallest is Medium"),
    "small": ("Medium", "this store's smallest is Medium"),
    "sm": ("Medium", "this store's smallest is Medium"),
    "xl": ("Large", "this store's biggest is Large"),
    "extra large": ("Large", "this store's biggest is Large"),
    "xtra large": ("Large", "this store's biggest is Large"),
    "venti": ("Large", "this store's biggest is Large"),
    "grande": ("Medium", "read as Medium"),
}

SUGAR_WORDS = {
    "no": 0, "none": 0, "zero": 0, "unsweetened": 0, "unsweet": 0, "sugar free": 0,
    "no sugar": 0, "without": 0, "plain": 0,
    "little": 30, "light": 30, "lightly sweet": 30, "slightly sweet": 30, "lite": 30,
    "quarter": 30,
    "half": 50, "half sweet": 50, "medium": 50, "mid": 50,
    "less": 70, "less sweet": 70, "lower": 70, "reduced": 70, "a bit less": 70,
    "regular": 100, "normal": 100, "standard": 100, "full": 100, "default": 100,
    "full sweet": 100, "sweet": 100, "yes": 100,
    "extra": 120, "extra sweet": 120, "more": 120, "very sweet": 120, "max": 120,
}

ICE_WORDS = {
    "no": "no", "none": "no", "zero": "no", "0": "no", "without": "no",
    "iceless": "no", "hold the": "no", "skip": "no",
    "less": "less", "light": "less", "little": "less", "half": "less",
    "lite": "less", "easy": "less", "easy on the": "less",
    "regular": "regular", "normal": "regular", "standard": "regular",
    "full": "regular", "yes": "regular", "default": "regular", "some": "regular",
    "more": "more", "extra": "more", "lots": "more", "lots of": "more",
    "heavy": "more", "double": "more", "max": "more",
}

# Only Soy Milk exists at Bay Ridge, but people ask for the others by name and
# deserve to be told which one they'll actually get.
MILK_WORDS = {
    "soy": "Soy Milk", "soy milk": "Soy Milk", "soya": "Soy Milk",
}
# "none", "regular", "whole milk" in a Milk column all mean the same thing: don't
# substitute anything. That is the absence of a modifier, not a request the store
# can't meet, so it must not raise a flag.
MILK_DEFAULT = {"none", "no", "n a", "na", "regular", "regular milk", "normal",
                "standard", "default", "milk", "whole", "whole milk", "dairy",
                "cow", "cows milk", "skim", "skim milk", "2%", "2% milk", "plain"}
# Genuine alternatives this store does not carry.
MILK_NOT_SOLD = ("oat", "almond", "cashew", "coconut milk", "rice milk", "lactose",
                 "lactaid", "macadamia", "pea milk", "half and half", "dairy free",
                 "non dairy", "nondairy")

TOPPING_WORDS = {
    "pearl": "Boba", "pearls": "Boba", "tapioca": "Boba", "tapioca pearls": "Boba",
    "black pearls": "Boba", "bubbles": "Boba", "black boba": "Boba", "bubble": "Boba",
    "tapioca boba": "Boba",
    "oreo": "OREO®", "oreos": "OREO®", "cookies": "OREO®", "cookie": "OREO®",
    "aloe": "Aloe Jelly", "aloe vera": "Aloe Jelly",
    "grass jelly": "Herbal Jelly",
    "cheese foam": "Milk Cap", "cheese cap": "Milk Cap", "milk foam": "Milk Cap",
    "salted cheese": "Milk Cap", "cream cap": "Milk Cap", "foam": "Milk Cap",
    "red beans": "Red Bean", "redbean": "Red Bean", "azuki": "Red Bean",
    "chia": "Chia Seeds", "chia seed": "Chia Seeds",
    "espresso": "Espresso Shot", "coffee shot": "Espresso Shot",
    "extra shot": "Espresso Shot", "shot of espresso": "Espresso Shot",
    "crystal": "Crystal Boba", "white pearls": "Crystal Boba",
    "white boba": "Crystal Boba", "agar boba": "Crystal Boba",
    "protein": "Protein Add On", "protein powder": "Protein Add On",
    "brown sugar": "Brown Sugar Wow Boba", "wow boba": "Brown Sugar Wow Boba",
    "flan": "Pudding", "egg pudding": "Pudding", "custard": "Pudding",
}

# Real requests that name a family rather than a product. Guessing between them
# would put something the group didn't ask for in the cart.
TOPPING_AMBIGUOUS = {
    "popping boba": ("Strawberry Popping Boba", "Coffee Popping Boba",
                     "Mango Popping Boba"),
    "popping": ("Strawberry Popping Boba", "Coffee Popping Boba",
                "Mango Popping Boba"),
    "jelly": ("Mango Jelly", "Aloe Jelly", "Herbal Jelly", "Nata Jelly"),
    "milk cap": ("Milk Cap", "Strawberry Milk Cap", "Matcha Milk Cap"),
}

# "2x boba", "x2 boba", "double pudding" — the topping is what we need; the count
# belongs to the cart writer (options carry their own quantity field, Task 1 §6).
# Stripping is always tried *alongside* the untouched text, never instead of it,
# so "extra shot" can still reach the synonym table as itself.
_TOPPING_QTY = re.compile(
    r"^\s*(?:\d{1,2}\s*[x×*]?|[x×*]\s*\d{1,2}|double|triple|extra)\s+(?=\D)"
    r"|[\s(]*[x×*]\s*\d{1,2}\s*\)?\s*$",
    re.IGNORECASE)


class StoreOptions:
    """One store's option vocabulary, built from the Task 1 menu snapshot."""

    def __init__(self, hints: dict):
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
        for name in self.drinks:
            self._drink_by_key.setdefault(norm(name), name)

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
            # Hot/Iced column feeds the size axis.
            temp = norm(temperature)
            if temp in ("hot", "warm"):
                return Resolved("Hot", ASSUMED, "read 'hot' as the Hot size")
            if temp in ("iced", "ice", "cold"):
                # Iced is the default on iced drinks; only 4 items have a Cold
                # option at all. Sending nothing is the correct iced order.
                return Resolved("", BLANK)
            return Resolved("", BLANK)
        if not key:
            return Resolved("", BLANK)

        if key in self._size_by_key:
            return Resolved(self._size_by_key[key], OK)
        if key in SIZE_DEFAULT:
            return Resolved("", BLANK)
        if key in SIZE_WORDS and SIZE_WORDS[key] in self.sizes:
            return Resolved(SIZE_WORDS[key], OK)
        if key in SIZE_NEAREST:
            value, why = SIZE_NEAREST[key]
            if value in self.sizes:
                return Resolved(value, ASSUMED, why)
        # "large cup", "size: large"
        for word, value in SIZE_WORDS.items():
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
        stripped = re.sub(r"\b(sugar|sweetness|sweetened|sweet|level|s)\b", " ", key)
        stripped = re.sub(r"\s+", " ", stripped).strip()

        found = re.search(r"(\d+)\s*%?", stripped)
        if found:
            level = int(found.group(1))
            if level in self.sugar_levels:
                return Resolved(self.sugar_label(level), OK)
            return Resolved("", UNKNOWN,
                            f"this store's sugar levels are "
                            f"{_join([f'{n}%' for n in self.sugar_levels])}, not {level}%")

        for candidate in (stripped, key):
            if candidate in SUGAR_WORDS:
                level = SUGAR_WORDS[candidate]
                if level in self.sugar_levels:
                    return Resolved(self.sugar_label(level), OK)
        for word, level in SUGAR_WORDS.items():
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

        stripped = re.sub(r"\b(ice|iced|cubes)\b", " ", key)
        stripped = re.sub(r"\s+", " ", stripped).strip()
        if not stripped:
            # The cell said only "ice" — that is the store default, not a level.
            return Resolved("", BLANK)

        for candidate in (stripped, key):
            if candidate in ICE_WORDS:
                name = self._ice_for(ICE_WORDS[candidate])
                if name:
                    return Resolved(name, OK)
        for word, level in ICE_WORDS.items():
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
        if key in MILK_WORDS and MILK_WORDS[key] in self.milk:
            return Resolved(MILK_WORDS[key], OK)
        if key in MILK_DEFAULT:
            return Resolved("", BLANK)
        for word, value in MILK_WORDS.items():
            if re.search(rf"\b{re.escape(word)}\b", key) and value in self.milk:
                return Resolved(value, OK)
        for word in MILK_NOT_SOLD:
            if word in key:
                offer = _join(self.milk) if self.milk else "no milk alternatives"
                return Resolved("", UNAVAILABLE,
                                f"this store doesn't have {text.strip()} — it offers {offer}")
        return Resolved("", UNKNOWN, f"\"{text}\" isn't a milk option we recognise")

    def topping(self, text: str) -> Resolved:
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
            if key in TOPPING_AMBIGUOUS:
                choices = [name for name in TOPPING_AMBIGUOUS[key] if name in self.toppings]
                if len(choices) > 1:
                    return Resolved("", AMBIGUOUS,
                                    f"\"{raw}\" could be {_join(choices, 'or')} — say which")
                if len(choices) == 1:
                    return Resolved(choices[0], OK)
        for key in keys:
            if key in TOPPING_WORDS and TOPPING_WORDS[key] in self.toppings:
                return Resolved(TOPPING_WORDS[key], OK)
        for key in keys:
            for word, value in TOPPING_WORDS.items():
                if len(word) >= 4 and re.search(rf"\b{re.escape(word)}\b", key) \
                        and value in self.toppings:
                    return Resolved(value, OK)

        fuzzy = {norm(name): name for name in self._fuzzy_toppings}
        for key in keys:
            close = difflib.get_close_matches(key, list(fuzzy), n=1, cutoff=0.85)
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
            if score >= 0.5:
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
        """A light sanity check only — real menu matching is the matcher's job.

        Exact (case- and spacing-insensitive) hits resolve. Anything else comes
        back unresolved but with suggestions attached, because picking the wrong
        drink is the one mistake here that costs money — so the person decides.
        """
        key = norm(text)
        if not key:
            return Resolved("", BLANK)
        if not self.drinks:
            return Resolved("", BLANK)
        if key in self._drink_by_key:
            return Resolved(self._drink_by_key[key], OK)

        suggestions = self.drink_suggestions(text)
        missing = f"no drink called \"{text.strip()}\" on this store's menu"
        if suggestions:
            return Resolved("", UNKNOWN,
                            f"{missing} — did you mean {_join(suggestions[:2], 'or')}?",
                            suggestions)
        return Resolved("", UNKNOWN, missing)


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
    for text in row.toppings:
        resolved = options.topping(text)
        flag(resolved, "toppings")
        # Duplicates would order the same topping twice.
        if resolved.value and resolved.value not in toppings:
            toppings.append(resolved.value)
    canonical["toppings"] = toppings

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
