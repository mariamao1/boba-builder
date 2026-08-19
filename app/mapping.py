"""The vocabulary configuration, and the check that it still fits the menu.

    python3 -m app.mapping            # is the mapping still true of the menu?

Everything about "what people write" lives in `data/mapping.json`, not in code:
size and sugar synonyms, milk alternatives this store doesn't carry, topping
families that are too vague to guess between, drink aliases, and the per-axis
policy the matcher applies when an item doesn't offer what was asked for.

WHY A FILE
----------
Task 1 §6: options are addressed by their exact display name, so a menu rename
silently breaks the cart. The menu will change — new toppings, renamed drinks,
a store that spells "Milk Alternative" differently. When it does, the fix should
be an edit to a JSON file and a re-run of the check below, not a code change.

    BOBA_MAPPING=/path/to/mine.json    an overlay merged over the shipped file

The overlay is merged one section at a time: objects update key by key (so you
can add three synonyms without restating the table), lists replace wholesale.

WHAT THE CHECK IS FOR
---------------------
Half of this file names real menu strings — `"oreo": "OREO®"` is only useful
while the store still calls it that. `check()` reads the current snapshot and
reports every mapping target that no longer exists, so a stale alias surfaces
as a line of output instead of as a drink nobody ordered.
"""

from __future__ import annotations

import functools
import json
import os
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_PATH = DATA_DIR / "mapping.json"
OVERLAY_ENV = "BOBA_MAPPING"

# Sections the rest of the app reads. Listed so a typo'd section name in the
# file is reported rather than silently ignored.
SECTIONS = ("size", "sugar", "ice", "milk", "toppings", "drinks", "matching")


def _merge(base: dict, overlay: dict) -> dict:
    """Overlay onto base: dicts merge key by key, everything else replaces."""
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Mapping:
    """One loaded configuration. `problems` is never fatal — it is reported."""

    def __init__(self, data: dict | None = None, problems: list[str] | None = None):
        self.data = data or {}
        self.problems = list(problems or [])

        size = self.section("size")
        self.size_words: dict = size.get("words", {})
        self.size_nearest: dict = {key: tuple(value)
                                   for key, value in (size.get("nearest") or {}).items()
                                   if len(value) == 2}
        self.size_default: set = set(size.get("means_default") or ())
        self.size_temperature: dict = size.get("temperature", {})

        sugar = self.section("sugar")
        self.sugar_words: dict = {key: int(value)
                                  for key, value in (sugar.get("words") or {}).items()}
        self.sugar_noise: tuple = tuple(sugar.get("noise") or ())

        ice = self.section("ice")
        self.ice_words: dict = ice.get("words", {})
        self.ice_noise: tuple = tuple(ice.get("noise") or ())

        milk = self.section("milk")
        self.milk_words: dict = milk.get("words", {})
        self.milk_default: set = set(milk.get("means_default") or ())
        self.milk_not_sold: tuple = tuple(milk.get("not_sold") or ())

        toppings = self.section("toppings")
        self.topping_words: dict = toppings.get("words", {})
        self.topping_ambiguous: dict = {key: tuple(value) for key, value
                                        in (toppings.get("ambiguous") or {}).items()}
        self.topping_cutoff: float = float(toppings.get("fuzzy_cutoff") or 0.85)

        drinks = self.section("drinks")
        self.drink_aliases: dict = drinks.get("aliases", {})
        self.drink_exclude: tuple = tuple(drinks.get("exclude") or ())
        self.drink_cutoff: float = float(drinks.get("suggestion_cutoff") or 0.5)

        matching = self.section("matching")
        self.axis_groups: dict = matching.get("axis_groups", {})
        # Canonical values that mean "the recipe already does this" — asking for
        # them on an item that doesn't list them is not an unavailable option,
        # it is an instruction to send no modifier (Task 1 §5).
        self.assumed_defaults: dict = {axis: [value.lower() for value in values]
                                       for axis, values
                                       in (matching.get("assume_default_when_missing") or {}).items()}
        # What to pick when a group is required and nobody chose anything.
        self.required_fallback: dict = matching.get("required_group_fallback", {})
        self.menu_stale_days: int = int(matching.get("menu_stale_days") or 14)

    def section(self, name: str) -> dict:
        value = self.data.get(name)
        return value if isinstance(value, dict) else {}


