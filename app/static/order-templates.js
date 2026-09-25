/* Browser-local whole-order templates (Task 28).

   Favorites (Task 15) save one drink; templates save an entire order — every
   drink paired with the name of the person who ordered it, each with its full
   modifier set. Like favorites they are account-free JSON records in this
   browser's `localStorage`, scoped to a store, and cleared with site data.
   Loading a template is always a starting point, never a finished result:
   entries are re-checked against the current menu and can be tweaked, added,
   or dropped before anything is committed. */
(function (root) {
  'use strict';

  const STORAGE_KEY = 'boba-builder:order-templates:v1';
  const MAX_PER_STORE = 10;
  const MAX_ENTRIES = 60;
  const AXES = ['size', 'sugar', 'ice', 'milk', 'temperature'];

  function storageOrDefault(storage) {
    return storage || root.localStorage;
  }

  function text(value, limit) {
    return typeof value === 'string' ? value.trim().slice(0, limit) : '';
  }

  function toppingsList(value) {
    let values = value;
    if (typeof values === 'string') values = values.split(',');
    if (!Array.isArray(values)) return [];
    return values.map((entry) => text(entry, 100)).filter(Boolean).slice(0, 20);
  }

  // One template entry: a person's name paired with a drink and its complete
  // modifier set. The drink and the person are both required — a nameless
  // entry cannot be confirmed by anyone in a group session.
  function cleanEntry(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const entry = {
      person: text(raw.person, 80),
      drink: text(raw.drink, 160),
      size: text(raw.size, 80),
      sugar: text(raw.sugar, 80),
      ice: text(raw.ice, 80),
      milk: text(raw.milk, 80),
      temperature: text(raw.temperature, 80),
      toppings: toppingsList(raw.toppings),
      quantity: Math.max(1, Math.min(20, Number.parseInt(raw.quantity, 10) || 1)),
      notes: text(raw.notes, 500),
    };
    if (!entry.person || !entry.drink) return null;
    return entry;
  }

  function clean(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const entries = Array.isArray(raw.entries)
      ? raw.entries.map(cleanEntry).filter(Boolean) : [];
    if (!entries.length || entries.length > MAX_ENTRIES) return null;
    const template = {
      id: text(raw.id, 100),
      name: text(raw.name, 80),
      restaurant_id: text(raw.restaurant_id, 100),
      entries: entries,
      created_at: text(raw.created_at, 40),
      updated_at: text(raw.updated_at, 40),
    };
    if (!template.id || !template.name || !template.restaurant_id) return null;
    return template;
  }

  function read(storage) {
    try {
      const parsed = JSON.parse(storageOrDefault(storage).getItem(STORAGE_KEY) || '[]');
      return Array.isArray(parsed) ? parsed.map(clean).filter(Boolean) : [];
    } catch (_error) {
      return [];
    }
  }

  function write(storage, templates) {
    storageOrDefault(storage).setItem(STORAGE_KEY, JSON.stringify(templates));
  }

  function id() {
    if (root.crypto && typeof root.crypto.randomUUID === 'function') {
      return root.crypto.randomUUID();
    }
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  }

  function list(storage, restaurantId) {
    const templates = read(storage);
    if (!restaurantId) return templates;
    return templates.filter((template) => template.restaurant_id === restaurantId);
  }

  // Build a template from preview/run rows. Matched rows carry `canonical`
  // menu values; prefer those over what was typed, so a reloaded template
  // starts from drinks the menu already understands.
  function fromRunRows(name, rows, restaurantId, existing) {
    const now = new Date().toISOString();
    const entries = (Array.isArray(rows) ? rows : []).map((row) => {
      const source = row || {};
      const canonical = source.canonical && typeof source.canonical === 'object'
        ? source.canonical : {};
      return cleanEntry({
        person: source.person,
        drink: canonical.drink || source.drink,
        size: canonical.size !== undefined ? canonical.size : source.size,
        sugar: canonical.sugar !== undefined ? canonical.sugar : source.sugar,
        ice: canonical.ice !== undefined ? canonical.ice : source.ice,
        milk: canonical.milk !== undefined ? canonical.milk : source.milk,
        temperature: canonical.temperature !== undefined
          ? canonical.temperature : source.temperature,
        toppings: canonical.toppings !== undefined ? canonical.toppings : source.toppings,
        quantity: source.quantity,
        notes: source.notes,
      });
    }).filter(Boolean);
    return clean({
      id: existing ? existing.id : id(),
      name: name,
      restaurant_id: restaurantId,
      entries: entries,
      created_at: (existing && existing.created_at) || now,
      updated_at: now,
    });
  }

  function save(storage, template) {
    const cleaned = clean(template);
    if (!cleaned) {
      throw new Error('Name this template and include at least one named drink before saving it.');
    }
    const templates = read(storage);
    const existingIndex = templates.findIndex((entry) => entry.id === cleaned.id);
    const countAtStore = templates.filter(
      (entry) => entry.restaurant_id === cleaned.restaurant_id && entry.id !== cleaned.id
    ).length;
    if (existingIndex < 0 && countAtStore >= MAX_PER_STORE) {
      throw new Error(`You can keep up to ${MAX_PER_STORE} order templates for each store.`);
    }
    if (existingIndex >= 0) templates[existingIndex] = cleaned;
    else templates.unshift(cleaned);
    write(storage, templates);
    return cleaned;
  }

  function remove(storage, templateId) {
    const templates = read(storage);
    const kept = templates.filter((template) => template.id !== templateId);
    if (kept.length === templates.length) return false;
    write(storage, kept);
    return true;
  }

  // Solo loading restores drinks, not people: one person is placing this
  // order. Each payload is a fresh copy, so editing a draft never rewrites
  // the saved template.
  function toSoloDrinks(template) {
    const entries = (template && Array.isArray(template.entries)) ? template.entries : [];
    return entries.map((entry) => ({
      drink: entry.drink,
      size: entry.size || '',
      sugar: entry.sugar || '',
      ice: entry.ice || '',
      milk: entry.milk || '',
      temperature: entry.temperature || '',
      toppings: (entry.toppings || []).slice(),
      quantity: entry.quantity || 1,
      notes: entry.notes || '',
    }));
  }

  // The owner's display name when every entry belongs to the same person
  // (compared case-insensitively, first spelling kept); otherwise ''.
  function uniformPerson(template) {
    const entries = (template && Array.isArray(template.entries)) ? template.entries : [];
    if (!entries.length) return '';
    const first = entries[0].person || '';
    if (!first) return '';
    const wanted = first.toLocaleLowerCase().replace(/\s+/g, ' ').trim();
    for (const entry of entries) {
      const candidate = (entry.person || '').toLocaleLowerCase().replace(/\s+/g, ' ').trim();
      if (candidate !== wanted) return '';
    }
    return first;
  }

  function peopleCount(template) {
    const names = {};
    const entries = (template && Array.isArray(template.entries)) ? template.entries : [];
    entries.forEach((entry) => {
      const key = (entry.person || '').toLocaleLowerCase().replace(/\s+/g, ' ').trim();
      if (key) names[key] = true;
    });
    return Object.keys(names).length;
  }

  function cupsCount(template) {
    const entries = (template && Array.isArray(template.entries)) ? template.entries : [];
    return entries.reduce((total, entry) => total + (Number(entry.quantity) || 1), 0);
  }

  function drinkKey(value) {
    return text(value, 160).toLocaleLowerCase().replace(/\s+/g, ' ');
  }

  // Per-entry menu check, mirroring the favorites rule: a changed drink or
  // modifier is never silently substituted — the entry loads for review.
  function entryCompatibility(entry, menu) {
    if (!entry || !menu || !menu.restaurant_id) {
      return { ok: false, reason: 'This template belongs to a different store.' };
    }
    const items = Array.isArray(menu.items) ? menu.items : [];
    const wantedName = drinkKey(entry.drink);
    const item = items.find((candidate) => drinkKey(candidate.name) === wantedName) || null;
    if (!item) return { ok: false, reason: `"${entry.drink}" is not on this store's menu right now.` };

    const groups = Array.isArray(item.option_groups) ? item.option_groups : [];
    const offeredAxes = {};
    for (const group of groups.filter((candidate) => candidate.axis !== 'toppings')) {
      offeredAxes[group.axis] = true;
      const offered = (group.options || []).map((option) => option.label);
      const value = entry[group.axis] || '';
      const minimum = Math.max(Number(group.min || 0), group.required ? 1 : 0);
      if ((minimum && !value) || (value && !offered.includes(value))) {
        return { ok: false, item: item, reason: `${group.name} needs to be reviewed.` };
      }
    }
    for (const axis of AXES) {
      if (entry[axis] && !offeredAxes[axis]) {
        return { ok: false, item: item, reason: 'Its available options have changed.' };
      }
    }
    const remaining = (entry.toppings || []).slice();
    for (const group of groups.filter((candidate) => candidate.axis === 'toppings')) {
      const offered = (group.options || []).map((option) => option.label);
      let selected = [];
      if (group.multiselect) {
        selected = remaining.filter((value) => offered.includes(value));
        selected.forEach((value) => remaining.splice(remaining.indexOf(value), 1));
      } else {
        const index = remaining.findIndex((value) => offered.includes(value));
        if (index >= 0) selected = remaining.splice(index, 1);
      }
      const minimum = Math.max(Number(group.min || 0), group.required ? 1 : 0);
      if (selected.length < minimum || (group.max && selected.length > group.max)) {
        return { ok: false, item: item, reason: `${group.name} needs to be reviewed.` };
      }
    }
    if (remaining.length) {
      return { ok: false, item: item, reason: 'One or more toppings are no longer offered.' };
    }
    return {
      ok: true,
      item: item,
      payload: {
        drink: item.name,
        size: entry.size,
        sugar: entry.sugar,
        ice: entry.ice,
        milk: entry.milk,
        temperature: entry.temperature,
        toppings: (entry.toppings || []).slice(),
        quantity: entry.quantity,
        notes: entry.notes,
      },
    };
  }

  root.BobaOrderTemplates = {
    STORAGE_KEY: STORAGE_KEY,
    MAX_PER_STORE: MAX_PER_STORE,
    MAX_ENTRIES: MAX_ENTRIES,
    list: list,
    fromRunRows: fromRunRows,
    save: save,
    remove: remove,
    toSoloDrinks: toSoloDrinks,
    uniformPerson: uniformPerson,
    peopleCount: peopleCount,
    cupsCount: cupsCount,
    entryCompatibility: entryCompatibility,
  };
}(window));
