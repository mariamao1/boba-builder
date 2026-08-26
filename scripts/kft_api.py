"""Thin client for the Kung Fu Tea / Paytronix OrderExperience backend.

Discovered in Task 1. The site (kft.orderexperience.net) is a client-rendered
Nuxt 3 SPA; every screen is driven by JSON from https://oxb.pxsweb.com/.

CLIENT_KEY is not a secret. It is compiled into the public JS bundle that the
site serves to every anonymous visitor, and is required on every request.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://oxb.pxsweb.com/"
CLIENT_KEY = "49ace91d8c17daf4d13e61c05883ff3edbd02d1b"

# Kung Fu Tea's app record, resolved from api/v1/apps/get_by_slug/kft.
APP_ID = "64d157a1e0b9625e4d011c96"
APP_SLUG = "kft"

USER_AGENT = "boba-cart-builder/0.1 (+group order helper)"


class ApiError(RuntimeError):
    """An API or transport failure, without leaking a cart access token."""

    def __init__(self, message, status=None, retryable=False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def _error_message(payload, fallback):
    if isinstance(payload, dict) and "error" in payload:
        error = payload["error"]
        if isinstance(error, dict):
            return error.get("display_message") or error.get("message") or fallback
        return str(error)
    if isinstance(payload, dict):
        return payload.get("display_message") or payload.get("message") or fallback
    return fallback


def _request(path, params=None, data=None, method=None, timeout=45):
    params = {"key": CLIENT_KEY, **(params or {})}
    url = urllib.parse.urljoin(API_BASE, path) + "?" + urllib.parse.urlencode(params)

    body = None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        # The site posts multipart/form-data; urlencoded is accepted equivalently
        # for the flat + bracket-notation keys the cart endpoints expect.
        body = urllib.parse.urlencode(data, doseq=True).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode()) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        message = _error_message(payload, f"ordering service returned HTTP {exc.code}")
        raise ApiError(message, status=exc.code, retryable=exc.code >= 500) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", None) or str(exc) or "network error"
        raise ApiError(f"could not reach the ordering service: {reason}",
                       retryable=True) from exc

    # The quote endpoint currently responds 200 with an empty body after it has
    # refreshed the order totals.  Treat that as a successful empty result and
    # follow it with get_order(); older code tried to JSON-decode it and failed.
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError("ordering service returned an unreadable response") from exc

    if isinstance(payload, dict) and "error" in payload:
        raise ApiError(_error_message(payload, "ordering service rejected the request"))
    return payload


def get_app():
    """App-level config: branding, currency, and the full restaurant id list."""
    return _request(f"api/v1/apps/get_by_slug/{APP_SLUG}")


def list_stores():
    """All 441 store records, with hours, order types, timezone and lead times."""
    return _request(f"api/v1/apps/restaurants/{APP_ID}")


def get_menu(restaurant_id):
    """Full menu for one store: every item with its option groups, in one call."""
    return _request(f"api/v1/restaurants/{restaurant_id}/menu")


def find_stores(stores, query):
    """Case-insensitive match of `query` against name / city / state / zip."""
    q = query.lower().strip()
    hits = []
    for s in stores:
        haystack = " ".join(
            str(s.get(f) or "") for f in ("name", "address", "city", "state", "zip")
        ).lower()
        if q in haystack:
            hits.append(s)
    return hits


# --- Cart operations -------------------------------------------------------
#
# Verified live 2026-08-17: create -> add (with all modifiers) -> read -> quote
# -> clone. See task1-recommendation.txt.


def create_order(restaurant_id, order_type="takeout"):
    """POST api/v1/orders -> {order_id, token, ...}.

    `token` is the whole session: an anonymous bearer for this cart, passed back
    as ?access_token=. There is no login and no cookie.
    """
    return _request(
        "api/v1/orders",
        data={"restaurant_id": restaurant_id, "type": order_type},
        method="POST",
    )


def add_item(order_id, token, item_id, size="Regular", options=None, quantity=1,
             notes=None, for_name=None):
    """POST api/v1/orders/{order_id}/items.

    `size` is the PRICE TIER (item.prices[].name -- "Regular" for every Bay Ridge
    item), NOT the "Choose A Size" selection. Passing a size option name here is
    a 400 ("Invalid size: Large .5"). Size goes in `options` like any other
    group; use the item's `default_price_tier` from the menu snapshot.

    `options` maps group name -> list of {"name": str, "quantity": int,
    "level": str|None}, encoded as options[<group name>][<i>][<field>].
    Options are addressed by NAME, not by id -- menu options carry no ids.

    `for_name` populates the per-item "for" field. It persists on THIS order, but
    is stripped when the cart is cloned into the user's browser at handoff, so it
    is useful for debugging only -- the who-gets-what manifest must be produced
    separately. See task1-recommendation.txt sec. 4.
    """
    data = {"quantity": quantity}
    if size:
        data["size"] = size
    if notes:
        data["notes"] = notes
    if for_name:
        data["for"] = for_name

    for group_name, picks in (options or {}).items():
        for i, opt in enumerate(picks):
            prefix = f"options[{group_name}][{i}]"
            data[f"{prefix}[name]"] = opt["name"]
            data[f"{prefix}[quantity]"] = opt.get("quantity", 1)
            if opt.get("level"):
                data[f"{prefix}[level]"] = opt["level"]

    return _request(
        f"api/v1/orders/{order_id}/items",
        params={"access_token": token},
        data={**data, "id": item_id},
        method="POST",
    )


def get_order(order_id, token):
    """GET api/v1/orders/{id} -- the cart / review screen contents."""
    return _request(f"api/v1/orders/{order_id}", params={"access_token": token})


def quote_order(order_id, token):
    """Refresh totals before handoff.

    The live endpoint may return an empty 200 response.  Call get_order after
    this to read the refreshed subtotal, tax, fees and total.
    """
    return _request(
        f"api/v1/orders/{order_id}/quote", params={"access_token": token}
    )


# --- Handoff ---------------------------------------------------------------

STOREFRONT = "https://kft.orderexperience.net"


def handoff_url(restaurant_id, order_id):
    """The link to hand the user. Opening it clones our cart into their browser.

    The SPA reads ?order_id= on cold load and POSTs api/v1/orders with it, which
    server-side copies our line items into a NEW order owned by their session.
    They get an ordinary editable cart; we never share a token.

    Reusable and non-destructive -- each open makes a fresh clone and our source
    order is untouched. `restaurant_id` MUST match the store we priced against:
    cloning across stores is allowed and would silently reprice the cart.
    """
    return f"{STOREFRONT}/{restaurant_id}/menu?order_id={order_id}"


def clone_order(order_id, restaurant_id, order_type="takeout"):
    """Server-side clone -- exactly what the user's browser does on handoff.

    Useful for verifying a handoff without a browser. Needs no access_token.
    Note the clone DROPS per-item `for` and `notes`.
    """
    return _request(
        "api/v1/orders",
        data={"order_id": order_id, "restaurant_id": restaurant_id,
              "type": order_type},
        method="POST",
    )


def polite_sleep(seconds=0.4):
    time.sleep(seconds)