def _read(path: Path) -> tuple[dict, list[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, [f"{path.name} is missing — falling back to exact matches only"]
    except json.JSONDecodeError as exc:
        return {}, [f"{path.name} isn't valid JSON ({exc.msg}, line {exc.lineno})"]
    except OSError as exc:
        return {}, [f"{path.name} couldn't be read ({exc.strerror})"]
    if not isinstance(data, dict):
        return {}, [f"{path.name} should be a JSON object"]
    return data, []


def load_from(path: Path | str, overlay: Path | str | None = None) -> Mapping:
    data, problems = _read(Path(path))
    if overlay:
        extra, extra_problems = _read(Path(overlay))
        problems += [f"{OVERLAY_ENV}: {problem}" for problem in extra_problems]
        data = _merge(data, extra)
    unknown = [key for key in data
               if key not in SECTIONS and key not in ("version", "notes")]
    problems += [f"unknown section \"{key}\" in the mapping — ignored" for key in unknown]
    return Mapping(data, problems)


@functools.lru_cache(maxsize=1)
def load() -> Mapping:
    """The shipped mapping, plus any overlay. Cached; call reload() after edits."""
    return load_from(CONFIG_PATH, os.environ.get(OVERLAY_ENV) or None)


def reload() -> Mapping:
    load.cache_clear()
    return load()


# --- does the mapping still fit the menu? -----------------------------------


def check(config: Mapping | None = None, hints: dict | None = None) -> list[str]:
    """Every mapping target that the current menu no longer offers.

    An empty list means the vocabulary and the snapshot agree. Anything here is
    a synonym pointing at a name the store has stopped using — harmless in the
    sense that it can only fail to match, but it means somebody's "oreo" now
    goes unmatched, so it should be seen.
    """
    config = config or load()
    if hints is None:
        from . import template  # local import: mapping must load without the data files
        hints = template.menu_hints()

    problems = list(config.problems)
    if not hints.get("drinks"):
        return problems + ["no menu snapshot, so the mapping couldn't be checked"]

    def missing(label: str, values, allowed, extra: str = "") -> None:
        allowed = {str(name).lower() for name in allowed}
        for value in values:
            if value and str(value).lower() not in allowed:
                problems.append(f"{label} -> \"{value}\"{extra} is not on the menu any more")

    missing("size", set(config.size_words.values()), hints.get("sizes") or ())
    missing("size", {value for value, _why in config.size_nearest.values()},
            hints.get("sizes") or ())
    missing("size (temperature)", {value for value in config.size_temperature.values() if value},
            hints.get("sizes") or ())

    levels = {int(part.rstrip("%")) for part in
              (name.split()[-1] for name in hints.get("sugar") or [])
              if part.rstrip("%").isdigit()}
    for level in sorted(set(config.sugar_words.values())):
        if level not in levels:
            problems.append(f"sugar -> {level}% is not on the menu any more")

    ice_names = {name.lower() for name in hints.get("ice") or ()}
    for level in sorted(set(config.ice_words.values())):
        if not any(name.startswith(level) for name in ice_names):
            problems.append(f"ice -> \"{level}\" has no matching option any more")

    missing("milk", set(config.milk_words.values()), hints.get("milk") or ())
    missing("topping", set(config.topping_words.values()), hints.get("toppings") or ())
    for key, choices in config.topping_ambiguous.items():
        missing(f"topping \"{key}\"", choices, hints.get("toppings") or ())
    missing("drink alias", set(config.drink_aliases.values()), hints.get("drinks") or ())

    for axis, wanted in config.required_fallback.items():
        pool = {"size": hints.get("sizes"), "sugar": hints.get("sugar"),
                "ice": hints.get("ice")}.get(axis) or []
        if wanted and not any(str(name).lower() in {p.lower() for p in pool} for name in wanted):
            problems.append(f"required-group fallback for {axis} lists none of this "
                            f"store's options ({', '.join(wanted)})")
    return problems


def main(argv=None) -> int:
    from . import template

    config = reload()
    hints = template.menu_hints()
    problems = check(config, hints)

    store = hints.get("store") or "no store snapshot"
    print(f"mapping: {CONFIG_PATH.relative_to(CONFIG_PATH.parent.parent)}  "
          f"(version {config.data.get('version', '?')})")
    overlay = os.environ.get(OVERLAY_ENV)
    if overlay:
        print(f"overlay: {overlay}")
    print(f"menu:    {store} — {hints.get('item_count', 0)} items\n")

    counts = [
        ("size words", len(config.size_words)),
        ("sugar words", len(config.sugar_words)),
        ("ice words", len(config.ice_words)),
        ("milk words", len(config.milk_words) + len(config.milk_not_sold)),
        ("topping words", len(config.topping_words)),
        ("ambiguous families", len(config.topping_ambiguous)),
        ("drink aliases", len(config.drink_aliases)),
    ]
    for label, count in counts:
        print(f"  {count:4}  {label}")

    if not problems:
        print("\nevery mapping target still exists on this store's menu.")
        return 0
    print(f"\n{len(problems)} thing{'s' if len(problems) != 1 else ''} to fix:")
    for problem in problems:
        print(f"  - {problem}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
