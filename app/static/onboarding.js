/* Browser-local state for the two first-time organizer hints.

   The moments are deliberately independent: choosing how to collect orders
   should not prevent the cart-handoff explanation from appearing later. */
(function (root) {
  'use strict';

  const STORAGE_KEY = 'boba-builder:onboarding:v1';
  const MOMENTS = { entry: true, handoff: true };

  function known(kind) {
    return Object.prototype.hasOwnProperty.call(MOMENTS, kind);
  }

  function browserStorage(provided) {
    if (provided !== undefined) return provided;
    try {
      return root.localStorage;
    } catch (_error) {
      return null;
    }
  }

  function read(store) {
    if (!store || typeof store.getItem !== 'function') return {};
    try {
      const parsed = JSON.parse(store.getItem(STORAGE_KEY) || '{}');
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
    } catch (_error) {
      return {};
    }
  }

  function write(store, state) {
    if (!store || typeof store.setItem !== 'function') return false;
    try {
      store.setItem(STORAGE_KEY, JSON.stringify(state));
      return true;
    } catch (_error) {
      return false;
    }
  }

  function shouldShow(kind, provided) {
    if (!known(kind)) return false;
    const state = read(browserStorage(provided));
    return state[`dismissed:${kind}`] !== true;
  }

  function dismiss(kind, provided) {
    if (!known(kind)) return false;
    const store = browserStorage(provided);
    const state = read(store);
    state[`dismissed:${kind}`] = true;
    return write(store, state);
  }

  function show(kind, provided) {
    if (!known(kind)) return false;
    const store = browserStorage(provided);
    const state = read(store);
    delete state[`dismissed:${kind}`];
    return write(store, state);
  }

  function reset(provided) {
    const store = browserStorage(provided);
    if (!store || typeof store.removeItem !== 'function') return false;
    try {
      store.removeItem(STORAGE_KEY);
      return true;
    } catch (_error) {
      return false;
    }
  }

  root.BobaOnboarding = { STORAGE_KEY, shouldShow, dismiss, show, reset };
}(typeof window !== 'undefined' ? window : this));
