"""The store's menu as objects: items, and what each one can actually be ordered with.

`app/options.py` resolves the user's words to a store-level label — `Large`,
`50%`, `Boba`. This module is the other half of the pair: it knows what each
individual item offers, which is where those labels turn into the exact strings
the cart API wants (Task 1 §5 — `Large` is `Large .7` on one drink and
`Large 1` on the next, and the upcharge is baked into the name).

Nothing here decides anything. It answers questions:

    menu.find("wintermelon lemonade")     -> Winter Melon Lemonade, matched how
    item.literal("size", "Large")         -> "Large .7"
    item.literal("sugar", "50%")          -> "Half S 50%"
    item.options("toppings")              -> what this drink can have on it

The judgement — what to do when the answer is None — belongs to app/matcher.py.

Two menu facts this module exists to hide:

* **Item names are not unique.** 14 Bay Ridge names map to two items each,
  cross-listed into a promotional category with identical prices and options.
  `find()` breaks the tie on the lowest id so the same sheet builds the same
  cart twice running.
* **Group names are per store.** Bay Ridge has a stray `Milk Tea` group;
  Philadelphia has `Tajin Option`. Which group is which axis comes from
  `data/mapping.json`, so a store with a differently-named group is a config
  edit rather than a code change.
"""

from __future__ import annotations

import datetime as _dt
import difflib
import functools
import json
import re
from pathlib import Path

from . import mapping

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TARGET_STORE = "650c9c3cd73592bc0e0bd50a"  # 5th Ave, Bk — Task 1's target store

# Same rule as scripts/fetch_menu.py: a trailing number on a size name is the
# upcharge, not part of the name — but only when it matches the option's price.
_SIZE_SUFFIX = re.compile(r"^(?P<base>.*?)\s*(?P<amount>\d*\.?\d+)$")


def snapshot_path() -> Path | None:
    path = DATA_DIR / f"menu-{TARGET_STORE}.json"
    if path.exists():
        return path
    candidates = sorted(DATA_DIR.glob("menu-*.json"))
    return candidates[0] if candidates else None


@functools.lru_cache(maxsize=1)
def snapshot() -> dict:
    """The raw menu capture. `{}` when there isn't one — never an exception."""
    path = snapshot_path()
    if path is None:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def captured_at() -> _dt.date | None:
    """When the snapshot was taken, from the file's own timestamp.

    The capture doesn't record a date inside itself, and the mtime is close
    enough for the only question anyone asks of it: is this too old to trust?
    """
    path = snapshot_path()
    if path is None:
        return None
    try:
        return _dt.date.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None


def size_label(name: str, price=None) -> str:
    """"Large .7" -> "Large". Leaves a name alone unless the suffix is the price."""
    name = (name or "").strip()
    found = _SIZE_SUFFIX.match(name)
    if not found or not found.group("base"):
        return name
    try:
        amount = float(found.group("amount"))
    except ValueError:
        return name
    if price is not None and abs(amount - float(price)) > 0.005:
        return name
    return found.group("base").strip(" .") or name


def norm(text) -> str:
    """Comparison key: lowercase, punctuation to spaces, percent kept.

    Percent survives because it is the whole meaning of a sugar level, and the
    ® in OREO® has to go for "oreo" to match. app/options.py re-exports this
    rather than keeping a second copy — the two modules must agree on when two
    strings are the same string.
    """
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%]+", " ", str(text or "").lower())).strip()


def squash(text) -> str:
    """The same key with the spaces gone: "wintermelon" == "winter melon"."""
    return norm(text).replace(" ", "")


