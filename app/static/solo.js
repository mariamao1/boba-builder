/* Solo ordering: one person ordering just for themselves.

   The page is usuals-first on purpose — that is the advantage over the Kung Fu
   Tea app (see docs/task27-solo-order.md). My Usuals (browser-local favorites
   with complete modifier sets) and recent saved orders sit above the menu, and
   each usual has a one-tap Reorder that skips the editor and goes straight to
   the shared preview → cart handoff. Everything else (menu, modifiers,
   estimates) is the same captured-menu pipeline as the group flow. */

(function () {
  'use strict';

  const MAX_VISIBLE_DRINKS = 40;
  const STORE_KEY = 'boba-builder:solo-store';
  const NAME_KEY = 'boba-builder:solo-name';
  const SAVED_TOKEN_KEY = 'boba-builder:saved-orders-token';

  /* --- pure helpers (also the tested surface) --------------------------- */

  // A favorite record -> the POST body for /api/solo-orders. The full modifier
  // set travels with it, which is exactly what the official app cannot do
  // without an account: one tap rebuilds the whole drink.
  function quickPayload(favorite) {
    const source = favorite || {};
    return {
      drink: source.drink,
      size: source.size || '',
      sugar: source.sugar || '',
      ice: source.ice || '',
      milk: source.milk || '',
      temperature: source.temperature || '',
      toppings: (source.toppings || []).slice(),
      quantity: source.quantity || 1,
      notes: source.notes || '',
    };
  }

  function validatePayload(payload) {
    if (!payload || !String(payload.drink || '').trim()) return 'pick a drink first';
    const quantity = Number(payload.quantity);
    if (!Number.isFinite(quantity) || Math.floor(quantity) !== quantity) {
      return "that quantity isn't a number";
    }
    if (quantity < 1) return "a quantity of nothing isn't an order";
    if (quantity > 20) return 'at most 20 of one drink per solo order';
    return '';
  }

  // The order-level body for POST /api/solo-orders: one person's drinks.
  function buildOrderPayload(restaurantId, person, drinks) {
    return {
      restaurant_id: restaurantId,
      person: person,
      drinks: drinks,
    };
  }

  if (typeof window !== 'undefined') {
    window.BobaSolo = {
      quickPayload: quickPayload,
      validatePayload: validatePayload,
      buildOrderPayload: buildOrderPayload,
    };
  }

  // Everything below needs the solo page's DOM. When the file is loaded
  // without it (the JS unit tests), only the helpers above apply.
  if (typeof document === 'undefined' || !document.getElementById
      || !document.getElementById('solo-form')) {
    return;
  }

  const $ = (id) => document.getElementById(id);
  const elements = {
    storeSearch: $('store-search'),
    storeHidden: $('solo-store'),
    storeResults: $('store-results'),
    storeCurrent: $('store-current'),
    form: $('solo-form'),
    person: $('person-name'),
    favoritesPanel: $('favorites-panel'),
    favoritesList: $('favorites-list'),
    favoritesCount: $('favorites-count'),
    favoriteStatus: $('favorite-status'),
    recentPanel: $('recent-panel'),
    recentList: $('recent-list'),
    popularPicks: $('popular-picks'),
    popularPicksList: $('popular-picks-list'),
    picker: $('drink-picker'),
    search: $('drink-search'),
    menuCount: $('menu-count'),
    categories: $('category-list'),
    drinkList: $('drink-list'),
    menuMore: $('menu-more'),
    surpriseMe: $('surprise-me'),
    surpriseHint: $('surprise-hint'),
    editor: $('drink-editor'),
    selectedName: $('selected-name'),
    selectedDescription: $('selected-description'),
    selectedOptions: $('selected-options'),
    selectedPrice: $('selected-price'),
    rerollDrink: $('reroll-drink'),
    editDrink: $('edit-drink'),
    removeDrink: $('remove-drink'),
    modifiers: $('modifier-groups'),
    quantity: $('quantity'),
    notes: $('notes'),
    saveFavorite: $('save-favorite'),
    favoriteNameField: $('favorite-name-field'),
    favoriteName: $('favorite-name'),
    total: $('estimated-total'),
    submit: $('submit-drink'),
    formStatus: $('form-status'),
    orderPanel: $('order-panel'),
    orderList: $('order-list'),
    orderCount: $('order-count'),
    orderTotal: $('order-total'),
    checkOrder: $('check-order'),
  };

  let availableStores = [];
  let matchingStores = [];
  let activeStoreIndex = -1;
  let restaurantId = '';
  let menu = null;
  let activeCategory = '';
  let selectedItem = null;
  let selections = {};
  let submitting = false;
  // One person's drinks, collected across usuals and the menu editor and
  // submitted as a single run. Each entry: {payload, title, detail, total}.
  let draftOrder = [];

  /* --- small utilities --------------------------------------------------- */

  function readStorage(key, fallback) {
    try {
      const value = window.localStorage.getItem(key);
      return value == null ? fallback : JSON.parse(value);
    } catch (_error) {
      return fallback;
    }
  }

  function writeStorage(key, value) {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch (_error) {
      // Private browsing must not block ordering.
    }
  }

  function plainWriteStorage(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (_error) {
      // Private browsing must not block ordering.
    }
  }

  function money(value) {
    return `$${Number(value || 0).toFixed(2)}`;
  }

  function plural(count, one, many) {
    return `${count} ${count === 1 ? one : (many || `${one}s`)}`;
  }

  function node(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined) result.textContent = text;
    return result;
  }

  function normalized(value) {
    return String(value || '').toLocaleLowerCase().replace(/\s+/g, ' ').trim();
  }

  async function request(url, options) {
    const response = await fetch(url, options);
    let data;
    try {
      data = await response.json();
    } catch (_error) {
      data = {};
    }
    if (!response.ok || (data && data.ok === false)) {
      throw new Error((data && data.error) || 'Something went wrong. Please try again.');
    }
    return data;
  }

  function setFormStatus(message, kind) {
    elements.formStatus.className = `status${kind ? ` ${kind}` : ''}`;
    elements.formStatus.textContent = message || '';
  }

  function setFavoriteStatus(message, kind) {
    elements.favoriteStatus.className = `favorite-status${kind ? ` ${kind}` : ''}`;
    elements.favoriteStatus.textContent = message || '';
  }

  /* --- store picker ------------------------------------------------------ */

  function storeText(store) {
    return [store.name, store.address, store.city, store.state, store.zip]
      .filter(Boolean).join(' ').toLocaleLowerCase();
  }

  function storeLabel(store) {
    const place = [store.city, store.state].filter(Boolean).join(', ');
    const zip = store.zip ? ` ${store.zip}` : '';
    return place ? `${store.name} — ${place}${zip}` : store.name;
  }

  function closeStoreResults() {
    elements.storeResults.hidden = true;
    elements.storeSearch.setAttribute('aria-expanded', 'false');
    elements.storeSearch.removeAttribute('aria-activedescendant');
    activeStoreIndex = -1;
  }

  function chooseStore(store, options) {
    const switching = restaurantId && restaurantId !== store.restaurant_id;
    restaurantId = store.restaurant_id;
    elements.storeHidden.value = store.restaurant_id;
    elements.storeSearch.value = storeLabel(store);
    elements.storeCurrent.textContent = `Ordering from ${storeLabel(store)}.`;
    closeStoreResults();
    if (!options || options.remember !== false) plainWriteStorage(STORE_KEY, store.restaurant_id);
    if (switching && draftOrder.length) {
      // Draft drinks hold modifiers from another store's menu; keeping them
      // would post options this store may not offer.
      draftOrder = [];
      renderDraft();
      setFormStatus('Switched stores, so the previous drinks were cleared. Add them again from this menu.',
        'err');
    }
    void loadMenu();
  }

  function renderStoreResults() {
    const results = elements.storeResults;
    const query = elements.storeSearch.value.toLocaleLowerCase().trim();
    results.textContent = '';
    activeStoreIndex = -1;
    if (!query) {
      matchingStores = [];
      closeStoreResults();
      return;
    }
    const terms = query.split(/\s+/).filter(Boolean);
    matchingStores = availableStores
      .filter((store) => terms.every((term) => storeText(store).includes(term)))
      .slice(0, 10);
    if (!matchingStores.length) {
      const empty = node('p', 'store-no-results', 'No stores match that search.');
      results.append(empty);
    } else {
      matchingStores.forEach((store, index) => {
        const option = node('button', 'store-result');
        option.type = 'button';
        option.id = `store-result-${index}`;
        option.setAttribute('role', 'option');
        option.setAttribute('aria-selected', 'false');
        const name = node('strong', null, store.name);
        const detail = node('span', null,
          [store.address, store.city, store.state, store.zip].filter(Boolean).join(' · '));
        option.append(name, detail);
        option.addEventListener('click', () => chooseStore(store));
        results.append(option);
      });
    }
    results.hidden = false;
    elements.storeSearch.setAttribute('aria-expanded', 'true');
  }

  /* --- menu -------------------------------------------------------------- */

  async function loadMenu() {
    menu = null;
    selectedItem = null;
    selections = {};
    elements.editor.classList.add('hidden');
    elements.picker.classList.remove('hidden');
    elements.drinkList.textContent = '';
    elements.menuCount.textContent = '';
    elements.surpriseMe.disabled = true;
    elements.surpriseHint.textContent = 'Loading the menu…';
    renderFavorites();
    elements.popularPicks.hidden = true;
    try {
      const data = await request(`/api/menu?restaurant_id=${encodeURIComponent(restaurantId)}`);
      menu = data.menu;
      if (!menu || !menu.items || !menu.items.length) {
        elements.surpriseHint.textContent = 'No drinks are available at this store right now.';
        return;
      }
      elements.surpriseMe.disabled = false;
      elements.surpriseHint.textContent = 'Picking an orderable drink and options…';
      renderCategories();
      renderDrinkList();
      renderFavorites();
      void loadPopularPicks();
    } catch (error) {
      elements.surpriseHint.textContent = error.message;
    }
  }

  function renderCategories() {
    elements.categories.textContent = '';
    const counts = {};
    menu.items.forEach((item) => {
      counts[item.category] = (counts[item.category] || 0) + 1;
    });
    const all = node('button', 'category-choice' + (activeCategory ? '' : ' on'));
    all.type = 'button';
    all.textContent = `All (${menu.items.length})`;
    all.addEventListener('click', () => { activeCategory = ''; renderCategories(); renderDrinkList(); });
    elements.categories.append(all);
    Object.keys(counts).sort().forEach((category) => {
      const button = node('button',
        'category-choice' + (activeCategory === category ? ' on' : ''));
      button.type = 'button';
      button.textContent = `${category} (${counts[category]})`;
      button.addEventListener('click', () => {
        activeCategory = category; renderCategories(); renderDrinkList();
      });
      elements.categories.append(button);
    });
  }

  function renderDrinkList() {
    if (!menu) return;
    const query = normalized(elements.search.value);
    const words = query.split(' ').filter(Boolean);
    const matches = menu.items.filter((item) => {
      if (activeCategory && item.category !== activeCategory) return false;
      const haystack = normalized(`${item.name} ${item.category} ${item.description}`);
      return words.every((word) => haystack.includes(word));
    });
    const visible = matches.slice(0, MAX_VISIBLE_DRINKS);
    elements.drinkList.textContent = '';
    if (!visible.length) {
      elements.drinkList.append(node('p', 'empty-menu', 'No drinks match that search.'));
    }
    visible.forEach((item) => {
      const button = node('button', 'drink-choice');
      button.type = 'button';
      button.setAttribute('aria-label', `Choose ${item.name}, ${money(item.price)}`);
      const wordsBox = node('span');
      wordsBox.append(node('span', 'drink-choice-name', item.name));
      wordsBox.append(node('span', 'drink-choice-category', item.category));
      button.append(wordsBox, node('span', 'drink-choice-price', money(item.price)));
      button.addEventListener('click', () => chooseDrink(item));
      elements.drinkList.append(button);
    });
    elements.menuCount.textContent = plural(matches.length, 'drink');
    elements.menuMore.textContent = matches.length > visible.length
      ? `Showing ${visible.length}. Search to narrow the menu.` : '';
  }

  /* --- favorites: the one-tap reorder ------------------------------------ */

  function favoriteOptionsText(favorite) {
    const parts = [favorite.size, favorite.sugar, favorite.ice, favorite.milk, favorite.temperature]
      .filter(Boolean);
    if ((favorite.toppings || []).length) parts.push(favorite.toppings.join(', '));
    if (favorite.quantity > 1) parts.push(`×${favorite.quantity}`);
    return parts.length ? parts.join(' · ') : 'Store defaults';
  }

  function findMenuItem(name) {
    if (!menu) return null;
    const wanted = normalized(name);
    return menu.items.find((item) => normalized(item.name) === wanted) || null;
  }

  function renderFavorites() {
    if (!window.BobaFavorites) return;
    const favorites = window.BobaFavorites.list(window.localStorage, restaurantId);
    elements.favoritesList.textContent = '';
    elements.favoritesCount.textContent = favorites.length
      ? plural(favorites.length, 'usual') : '';
    if (!favorites.length) {
      elements.favoritesList.append(node('p', 'muted',
        'No usuals for this store yet — build a drink below and save it, and next time it is one tap.'));
      return;
    }
    favorites.forEach((favorite) => {
      const card = node('div', 'favorite-row');
      const label = node('div', 'favorite-label');
      label.append(node('strong', null, favorite.name));
      label.append(node('span', 'muted', `${favorite.drink} · ${favoriteOptionsText(favorite)}`));
      const actions = node('div', 'favorite-actions');
      const reorder = node('button', 'btn primary compact', 'Reorder →');
      reorder.type = 'button';
      reorder.setAttribute('aria-label', `Reorder ${favorite.name}, ${favorite.drink}`);
      reorder.addEventListener('click', () => quickReorder(favorite));
      const customize = node('button', 'text-button', 'Customize');
      customize.type = 'button';
      customize.addEventListener('click', () => customizeFavorite(favorite));
      actions.append(reorder, customize);
      card.append(label, actions);
      elements.favoritesList.append(card);
    });
  }

  // A drink payload against its menu item, for draft order totals. Unknown
  // drinks (a stale usual) stay estimable-nowhere: null, never a guess.
  function estimateFor(item, payload) {
    if (!item) return null;
    let price = Number(item.price || 0);
    const unmatchedToppings = (payload.toppings || []).slice();
    item.option_groups.forEach((group) => {
      let values = [];
      if (group.axis === 'toppings') {
        values = group.options
          .filter((option) => unmatchedToppings.includes(option.label))
          .map((option) => option.label);
        values.forEach((value) => unmatchedToppings.splice(unmatchedToppings.indexOf(value), 1));
      } else if (payload[group.axis]) {
        values = [payload[group.axis]];
      }
      values.forEach((value) => {
        const option = group.options.find((candidate) => candidate.label === value);
        price += Number((option && option.price) || 0);
      });
    });
    return price * Math.max(1, Number(payload.quantity) || 1);
  }

  function addToDraft(payload, title, detail) {
    const problem = validatePayload(payload);
    if (problem) {
      setFormStatus(problem, 'err');
      return false;
    }
    draftOrder.push({
      payload: payload,
      title: title || payload.drink,
      detail: detail || '',
      total: estimateFor(findMenuItem(payload.drink), payload),
    });
    renderDraft();
    return true;
  }

  function renderDraft() {
    elements.orderPanel.hidden = !draftOrder.length;
    elements.orderList.textContent = '';
    elements.orderCount.textContent = draftOrder.length
      ? plural(draftOrder.length, 'drink') : '';
    draftOrder.forEach((entry, index) => {
      const card = node('div', 'favorite-row');
      const label = node('div', 'favorite-label');
      label.append(node('strong', null, entry.title));
      if (entry.detail) label.append(node('span', 'muted', entry.detail));
      const remove = node('button', 'text-button', 'Remove');
      remove.type = 'button';
      remove.setAttribute('aria-label', `Remove ${entry.title} from your solo order`);
      remove.addEventListener('click', () => {
        draftOrder.splice(index, 1);
        renderDraft();
      });
      card.append(label, remove);
      if (entry.total != null) card.append(node('span', 'muted', money(entry.total)));
      elements.orderList.append(card);
    });
    const known = draftOrder.map((entry) => entry.total).filter((value) => value != null);
    if (!known.length) {
      elements.orderTotal.textContent = '—';
      return;
    }
    const sum = known.reduce((total, value) => total + value, 0);
    elements.orderTotal.textContent = money(sum)
      + (known.length < draftOrder.length ? '+' : '');
  }

  function quickReorder(favorite) {
    // The fast path: no editor, no modifier rebuilding. The favorite already
    // holds the complete modifier set, so adding it is a single tap — the
    // order is checked once, for however many drinks it holds.
    const payload = quickPayload(favorite);
    const item = findMenuItem(payload.drink);
    if (!item) {
      setFavoriteStatus(`“${favorite.drink}” is not on this store's menu right now.`, 'err');
      return;
    }
    if (addToDraft(payload, favorite.drink, favoriteOptionsText(favorite))) {
      setFavoriteStatus(`Added ${favorite.drink} — ${plural(draftOrder.length, 'drink')} in your order.`, '');
      elements.orderPanel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }

  function customizeFavorite(favorite) {
    const item = findMenuItem(favorite.drink);
    if (!item) {
      setFavoriteStatus(`“${favorite.drink}” is not on this store's menu right now.`, 'err');
      return;
    }
    chooseDrink(item, quickPayload(favorite));
    elements.editor.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  /* --- recent saved orders ------------------------------------------------ */

  async function loadRecent() {
    let token = null;
    try {
      token = window.localStorage.getItem(SAVED_TOKEN_KEY);
    } catch (_error) {
      token = null;
    }
    if (!token) return;
    let orders = [];
    try {
      const data = await request('/api/saved-orders', {
        headers: { 'X-Saved-Orders-Token': token },
      });
      orders = (data.orders || []).slice(0, 3);
    } catch (_error) {
      return;
    }
    if (!orders.length) return;
    elements.recentPanel.hidden = false;
    elements.recentList.textContent = '';
    orders.forEach((order) => {
      const card = node('div', 'favorite-row');
      const label = node('div', 'favorite-label');
      label.append(node('strong', null, order.label || 'Saved order'));
      const cups = (order.items || []).reduce(
        (total, item) => total + (Number(item.quantity) || 1), 0);
      label.append(node('span', 'muted',
        `${plural(cups, 'cup')} · ${order.created_at ? String(order.created_at).slice(0, 10) : ''}`));
      const repeat = node('button', 'btn compact', 'Order again →');
      repeat.type = 'button';
      repeat.addEventListener('click', async () => {
        repeat.disabled = true;
        try {
          const data = await request(`/api/saved-orders/${encodeURIComponent(order.id)}/repeat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Saved-Orders-Token': token },
            body: '{}',
          });
          window.location.href = data.preview_url;
        } catch (error) {
          setFavoriteStatus(error.message, 'err');
          repeat.disabled = false;
        }
      });
      card.append(label, repeat);
      elements.recentList.append(card);
    });
  }

  /* --- popular picks + randomizer ----------------------------------------- */

  async function loadPopularPicks() {
    if (!window.BobaLeaderboard) return;
    try {
      const data = await request(
        `/api/leaderboard?restaurant_id=${encodeURIComponent(restaurantId)}`);
      const picks = window.BobaLeaderboard.quickPicks(
        (data.leaderboard && data.leaderboard.entries) || [], menu.items, 5);
      window.BobaLeaderboard.renderQuickPicks(
        document, elements.popularPicks, elements.popularPicksList, picks,
        (item) => chooseDrink(item));
    } catch (_error) {
      elements.popularPicks.hidden = true;
    }
  }

  function surpriseMe() {
    if (!menu || !window.BobaRandomizer) return;
    const surprise = window.BobaRandomizer.pick(menu, activeCategory);
    if (!surprise) {
      setFormStatus('No orderable drinks are available in this category.', 'err');
      return;
    }
    chooseDrink(surprise.item, surprise.selections);
    setFormStatus(`✨ Surprise! We picked ${surprise.item.name}. Tweak anything, re-roll, or check it as-is.`,
      'surprise');
  }

  /* --- editor ------------------------------------------------------------- */

  function initialSelection(group) {
    if (group.multiselect) return [];
    const marked = group.options.find((option) => option.is_default);
    if (group.required && marked) return marked.label;
    if (group.required && group.options.length === 1) return group.options[0].label;
    return '';
  }

  function chooseDrink(item, existing) {
    selectedItem = item;
    selections = {};
    item.option_groups.forEach((group) => {
      selections[group.key] = initialSelection(group);
    });
    if (existing) fillExistingSelections(existing);
    elements.picker.classList.add('hidden');
    elements.editor.classList.remove('hidden');
    elements.selectedName.textContent = item.name;
    elements.selectedDescription.textContent = item.description || '';
    elements.selectedPrice.textContent = money(item.price);
    elements.removeDrink.setAttribute('aria-label', `Remove ${item.name}`);
    renderModifiers();
    updateEstimate();
    setFormStatus('', '');
  }

  function fillExistingSelections(order) {
    const remainingToppings = (order.toppings || []).slice();
    selectedItem.option_groups.filter((group) => group.axis !== 'toppings').forEach((group) => {
      const offered = group.options.map((option) => option.label);
      const value = order[group.axis] || '';
      selections[group.key] = offered.includes(value) ? value : '';
    });
    selectedItem.option_groups.filter((group) => group.axis === 'toppings').forEach((group) => {
      const offered = group.options.map((option) => option.label);
      if (!group.multiselect) {
        const matchIndex = remainingToppings.findIndex((name) => offered.includes(name));
        selections[group.key] = matchIndex >= 0
          ? remainingToppings.splice(matchIndex, 1)[0] : '';
        return;
      }
      const picked = [];
      for (let index = remainingToppings.length - 1; index >= 0; index -= 1) {
        if (offered.includes(remainingToppings[index])) {
          picked.unshift(remainingToppings[index]);
          remainingToppings.splice(index, 1);
        }
      }
      selections[group.key] = picked;
    });
  }

  function optionPriceText(option) {
    return Number(option.price || 0) ? `+${money(option.price)}` : '';
  }

  function renderModifiers() {
    elements.modifiers.textContent = '';
    selectedItem.option_groups.forEach((group) => {
      const fieldset = node('fieldset', 'modifier-group');
      fieldset.dataset.groupKey = group.key;
      const legend = node('legend', null, group.name);
      if (group.required) {
        legend.append(document.createTextNode(' '));
        legend.append(node('span', 'required-mark', 'Required'));
      }
      fieldset.append(legend);
      if (group.multiselect) {
        const grid = node('div', 'option-grid');
        group.options.forEach((option) => {
          const label = node('label', 'option-check');
          const input = document.createElement('input');
          input.type = 'checkbox';
          input.value = option.label;
          input.checked = (selections[group.key] || []).includes(option.label);
          input.addEventListener('change', () => {
            selections[group.key] = Array.from(grid.querySelectorAll('input:checked'))
              .map((control) => control.value);
            enforceMaximum(group, grid);
            clearGroupError(fieldset);
            updateEstimate();
          });
          label.append(input, node('span', 'option-name', option.label));
          const price = optionPriceText(option);
          if (price) label.append(node('span', 'option-price', price));
          grid.append(label);
        });
        fieldset.append(grid);
        enforceMaximum(group, grid);
      } else {
        const select = node('select', 'modifier-select');
        select.setAttribute('aria-label', group.name);
        if (group.required) select.required = true;
        const blank = document.createElement('option');
        blank.value = '';
        blank.textContent = group.required ? 'Choose one…' : 'Store default / none';
        select.append(blank);
        group.options.forEach((option) => {
          const choice = document.createElement('option');
          choice.value = option.label;
          const extra = optionPriceText(option);
          choice.textContent = `${option.label}${extra ? ` · ${extra}` : ''}`;
          choice.selected = selections[group.key] === option.label;
          select.append(choice);
        });
        select.addEventListener('change', () => {
          selections[group.key] = select.value;
          clearGroupError(fieldset);
          updateEstimate();
        });
        fieldset.append(select);
      }
      fieldset.append(node('p', 'group-error'));
      elements.modifiers.append(fieldset);
    });
  }

  function enforceMaximum(group, grid) {
    if (!group.max) return;
    const checked = grid.querySelectorAll('input:checked').length;
    grid.querySelectorAll('input').forEach((control) => {
      control.disabled = !control.checked && checked >= group.max;
    });
  }

  function clearGroupError(fieldset) {
    const error = fieldset.querySelector('.group-error');
    if (error) error.textContent = '';
  }

  function selectedOption(group, label) {
    return group.options.find((option) => option.label === label);
  }

  function selectedUnitPrice() {
    let price = Number(selectedItem.price || 0);
    selectedItem.option_groups.forEach((group) => {
      const values = Array.isArray(selections[group.key])
        ? selections[group.key] : [selections[group.key]].filter(Boolean);
      values.forEach((value) => {
        const option = selectedOption(group, value);
        price += Number((option && option.price) || 0);
      });
    });
    return price;
  }

  function updateEstimate() {
    if (!selectedItem) {
      elements.total.textContent = '—';
      elements.selectedOptions.textContent = '';
      return;
    }
    const summary = [];
    selectedItem.option_groups.forEach((group) => {
      const values = Array.isArray(selections[group.key])
        ? selections[group.key] : [selections[group.key]].filter(Boolean);
      if (values.length) summary.push(`${group.name}: ${values.join(', ')}`);
    });
    elements.selectedOptions.textContent = summary.length ? summary.join(' · ') : 'Store defaults';
    const quantity = Math.max(1, Math.min(20, Number(elements.quantity.value) || 1));
    elements.selectedPrice.textContent = quantity > 1
      ? `${money(selectedUnitPrice())} each` : money(selectedUnitPrice());
    elements.total.textContent = money(selectedUnitPrice() * quantity);
  }

  function validateSelections() {
    let valid = true;
    selectedItem.option_groups.forEach((group) => {
      const fieldset = elements.modifiers.querySelector(`[data-group-key="${group.key}"]`);
      const error = fieldset && fieldset.querySelector('.group-error');
      const values = Array.isArray(selections[group.key])
        ? selections[group.key] : [selections[group.key]].filter(Boolean);
      const minimum = Math.max(Number(group.min || 0), group.required ? 1 : 0);
      if (values.length < minimum) {
        if (error) {
          error.textContent = minimum === 1
            ? 'Choose one option.' : `Choose at least ${minimum}.`;
        }
        valid = false;
      } else if (group.max && values.length > group.max) {
        if (error) error.textContent = `Choose no more than ${group.max}.`;
        valid = false;
      } else if (error) {
        error.textContent = '';
      }
    });
    return valid;
  }

  function editorSummary() {
    const parts = [];
    selectedItem.option_groups.forEach((group) => {
      const values = Array.isArray(selections[group.key])
        ? selections[group.key] : [selections[group.key]].filter(Boolean);
      if (values.length) parts.push(values.join(', '));
    });
    const quantity = Math.max(1, Math.min(20, Number(elements.quantity.value) || 1));
    if (quantity > 1) parts.push(`×${quantity}`);
    return parts.length ? parts.join(' · ') : 'Store defaults';
  }

  function buildPayload() {
    const payload = {
      drink: selectedItem.name,
      size: '', sugar: '', ice: '', milk: '', temperature: '', toppings: [],
      quantity: Math.max(1, Math.min(20, Number(elements.quantity.value) || 1)),
      notes: elements.notes.value.trim(),
    };
    selectedItem.option_groups.forEach((group) => {
      const value = selections[group.key];
      if (group.axis === 'toppings') {
        if (Array.isArray(value)) payload.toppings.push(...value);
        else if (value) payload.toppings.push(value);
      } else if (value) {
        payload[group.axis] = value;
      }
    });
    return payload;
  }

  function resetEditor() {
    selectedItem = null;
    selections = {};
    elements.quantity.value = '1';
    elements.notes.value = '';
    elements.editor.classList.add('hidden');
    elements.picker.classList.remove('hidden');
    elements.saveFavorite.checked = false;
    elements.favoriteName.value = '';
    elements.favoriteNameField.classList.add('hidden');
    elements.search.value = '';
    setFormStatus('', '');
  }

  /* --- submit: drinks into the draft, then the draft through the pipeline -- */

  function submitSolo(event) {
    event.preventDefault();
    if (!selectedItem) return;
    if (!validateSelections()) {
      setFormStatus('Choose the required options first.', 'err');
      return;
    }
    const payload = buildPayload();
    try {
      maybeSaveFavorite(payload);
    } catch (_error) {
      return;
    }
    if (addToDraft(payload, payload.drink, editorSummary())) {
      setFormStatus(`Added ${payload.drink} — ${plural(draftOrder.length, 'drink')} in your order.`, '');
      resetEditor();
      renderDrinkList();
      elements.orderPanel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }

  async function checkOrder() {
    if (submitting || !draftOrder.length) return;
    submitting = true;
    elements.checkOrder.disabled = true;
    elements.checkOrder.textContent = 'Checking…';
    const person = elements.person.value.trim();
    try {
      const data = await request('/api/solo-orders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildOrderPayload(
          restaurantId, person, draftOrder.map((entry) => entry.payload))),
      });
      plainWriteStorage(NAME_KEY, person);
      draftOrder = [];
      window.location.href = data.preview_url;
    } catch (error) {
      setFormStatus(error.message, 'err');
    } finally {
      submitting = false;
      elements.checkOrder.disabled = false;
      elements.checkOrder.textContent = 'Check my order →';
    }
  }

  function maybeSaveFavorite(payload) {
    if (!elements.saveFavorite.checked || !window.BobaFavorites) return;
    try {
      const name = (elements.favoriteName.value || '').trim()
        || `${payload.drink} usual`;
      const favorite = window.BobaFavorites.fromOrder(name, {
        drink: payload.drink, size: payload.size, sugar: payload.sugar,
        ice: payload.ice, milk: payload.milk, temperature: payload.temperature,
        toppings: payload.toppings, quantity: payload.quantity, notes: payload.notes,
      }, restaurantId, selectedItem);
      window.BobaFavorites.save(window.localStorage, favorite);
    } catch (error) {
      setFormStatus(error.message, 'err');
      throw error;
    }
  }

  /* --- wire up ------------------------------------------------------------ */

  elements.storeSearch.addEventListener('input', () => {
    elements.storeHidden.value = '';
    renderStoreResults();
  });
  elements.storeSearch.addEventListener('focus', renderStoreResults);
  elements.storeSearch.addEventListener('blur', () => {
    window.setTimeout(closeStoreResults, 150);
  });
  elements.storeSearch.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      closeStoreResults();
      return;
    }
    if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && matchingStores.length) {
      event.preventDefault();
      activeStoreIndex = (activeStoreIndex + (event.key === 'ArrowDown' ? 1 : -1)
        + matchingStores.length) % matchingStores.length;
      const options = [...elements.storeResults.querySelectorAll('.store-result')];
      options.forEach((option, index) => {
        option.classList.toggle('active', index === activeStoreIndex);
      });
      return;
    }
    if (event.key === 'Enter' && matchingStores.length) {
      event.preventDefault();
      chooseStore(matchingStores[activeStoreIndex >= 0 ? activeStoreIndex : 0]);
    }
  });
  document.addEventListener('click', (event) => {
    if (!$('store-picker').contains(event.target)) closeStoreResults();
  });

  elements.search.addEventListener('input', renderDrinkList);
  elements.surpriseMe.addEventListener('click', surpriseMe);
  elements.rerollDrink.addEventListener('click', surpriseMe);
  elements.editDrink.addEventListener('click', () => {
    elements.editor.classList.add('hidden');
    elements.picker.classList.remove('hidden');
  });
  elements.removeDrink.addEventListener('click', resetEditor);
  elements.quantity.addEventListener('input', updateEstimate);
  elements.saveFavorite.addEventListener('change', () => {
    elements.favoriteNameField.classList.toggle('hidden', !elements.saveFavorite.checked);
    elements.favoriteName.required = elements.saveFavorite.checked;
    if (elements.saveFavorite.checked) elements.favoriteName.focus();
  });
  elements.form.addEventListener('submit', submitSolo);
  elements.checkOrder.addEventListener('click', checkOrder);

  function readRaw(key) {
    try {
      return window.localStorage.getItem(key) || '';
    } catch (_error) {
      return '';
    }
  }

  async function init() {
    const storedName = readRaw(NAME_KEY);
    if (storedName) elements.person.value = storedName;
    try {
      const data = await request('/api/stores');
      availableStores = data.stores || [];
      const remembered = readRaw(STORE_KEY);
      const preferred = (typeof remembered === 'string' && remembered)
        ? availableStores.find((store) => store.restaurant_id === remembered)
        : null;
      const fallback = availableStores.find(
        (store) => store.restaurant_id === data.default_restaurant_id) || availableStores[0];
      const initial = preferred || fallback;
      if (!initial) {
        setFormStatus('No stores are available right now. Please try again later.', 'err');
        return;
      }
      chooseStore(initial, { remember: Boolean(preferred) });
    } catch (error) {
      setFormStatus(error.message, 'err');
    }
    void loadRecent();
  }

  void init();
})();
