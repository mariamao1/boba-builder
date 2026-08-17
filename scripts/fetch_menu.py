#!/usr/bin/env python3
"""Capture one store's full menu as structured data for Task 3.

    python3 scripts/fetch_menu.py --store 650c9c52d73592bc0e0bd5a7
    python3 scripts/fetch_menu.py --find "Philadelphia"
    python3 scripts/fetch_menu.py --list-stores

Writes data/menu-<restaurant_id>.json plus a human-readable summary.

The normalisation here exists because KFT's option groups are defined per item,
not globally: across two stores, 23 distinct size literals ("Large .5",
"Large 1", "Large.3", ...) collapse to four real labels, because the upcharge
is baked into the option name. Task 3 has to map free-text spreadsheet answers
onto whatever that specific drink offers, so each item carries its own resolved
size / sugar / ice / toppings / milk vocabulary.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import kft_api  # noqa: E402

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

# Group names seen across the whole menu, mapped to the four logical axes a
# group-order spreadsheet actually talks about.
AXIS_BY_GROUP = {
    "Choose A Size": "size",
    "Sugar Level": "sugar",
    "Ice Level": "ice",
    "Choose Topping(s)": "toppings",
    "Included Topping": "toppings",
    "Croffle Topping Options": "toppings",
    "Waffle/ Cake Topping": "toppings",
    # A fifth axis beyond the four the brief named, on 37 Bay Ridge items.
    # People do write "oat milk" in the spreadsheet, so it needs to be routable.
    "Milk Alternative": "milk",
}

# Size names carry the upcharge as a trailing token, in five inconsistent
# spellings seen across the real menu:
#     'Large .5'  'Large 1'  'Large 1.5'  'Large 0.45'  'Large.3'  'Medium'
# so the separator may be a space, nothing at all, or a bare leading dot.
SIZE_SUFFIX = re.compile(r"^(?P<base>.*?)\s*(?P<amount>\d*\.?\d+)$")


def split_size_name(name, price=None):
    """Return (canonical_label, literal_name). Literal is what the API wants.

    Only strips the trailing token when it genuinely matches the option's own
    `price`, so a size legitimately named with a digit is left alone. Falls
    back to the untouched name whenever we can't prove the suffix is a price.
    """
    name = name.strip()
    m = SIZE_SUFFIX.match(name)
    if not m or not m.group("base"):
        return name, name

    try:
        parsed = float(m.group("amount"))
    except ValueError:
        return name, name

    # '.5' and '5' both mean 50c; a bare integer means whole dollars.
    if price is not None and abs(parsed - float(price)) > 0.005:
        return name, name

    return m.group("base").strip(" .") or name, name


def normalise_option(opt):
    return {
        "name": opt["name"],
        "price": opt.get("price") or 0,
        "calories": opt.get("cal"),
        "is_default": bool(opt.get("is_default")),
        "is_disabled": bool(opt.get("is_disabled")),
    }


def normalise_group(group):
    name = group["name"]
    axis = AXIS_BY_GROUP.get(name, "other")
    options = [normalise_option(o) for o in group.get("options") or []]

    entry = {
        "group_name": name,          # exact string the cart payload keys on
        "axis": axis,
        "required": (group.get("min") or 0) > 0,
        "min": group.get("min") or 0,
        # max 0 means unlimited, not "none allowed"
        "max": group.get("max") or None,
        "multiselect": bool(group.get("multiselect")),
        "allows_quantity": bool(group.get("quantities")),
        "free_choices": group.get("free_opts") or 0,
        "options": options,
    }

    if axis == "size":
        entry["canonical"] = {
            split_size_name(o["name"], o["price"])[0]: o["name"] for o in options
        }
    return entry


def normalise_item(item):
    groups = [normalise_group(g) for g in item.get("option_groups") or []]
    by_axis = {}
    for g in groups:
        by_axis.setdefault(g["axis"], []).append(g["group_name"])

    return {
        "id": item["id"],
        "name": item["name"],
        "category": item.get("category"),
        "description": (item.get("description") or "").strip() or None,
        "base_price": float(item["display_price"]) if item.get("display_price") else None,
        # The cart's `size` field is a PRICE TIER (item.prices[].name), not the
        # "Choose A Size" option group -- posting "Large .7" as `size` is a 400.
        # Every Bay Ridge item currently has the single tier "Regular", but it is
        # captured per item rather than assumed, because it is a required field.
        "price_tiers": [p["name"] for p in item.get("prices") or []],
        "default_price_tier": next(
            (p["name"] for p in item.get("prices") or [] if p.get("is_default")),
            (item.get("prices") or [{}])[0].get("name"),
        ),
        "available": bool(item.get("can_order")) and not item.get("is_sold_out"),
        "sold_out": bool(item.get("is_sold_out")),
        # A drink with no size group is fixed-format (cans, food, desserts).
        "has_size": "size" in by_axis,
        "has_sugar": "sugar" in by_axis,
        "has_ice": "ice" in by_axis,
        "has_milk_alternative": "milk" in by_axis,
        "option_groups": groups,
    }


def build_snapshot(restaurant_id, store_record=None):
    raw = kft_api.get_menu(restaurant_id)
    items = [normalise_item(i) for i in raw["menu"]]

    categories = []
    for node in raw.get("hierarchy") or []:
        for child in node.get("contents") or []:
            if child.get("type") == "category":
                categories.append({"id": child.get("id"), "name": child.get("name")})

    snapshot = {
        "source": "https://oxb.pxsweb.com/api/v1/restaurants/%s/menu" % restaurant_id,
        "restaurant_id": restaurant_id,
        "restaurant_name": raw.get("name"),
        "can_order_now": raw.get("can_order"),
        "category_order": categories,
        "item_count": len(items),
        "items": items,
    }
    if store_record:
        snapshot["store"] = {
            k: store_record.get(k)
            for k in ("name", "address", "city", "state", "zip", "phone", "tz",
                      "takeout", "delivery", "dinein", "curbside", "lead",
                      "accepting_orders", "allow_notes", "group_orders", "hours")
        }
    return snapshot


def summarise(snapshot):
    items = snapshot["items"]
    out = [
        f"{snapshot['restaurant_name']}  ({snapshot['restaurant_id']})",
        f"{len(items)} items, orderable right now: {snapshot['can_order_now']}",
        "",
    ]
    by_cat = {}
    for it in items:
        by_cat.setdefault(it["category"], []).append(it)
    for cat, group in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
        out.append(f"  {cat or '(uncategorised)'}: {len(group)}")

    sizes, sugars, ices = set(), set(), set()
    for it in items:
        for g in it["option_groups"]:
            names = tuple(o["name"] for o in g["options"])
            if g["axis"] == "size":
                sizes.add(names)
            elif g["axis"] == "sugar":
                sugars.add(names)
            elif g["axis"] == "ice":
                ices.add(names)
    out += [
        "",
        f"  distinct size option-sets:  {len(sizes)}",
        f"  distinct sugar option-sets: {len(sugars)}",
        f"  distinct ice option-sets:   {len(ices)}",
        "  -> modifier vocabulary is per-item; resolve against the item, "
        "never against a global list.",
    ]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", help="restaurant id to snapshot")
    ap.add_argument("--find", help="search stores by name/city/state/zip")
    ap.add_argument("--list-stores", action="store_true")
    args = ap.parse_args()

    if args.find or args.list_stores:
        stores = kft_api.list_stores()
        hits = kft_api.find_stores(stores, args.find) if args.find else stores
        visible = [s for s in hits if not s.get("hide_from_picker")]
        print(f"{len(visible)} store(s):\n")
        for s in visible[:60]:
            flags = ",".join(
                f for f in ("takeout", "delivery", "dinein") if s.get(f)
            )
            print(f"  {s['id']}  {s['name'][:34]:36s} {s.get('city')}, "
                  f"{s.get('state')} {s.get('zip')}  [{flags}]")
        if len(visible) > 60:
            print(f"  ... and {len(visible) - 60} more")
        return

    if not args.store:
        ap.error("pass --store <id>, or --find/--list-stores to look one up")

    stores = kft_api.list_stores()
    record = next((s for s in stores if s["id"] == args.store), None)

    snapshot = build_snapshot(args.store, record)
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"menu-{args.store}.json"
    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))

    print(summarise(snapshot))
    print(f"\nwrote {path.relative_to(path.parent.parent)} "
          f"({path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
