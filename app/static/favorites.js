/* Browser-local favorite drinks. There are deliberately no accounts or server
   identifiers here: a usual belongs to this browser and is scoped to a store. */
(function (root) {
  'use strict';

  const STORAGE_KEY = 'boba-builder:favorites:v1';
  const MAX_PER_STORE = 8;
  const AXES = ['size', 'sugar', 'ice', 'milk', 'temperature'];

  function storageOrDefault(storage) {
    return storage || root.localStorage;
  }

  function text(value, limit) {
    return typeof value === 'string' ? value.trim().slice(0, limit) : '';
  }

  function clean(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const favorite = {
      id: text(raw.id, 100),
      name: text(raw.name, 50),
      restaurant_id: text(raw.restaurant_id, 100),
      item_id: text(raw.item_id, 160),
      drink: text(raw.drink, 160),
      size: text(raw.size, 80),
      sugar: text(raw.sugar, 80),
      ice: text(raw.ice, 80),
      milk: text(raw.milk, 80),
      temperature: text(raw.temperature, 80),
      toppings: Array.isArray(raw.toppings)
        ? raw.toppings.map((value) => text(value, 100)).filter(Boolean).slice(0, 20) : [],
      quantity: Math.max(1, Math.min(20, Number.parseInt(raw.quantity, 10) || 1)),
      notes: text(raw.notes, 500),
      created_at: text(raw.created_at, 40),
      updated_at: text(raw.updated_at, 40),
    };
    if (!favorite.id || !favorite.name || !favorite.restaurant_id || !favorite.drink) return null;
    return favorite;
  }

  function read(storage) {
    try {
      const parsed = JSON.parse(storageOrDefault(storage).getItem(STORAGE_KEY) || '[]');
      return Array.isArray(parsed) ? parsed.map(clean).filter(Boolean) : [];
    } catch (_error) {
      return [];
    }
  }

  function write(storage, favorites) {
    storageOrDefault(storage).setItem(STORAGE_KEY, JSON.stringify(favorites));
  }

  function id() {
    if (root.crypto && typeof root.crypto.randomUUID === 'function') {
      return root.crypto.randomUUID();
    }
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  }

  function list(storage, restaurantId) {
    const favorites = read(storage);
    if (!restaurantId) return favorites;
    return favorites.filter((favorite) => favorite.restaurant_id === restaurantId);
  }

  function fromOrder(name, payload, restaurantId, item, existing) {
    const now = new Date().toISOString();
    return clean({
      id: existing ? existing.id : id(),
      name: name,
      restaurant_id: restaurantId,
      item_id: item && item.id,
      drink: payload.drink,
      size: payload.size,
      sugar: payload.sugar,
      ice: payload.ice,
      milk: payload.milk,
      temperature: payload.temperature,
      toppings: payload.toppings,
      quantity: payload.quantity,
      notes: payload.notes,
      created_at: (existing && existing.created_at) || now,
      updated_at: now,
    });
  }

  function save(storage, favorite) {
    const cleaned = clean(favorite);
    if (!cleaned) throw new Error('Give this usual a name before saving it.');
    const favorites = read(storage);
    const existingIndex = favorites.findIndex((entry) => entry.id === cleaned.id);
    const countAtStore = favorites.filter(
      (entry) => entry.restaurant_id === cleaned.restaurant_id && entry.id !== cleaned.id
    ).length;
    if (existingIndex < 0 && countAtStore >= MAX_PER_STORE) {
      throw new Error(`You can keep up to ${MAX_PER_STORE} usuals for each store.`);
    }
    if (existingIndex >= 0) favorites[existingIndex] = cleaned;
    else favorites.unshift(cleaned);
    write(storage, favorites);
    return cleaned;
  }

  function remove(storage, favoriteId) {
    const favorites = read(storage);
    const kept = favorites.filter((favorite) => favorite.id !== favoriteId);
    if (kept.length === favorites.length) return false;
    write(storage, kept);
    return true;
  }

  function drinkKey(value) {
    return text(value, 160).toLocaleLowerCase().replace(/\s+/g, ' ');
  }

  function findItem(favorite, menu) {
    const items = menu && Array.isArray(menu.items) ? menu.items : [];
    const byId = items.find((candidate) => favorite.item_id
      && String(candidate.id) === String(favorite.item_id));
    if (byId) return byId;
    const wantedName = drinkKey(favorite.drink);
    return items.find((candidate) => drinkKey(candidate.name) === wantedName) || null;
  }

  function compatibility(favorite, menu) {
    if (!favorite || !menu || favorite.restaurant_id !== menu.restaurant_id) {
      return { ok: false, reason: 'This usual belongs to a different store.' };
    }
    const item = findItem(favorite, menu);
    if (!item) return { ok: false, reason: 'This drink is no longer on this store menu.' };

    const groups = Array.isArray(item.option_groups) ? item.option_groups : [];
    const offeredAxes = {};
    for (const group of groups.filter((candidate) => candidate.axis !== 'toppings')) {
      offeredAxes[group.axis] = true;
      const offered = (group.options || []).map((option) => option.label);
      const value = favorite[group.axis] || '';
      const minimum = Math.max(Number(group.min || 0), group.required ? 1 : 0);
      if ((minimum && !value) || (value && !offered.includes(value))) {
        return { ok: false, item: item, reason: `${group.name} needs to be reviewed.` };
      }
    }
    for (const axis of AXES) {
      if (favorite[axis] && !offeredAxes[axis]) {
        return { ok: false, item: item, reason: 'Its available options have changed.' };
      }
    }

    const remaining = favorite.toppings.slice();
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
        size: favorite.size,
        sugar: favorite.sugar,
        ice: favorite.ice,
        milk: favorite.milk,
        temperature: favorite.temperature,
        toppings: favorite.toppings.slice(),
        quantity: favorite.quantity,
        notes: favorite.notes,
      },
    };
  }

  root.BobaFavorites = {
    STORAGE_KEY: STORAGE_KEY,
    MAX_PER_STORE: MAX_PER_STORE,
    list: list,
    fromOrder: fromOrder,
    save: save,
    remove: remove,
    findItem: findItem,
    compatibility: compatibility,
  };
}(window));
