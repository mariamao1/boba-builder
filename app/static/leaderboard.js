/* Shared rendering for the public ranking and store-compatible quick picks. */

(function (root) {
  'use strict';

  function normalize(value) {
    return String(value || '').toLocaleLowerCase().replace(/\s+/g, ' ').trim();
  }

  function quickPicks(entries, menu, limit) {
    var items = Array.isArray(menu) ? menu : ((menu && menu.items) || []);
    var byName = {};
    items.forEach(function (item) {
      var key = normalize(item && item.name);
      if (key && !byName[key]) byName[key] = item;
    });
    var used = {};
    var result = [];
    (entries || []).some(function (entry) {
      var key = normalize(entry && entry.drink);
      var item = byName[key];
      if (!item || used[key]) return false;
      used[key] = true;
      result.push({
        drink: item.name,
        drinks: Number(entry.drinks || 0),
        orders: Number(entry.orders || 0),
        item: item,
      });
      return result.length >= (limit || 5);
    });
    return result;
  }

  function countText(count) {
    return count + (count === 1 ? ' cup' : ' cups');
  }

  function renderBoard(doc, list, leaderboard) {
    list.textContent = '';
    var entries = (leaderboard && leaderboard.entries) || [];
    if (!entries.length) {
      var empty = doc.createElement('li');
      empty.className = 'leaderboard-empty';
      empty.textContent = 'No group orders yet';
      list.append(empty);
      return;
    }
    entries.forEach(function (entry, index) {
      var row = doc.createElement('li');
      row.className = 'leaderboard-row';
      row.setAttribute('aria-label',
        'Number ' + (index + 1) + ', ' + entry.drink + ', '
        + countText(Number(entry.drinks || 0)));

      var rank = doc.createElement('span');
      rank.className = 'leaderboard-rank';
      rank.textContent = String(index + 1);
      rank.setAttribute('aria-hidden', 'true');
      var drink = doc.createElement('strong');
      drink.className = 'leaderboard-drink';
      drink.textContent = entry.drink;
      var count = doc.createElement('span');
      count.className = 'leaderboard-count';
      count.textContent = '×' + Number(entry.drinks || 0);
      row.append(rank, drink, count);
      list.append(row);
    });
  }

  function renderQuickPicks(doc, panel, list, picks, onPick) {
    list.textContent = '';
    panel.hidden = !picks.length;
    if (!picks.length) return;
    picks.forEach(function (pick) {
      var button = doc.createElement('button');
      button.type = 'button';
      button.className = 'popular-pick';
      button.setAttribute('aria-label', 'Choose ' + pick.drink + ', ' + countText(pick.drinks));
      var name = doc.createElement('strong');
      name.textContent = pick.drink;
      var count = doc.createElement('span');
      count.textContent = '×' + pick.drinks;
      button.append(name, count);
      button.addEventListener('click', function () { onPick(pick.item); });
      list.append(button);
    });
  }

  root.BobaLeaderboard = {
    normalize: normalize,
    quickPicks: quickPicks,
    renderBoard: renderBoard,
    renderQuickPicks: renderQuickPicks,
  };
}(typeof window === 'undefined' ? this : window));
