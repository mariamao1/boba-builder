/* Menu-driven random drink selections for the participant order page.

   The captured menu is the source of truth: required/min/max constraints are
   read from each item's own option groups. Optional extras are deliberately
   restrained so "Surprise Me" stays fun without quietly building an enormous
   toppings bill. */

(function (global) {
  'use strict';

  function asCount(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) && number >= 0 ? Math.floor(number) : fallback;
  }

  function optionLabels(group) {
    const labels = [];
    const options = group && Array.isArray(group.options) ? group.options : [];
    options.forEach((option) => {
      const label = option && typeof option.label === 'string' ? option.label : '';
      if (label && !labels.includes(label)) labels.push(label);
    });
    return labels;
  }

  function limits(group, optionCount) {
    const minimum = Math.max(asCount(group.min, 0), group.required ? 1 : 0);
    let maximum = group.multiselect ? optionCount : Math.min(1, optionCount);
    if (group.max !== null && group.max !== undefined) {
      maximum = Math.min(maximum, asCount(group.max, maximum));
    }
    return { minimum, maximum };
  }

  function isDrink(item) {
    // The participant menu can also contain a Featured Food section. It stays
    // available for manual ordering, but a drink surprise should not be fries.
    return !/(^|\s)food($|\s)/i.test(String(item.category || ''));
  }

  function isOrderableItem(item) {
    if (!item || typeof item.name !== 'string' || !item.name.trim() || !isDrink(item)
        || !Array.isArray(item.option_groups)) {
      return false;
    }
    const groups = item.option_groups;
    const keys = new Set();
    return groups.every((group) => {
      if (!group || typeof group.key !== 'string' || !group.key || keys.has(group.key)
          || !Array.isArray(group.options)) return false;
      keys.add(group.key);
      const labels = optionLabels(group);
      const range = limits(group, labels.length);
      return range.minimum <= range.maximum;
    });
  }

  function eligibleItems(menu, category) {
    const items = menu && Array.isArray(menu.items) ? menu.items : [];
    return items.filter((item) => (!category || item.category === category) && isOrderableItem(item));
  }

  function randomUnit(random) {
    const value = Number((random || Math.random)());
    if (!Number.isFinite(value) || value <= 0) return 0;
    if (value >= 1) return 0.9999999999999999;
    return value;
  }

  function randomIndex(length, random) {
    return Math.floor(randomUnit(random) * length);
  }

  function randomLabels(labels, count, random) {
    const remaining = labels.slice();
    const chosen = [];
    while (chosen.length < count && remaining.length) {
      chosen.push(remaining.splice(randomIndex(remaining.length, random), 1)[0]);
    }
    return chosen;
  }

  function selectionFor(group, random) {
    const labels = optionLabels(group);
    const range = limits(group, labels.length);

    if (!group.multiselect) {
      if (!labels.length || range.maximum === 0) return '';
      if (group.axis === 'toppings') return labels[randomIndex(labels.length, random)];
      // Blank is the real store-default choice for an optional select. Give it
      // a little more weight than any one paid/custom option.
      if (range.minimum === 0 && randomUnit(random) < 0.6) return '';
      return labels[randomIndex(labels.length, random)];
    }

    // Unlimited topping menus can contain dozens of choices. Keep randomized
    // toppings to one or two (while honoring a larger required minimum) rather
    // than creating an expensive novelty order. Other multi-select groups may
    // still validly use none.
    const minimum = group.axis === 'toppings' && range.maximum > 0
      ? Math.max(range.minimum, 1) : range.minimum;
    const playfulMaximum = Math.min(range.maximum, Math.max(minimum, 2));
    let count = minimum;
    if (playfulMaximum > minimum) {
      count += randomIndex(playfulMaximum - minimum + 1, random);
    }
    return randomLabels(labels, count, random);
  }

  function isValidSelection(item, selections) {
    if (!isOrderableItem(item) || !selections) return false;
    return item.option_groups.every((group) => {
      const offered = optionLabels(group);
      const raw = selections[group.key];
      const values = Array.isArray(raw) ? raw : [raw].filter(Boolean);
      const range = limits(group, offered.length);
      const unique = new Set(values);
      return values.length === unique.size
        && values.every((value) => offered.includes(value))
        && values.length >= range.minimum
        && values.length <= range.maximum
        && (group.multiselect || values.length <= 1);
    });
  }

  function pick(menu, category, random) {
    const candidates = eligibleItems(menu, category);
    if (!candidates.length) return null;
    const item = candidates[randomIndex(candidates.length, random)];
    const selections = {};
    item.option_groups.forEach((group) => {
      selections[group.key] = selectionFor(group, random);
    });
    return isValidSelection(item, selections) ? { item, selections } : null;
  }

  global.BobaRandomizer = {
    eligibleItems,
    isValidSelection,
    pick,
  };
})(typeof window !== 'undefined' ? window : this);