class MenuGroup:
    """One option group on one item, with the axis it belongs to."""

    __slots__ = ("item_name", "group_name", "axis", "required", "min", "max",
                 "multiselect", "allows_quantity", "options", "_canonical")

    def __init__(self, item_name: str, data: dict, config: mapping.Mapping):
        self.item_name = item_name
        self.group_name = data.get("group_name") or ""
        # The snapshot records an axis; the config can re-route a group the
        # capture didn't recognise, without re-fetching the menu.
        self.axis = config.axis_groups.get(self.group_name) or data.get("axis") or "other"
        self.min = data.get("min") or 0
        self.max = data.get("max")
        self.required = bool(data.get("required")) or self.min > 0
        self.multiselect = bool(data.get("multiselect"))
        self.allows_quantity = bool(data.get("allows_quantity"))
        self.options = [option for option in data.get("options") or []
                        if not option.get("is_disabled")]
        self._canonical = data.get("canonical")

    @property
    def names(self) -> list[str]:
        return [option["name"] for option in self.options]

    def option(self, name: str) -> dict | None:
        key = norm(name)
        for option in self.options:
            if norm(option["name"]) == key:
                return option
        return None

    def canonical(self) -> dict[str, str]:
        """{store-level label -> the literal to post}.

        Sizes carry a map from the capture ("Large" -> "Large .7"); sugar is
        keyed on its percentage, because the store's own wording is inconsistent
        English — `Regular Sugar 100%` beside `Less S 70%` (Task 1 §5). Every
        other axis already reads as itself.
        """
        if self.axis == "sugar":
            labels = {}
            for option in self.options:
                found = re.search(r"(\d+)\s*%", option["name"])
                labels[f"{found.group(1)}%" if found else option["name"]] = option["name"]
            return labels
        if isinstance(self._canonical, dict) and self._canonical:
            return self._canonical
        if self.axis != "size":
            return {name: name for name in self.names}
        return {size_label(option["name"], option.get("price")): option["name"]
                for option in self.options}

    def label_for(self, option_name: str) -> str:
        """The literal back to its store-level label: "Less S 70%" -> "70%"."""
        for label, literal in self.canonical().items():
            if literal == option_name:
                return label
        return option_name

    def default(self, prefer: list[str] | None = None) -> dict | None:
        """What to send when this group is required and nobody chose anything.

        The menu's own default first, then whatever the mapping says this store
        normally serves, then the cheapest option — and never a "No ..." removal
        option if there is anything else, because a required group defaulting to
        "without" is the one guess that changes the drink.
        """
        for option in self.options:
            if option.get("is_default"):
                return option
        labels = self.canonical()
        for wanted in prefer or ():
            key = norm(wanted)
            for option in self.options:
                if norm(option["name"]) == key:
                    return option
            for label, literal in labels.items():
                if norm(label) == key:
                    return self.option(literal)
        positive = [option for option in self.options
                    if not norm(option["name"]).startswith("no ")] or self.options
        return min(positive, key=lambda option: (option.get("price") or 0),
                   default=None)


class MenuItem:
    """One orderable thing, and the vocabulary it personally offers."""

    def __init__(self, data: dict, config: mapping.Mapping):
        self.id = data.get("id") or ""
        self.name = data.get("name") or ""
        self.category = data.get("category") or ""
        self.description = data.get("description") or ""
        self.base_price = data.get("base_price")
        self.available = bool(data.get("available")) and not data.get("sold_out")
        self.sold_out = bool(data.get("sold_out"))
        # Task 1 §6: the cart's `size` field is the PRICE TIER, not the size
        # option. Posting "Large .5" there is a 400.
        self.price_tier = (data.get("default_price_tier")
                           or (data.get("price_tiers") or ["Regular"])[0])
        self.groups = [MenuGroup(self.name, group, config)
                       for group in data.get("option_groups") or []]

    def __repr__(self) -> str:  # for test failure output
        return f"MenuItem({self.name!r})"

    def groups_for(self, axis: str) -> list[MenuGroup]:
        return [group for group in self.groups if group.axis == axis]

    def has(self, axis: str) -> bool:
        return any(group.axis == axis for group in self.groups)

    def options(self, axis: str) -> list[str]:
        """Every option name on this item for one axis, in menu order."""
        names: list[str] = []
        for group in self.groups_for(axis):
            for name in group.names:
                if name not in names:
                    names.append(name)
        return names

    def labels(self, axis: str) -> list[str]:
        """The same list, as store-level labels ("Large", not "Large .7")."""
        labels: list[str] = []
        for group in self.groups_for(axis):
            for label in group.canonical():
                if label not in labels:
                    labels.append(label)
        return labels

    def literal(self, axis: str, label: str) -> tuple[MenuGroup, dict] | None:
        """A canonical label -> (group, option) on this item, or None.

        Sugar is matched on its percentage rather than its wording, because the
        store's own names are inconsistent English: `Regular Sugar 100%` beside
        `Less S 70%` (Task 1 §5).
        """
        if not label:
            return None
        if axis == "sugar":
            return self._sugar(label)
        key = norm(label)
        for group in self.groups_for(axis):
            for candidate, literal in group.canonical().items():
                if norm(candidate) == key:
                    option = group.option(literal)
                    if option:
                        return group, option
            option = group.option(label)
            if option:
                return group, option
        return None

    def _sugar(self, label: str) -> tuple[MenuGroup, dict] | None:
        wanted = re.search(r"(\d+)", str(label))
        if not wanted:
            return None
        percent = wanted.group(1)
        for group in self.groups_for("sugar"):
            for option in group.options:
                found = re.search(r"(\d+)\s*%", option["name"])
                if found and found.group(1) == percent:
                    return group, option
        return None

    def sugar_levels(self) -> list[int]:
        levels = []
        for name in self.options("sugar"):
            found = re.search(r"(\d+)\s*%", name)
            if found and int(found.group(1)) not in levels:
                levels.append(int(found.group(1)))
        return sorted(levels)

    def required_groups(self) -> list[MenuGroup]:
        return [group for group in self.groups if group.required]

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "category": self.category,
                "price": self.base_price, "price_tier": self.price_tier}


