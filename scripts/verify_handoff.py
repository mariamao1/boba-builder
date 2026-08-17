"""Task 1 cart-handoff verification, end to end. THE ONE OPEN ITEM FROM TASK 1.

Run this as soon as a machine with a working browser is available; it is the
only part of the handoff finding that was never executed. Chromium refuses to
start in the Task 1 environment (Mach port rendezvous blocked by endpoint
security), so the client-side leg is documented from the JS bundle only.

Expected result: the clean profile ends up with an "orders" localStorage entry
whose order id is DIFFERENT from the built cart (clone-on-open), and whose
contents and subtotal match.

Builds a cart with the API, then opens the candidate handoff URL in a CLEAN
browser profile (empty localStorage - i.e. a different person's browser) and
checks whether the cart materialises.
"""
import json, shutil, sys, tempfile, time, urllib.parse, urllib.request

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import cdp

BASE = "https://oxb.pxsweb.com/"
KEY = "49ace91d8c17daf4d13e61c05883ff3edbd02d1b"
STORE = "650c9c3cd73592bc0e0bd50a"


def api(path, data=None, params=None, method=None):
    p = {"key": KEY, **(params or {})}
    body = urllib.parse.urlencode(data, doseq=True).encode() if data else None
    h = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    if body:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    r = urllib.request.Request(BASE + path + "?" + urllib.parse.urlencode(p),
                               data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=45) as x:
            return x.status, json.loads(x.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


# ---- 1. build a cart the way the automation would ------------------------
_, order = api("api/v1/orders", {"restaurant_id": STORE, "type": "takeout"},
               method="POST")
OID, TOK = order["order_id"], order["token"]
print(f"built cart {OID}")

DRINKS = [
    # (item_id, size-option, sugar, ice, topping)
    ("68306a89068b280679072a15", "Large .5", "Half S 50%", "Less Ice",
     "Brown Sugar Wow Boba"),
    ("6a2467712747ea36450829ea", "Medium", "No S 0%", None, "Pudding"),
]
for item_id, size, sugar, ice, topping in DRINKS:
    d = {"id": item_id, "quantity": 1, "size": "Regular",
         "options[Choose A Size][0][name]": size,
         "options[Choose A Size][0][quantity]": 1,
         "options[Sugar Level][0][name]": sugar,
         "options[Sugar Level][0][quantity]": 1,
         "options[Choose Topping(s)][0][name]": topping,
         "options[Choose Topping(s)][0][quantity]": 1}
    if ice:
        d["options[Ice Level][0][name]"] = ice
        d["options[Ice Level][0][quantity]"] = 1
    s, r = api(f"api/v1/orders/{OID}/items", d, {"access_token": TOK}, "POST")
    print(f"  add {r.get('name') if isinstance(r, dict) else r}: {s}")

_, built = api(f"api/v1/orders/{OID}")
print("server-side cart:", [(i["name"], i["total_price"]) for i in built["items"]],
      "subtotal", built["subtotal"])

HANDOFF = f"https://kft.orderexperience.net/{STORE}/menu?order_id={OID}"
print("\nhandoff URL:", HANDOFF)

# ---- 2. open it in a clean browser (nobody else's localStorage) ----------
profile = tempfile.mkdtemp(prefix="kft-clean-")
proc = cdp.launch(profile)
try:
    tab = cdp.new_tab("about:blank")
    ws = cdp.WS(tab["webSocketDebuggerUrl"])
    ws.call("Runtime.enable")
    ws.call("Page.enable")

    ws.call("Page.navigate", {"url": HANDOFF})
    time.sleep(25)  # SPA boot + clone round-trip

    state = cdp.evaluate(ws, """(() => {
      const ls = {};
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        ls[k] = localStorage.getItem(k);
      }
      return JSON.stringify({
        url: location.href,
        orders: ls['orders'] || null,
        bodyHasCart: /cart|Cart/.test(document.body.innerText),
      });
    })()""")
    st = json.loads(state) if isinstance(state, str) else state
    print("\n--- browser after load ---")
    print("final url :", st.get("url"))
    print("localStorage['orders'] :", st.get("orders"))

    adopted = None
    if st.get("orders"):
        arr = json.loads(st["orders"])
        for e in arr:
            if e.get("restaurantId") == STORE and not e.get("isSubmitted"):
                adopted = e
    print("adopted entry:", adopted)

    if adopted:
        newid = adopted["id"]
        _, got = api(f"api/v1/orders/{newid}")
        print(f"\nbrowser's own order {newid}:")
        for i in got.get("items", []):
            print("   ", i["name"], i["total_price"],
                  [o["name"] for o in i.get("options", [])])
        print("   subtotal", got.get("subtotal"), "total", got.get("total_amount"))
        print("\nSAME ORDER ID AS BUILT?", newid == OID)
    else:
        print("\nNo cart adopted - handoff via this URL FAILED")

    ws.call("Page.captureScreenshot", {"format": "png"})
finally:
    proc.terminate()
    shutil.rmtree(profile, ignore_errors=True)
