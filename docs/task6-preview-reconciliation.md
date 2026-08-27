# Task 6 — In-App Preview & Reconciliation

The preview at `/preview/<run_id>` is the checkpoint between import and the
external Kung Fu Tea cart. It shows every parsed row, including rows that cannot
currently be ordered, alongside the exact menu item and modifiers that the cart
builder will use.

## Before handoff

- The summary keeps separate counts for requested drinks, mapped drinks, ready
  rows, and rows needing attention, plus the current estimated subtotal.
- Warning, error, unmapped, and dropped-option rows are highlighted. The raw
  spreadsheet value remains visible wherever it differs from the resolved menu
  value.
- **Review & edit** allows changes to name, drink, size, sugar, ice, milk,
  toppings, quantity, and notes. Each change is saved and re-run through the
  normal resolver and matcher, so counts, prices, warnings, and available option
  lists update together.
- Saves are serialized, and cart creation waits for the last edit. Clicking the
  cart button immediately after leaving a field therefore cannot build from the
  stale value.
- Unresolved rows do not disappear. The user may fix them in place or continue;
  anything that cannot be added is named in the reconciliation.

## After cart creation

Cart creation opens a distinct third-step view rather than appending another
panel below the order. Its single top back control returns to the saved order
review; from there the same control returns to import. The cart handoff view
includes a reconciliation with:

- requested, placed, and not-placed drink counts;
- a person-by-person manifest of every confirmed cart line and its modifiers;
- every failed or skipped row, its quantity, and the reason;
- the live subtotal, tax, fees, and total when Kung Fu Tea returns them; and
- store availability and verification warnings.

Successful add responses are checked against the final server-side cart
read-back. If an acknowledged line is missing, it moves from the placed manifest
to the failure list instead of being presented as safe. The user then opens an
editable clone on Kung Fu Tea, compares it with this summary, and performs the
manual checkout there. Boba Builder never submits payment.