class Match:
    """The outcome of looking a drink name up. Falsy when nothing was found."""

    __slots__ = ("item", "how", "choices")

    #: how the name was found — worst to best, all of them safe to apply
    EXACT = "exact"            # the sheet said exactly what the menu says
    NORMALISED = "normalised"  # same name, different spacing/case/punctuation
    ALIAS = "alias"            # a name from data/mapping.json
    NONE = "none"
    AMBIGUOUS = "ambiguous"    # several different drinks, equally close

    def __init__(self, item: MenuItem | None = None, how: str = NONE,
                 choices: list[str] | None = None):
        self.item = item
        self.how = how
        self.choices = choices or []

    def __bool__(self) -> bool:
        return self.item is not None

    def __repr__(self) -> str:
        return f"Match({self.item.name if self.item else None!r}, {self.how!r})"


class StoreMenu:
    """Everything one store sells, indexed for lookup."""

    def __init__(self, data: dict | None = None, config: mapping.Mapping | None = None):
        data = data or {}
        self.config = config or mapping.load()
        self.restaurant_id = data.get("restaurant_id") or ""
        self.store = data.get("restaurant_name") or (data.get("store") or {}).get("name") or ""
        self.items = [MenuItem(item, self.config) for item in data.get("items") or []]

        excluded = tuple(word.lower() for word in self.config.drink_exclude)
        self.drinks = [item for item in self.items
                       if item.available
                       and not any(word in item.name.lower() for word in excluded)]

        # Name -> item. Duplicates resolve to the lowest id so the same sheet
        # produces the same cart twice running (Task 1 §5).
        self._by_name: dict[str, MenuItem] = {}
        self._by_squash: dict[str, list[MenuItem]] = {}
        for item in sorted(self.drinks, key=lambda item: item.id):
            self._by_name.setdefault(norm(item.name), item)
            self._by_squash.setdefault(squash(item.name), []).append(item)
        self._aliases = {norm(key): value
                         for key, value in self.config.drink_aliases.items()}
        # Everything, including what's sold out and what we never order (gift
        # cards). Only used to tell someone *why* their drink isn't offered.
        self._any_by_name: dict[str, MenuItem] = {}
        for item in sorted(self.items, key=lambda item: item.id):
            self._any_by_name.setdefault(norm(item.name), item)
            self._any_by_name.setdefault(squash(item.name), item)

    def __bool__(self) -> bool:
        return bool(self.items)

    def __len__(self) -> int:
        return len(self.items)

    @property
    def names(self) -> list[str]:
        seen: dict[str, None] = {}
        for item in self.drinks:
            seen.setdefault(item.name, None)
        return list(seen)

    def by_id(self, item_id: str) -> MenuItem | None:
        return next((item for item in self.items if item.id == item_id), None)

    def find(self, text: str) -> Match:
        """A drink name -> the item to order, and how sure we are.

        Only three tiers get here, and all three are the *same name*: exactly as
        written, the same name spelled differently, or a name the mapping file
        says is this drink. Anything looser is a guess about what somebody wants
        to drink, and app/matcher.py offers those rather than applying them.
        """
        key = norm(text)
        if not key or not self.drinks:
            return Match()

        exact = self._by_name.get(key)
        if exact:
            return Match(exact, Match.EXACT)

        alias = self._aliases.get(key)
        if alias:
            target = self._by_name.get(norm(alias))
            if target:
                return Match(target, Match.ALIAS)

        # "Wintermelon Lemonade" and "Winter Melon Lemonade" are one drink typed
        # two ways, not a judgement call — unless the squashed key hits two
        # genuinely different names, which would be.
        candidates = self._by_squash.get(squash(text)) or []
        distinct = {item.name for item in candidates}
        if len(distinct) == 1:
            return Match(candidates[0], Match.NORMALISED)
        if len(distinct) > 1:
            return Match(None, Match.AMBIGUOUS, sorted(distinct))
        return Match()

    def unorderable(self, text: str) -> MenuItem | None:
        """An item this store lists but we won't order — sold out, or a gift card.

        `find()` never returns these. This exists so the reason can be "Taro
        Slush is sold out today" instead of "no drink called Taro Slush".
        """
        if not text:
            return None
        item = self._any_by_name.get(norm(text)) or self._any_by_name.get(squash(text))
        if item is None or item in self.drinks:
            return None
        return item

    def suggestions(self, text: str, limit: int = 5) -> list[str]:
        """Near misses to offer a person, best first. Never applied on our own."""
        key = norm(text)
        if not key or not self.drinks:
            return []
        wanted = {word for word in key.split() if len(word) >= 3}
        cutoff = self.config.drink_cutoff
        scored: list[tuple[float, str]] = []
        for name in self.names:
            candidate = norm(name)
            score = difflib.SequenceMatcher(None, key, candidate).ratio()
            if wanted:
                shared = wanted & set(candidate.split())
                score = max(score, 0.9 * len(shared) / len(wanted))
            if score >= cutoff:
                scored.append((score, name))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [name for _score, name in scored[:limit]]


@functools.lru_cache(maxsize=1)
def store_menu() -> StoreMenu:
    """The target store's menu. Cached — the snapshot doesn't change under us."""
    return StoreMenu(snapshot(), mapping.load())


def reload() -> StoreMenu:
    snapshot.cache_clear()
    store_menu.cache_clear()
    return store_menu()
