# Task 16 — Randomizer / “Surprise Me”

The participant order page offers **Surprise Me** before a drink is selected
and **Surprise me again** in the editor. A result is only a draft: every chosen
modifier stays editable, and nothing is submitted until the participant taps
**Add my drink**.

The drink is chosen uniformly from the current store menu. Selecting a category
chip first limits the draw to that category; search text does not silently
affect it. Food categories are excluded because this action promises a drink.
There is no popularity weighting because captured menus contain no reliable
popularity signal.

Options come exclusively from the selected item's own option groups. Required
and minimum choices are always filled, maximums are always observed, and
optional single-choice groups can use the store default. For open-ended
multi-select groups, the randomizer chooses no more than two options unless the
menu itself requires more. When a drink offers an add-on topping group, at least
one topping is included so that part of the order is a surprise too. This keeps
surprise toppings playful but avoids producing an unexpectedly expensive order.

The randomizer is implemented as a small standalone browser module so its
constraint handling can be tested independently from the page DOM.
