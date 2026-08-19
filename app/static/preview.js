/* Preview page: show what we read from the sheet, then hand it to the pipeline. */

const runId = window.location.pathname.split('/').filter(Boolean).pop();
const body = document.getElementById('body');

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const plural = (count, one, many) => `${count} ${count === 1 ? one : many || one + 's'}`;

function card(title) {
  const section = el('section', 'card');
  if (title) section.append(el('h2', null, title));
  body.append(section);
  return section;
}

function issueList(issues) {
  const list = el('ul', 'issues');
  issues.forEach((issue) => {
    const item = el('li', issue.level);
    item.append(el('span', 'badge ' + issue.level, issue.level));
    const text = issue.row ? `Row ${issue.row}: ${issue.message}` : issue.message;
    item.append(el('span', null, text));
    list.append(item);
  });
  return list;
}

const money = (value) => `$${(value || 0).toFixed(2)}`;

/* One option cell: what we resolved it to, and what the sheet said if that
   differs. A value we couldn't place is shown as typed and underlined, never
   blanked — the whole point is that the row still reaches the person.

   When the matched drink turns out not to offer it, the cell also carries the
   ways out: this drink's own options, one click each. */
function optionCell(row, axis, resolved, raw) {
  const cell = el('td');
  const typed = (raw || '').trim();
  const problems = ((row.match && row.match.unmapped) || []).filter((u) => u.axis === axis);
  // Asked for, and this drink simply hasn't got that axis — a slush has no ice
  // level. Nothing to pick, so say plainly that it isn't going in the order.
  const gone = ((row.match && row.match.dropped) || []).find((d) => d.axis === axis);
  if (gone) {
    const cell = el('td');
    cell.append(el('span', 'dropped', gone.asked));
    cell.append(el('span', 'was', 'not sent'));
    return cell;
  }
  // A row can ask for four toppings and only trip on the third, so the ones
  // that fitted stay shown as normal and only the odd one out gets a fixer.
  const kept = axis === 'toppings'
    ? ((row.canonical && row.canonical.toppings) || [])
        .filter((name) => !problems.some((p) => p.asked === name)).join(', ')
    : (problems.length ? '' : resolved);

  if (kept) {
    cell.append(document.createTextNode(kept));
    if (typed && typed.toLowerCase() !== kept.toLowerCase()) {
      cell.append(el('span', 'was', typed));
    }
  } else if (problems.length) {
    cell.append(el('span', 'unresolved', problems.map((p) => p.asked).join(', ')));
  } else if (typed) {
    cell.append(el('span', 'unresolved', typed));
  } else {
    cell.append(el('span', 'default-value', '—'));
  }
  problems.forEach((problem) => cell.append(optionFixer(row, axis, problem)));
  return cell;
}

/* Send one row's correction and redraw from whatever comes back. */
async function editRow(rowNumber, changes, feedback) {
  if (feedback) feedback.textContent = 'Saving…';
  try {
    const response = await fetch(`/api/runs/${runId}/rows/${rowNumber}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(changes),
    });
    const data = await response.json();
    if (!data.ok) throw new Error(data.error || 'that didn\'t save');
    render(data.run, data.stages);
  } catch (error) {
    if (feedback) feedback.textContent = error.message;
  }
}

const setDrink = (rowNumber, drink, feedback) =>
  drink ? editRow(rowNumber, { drink }, feedback) : undefined;

/* What to send for one axis when somebody picks a replacement. Toppings are a
   list, so the pick swaps out the one entry that didn't fit and leaves the rest
   of the order alone; "" drops it and takes the store's default. */
function changeFor(row, axis, asked, picked) {
  if (axis !== 'toppings') return { [axis]: picked };
  const current = (row.canonical && row.canonical.toppings) || row.toppings || [];
  const next = current
    .map((name) => (name === asked ? picked : name))
    .filter(Boolean);
  return { toppings: next };
}

/* The fix-it control for a modifier this drink can't do: what it can do
   instead, plus the option of going without. Never applied on its own. */
function optionFixer(row, axis, problem) {
  const wrap = el('div', 'fixer');
  const feedback = el('div', 'why');
  const choices = ((row.match && row.match.choices) || {})[axis] || [];
  const line = el('div', 'suggest-line');

  const pick = (value) => editRow(
    row.row_number, changeFor(row, axis, problem.asked, value), feedback);

  if (problem.asked) line.append(el('span', 'why', `${problem.asked} →`));
  if (choices.length > 6) {
    // 21 toppings is a menu, not a row of buttons.
    const select = el('select', 'drink-input');
    select.setAttribute('aria-label', `Choose the ${axis} for row ${row.row_number}`);
    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = 'pick one…';
    select.append(blank);
    choices.forEach((name) => {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      select.append(option);
    });
    select.addEventListener('change', () => { if (select.value) pick(select.value); });
    line.append(select);
  } else {
    choices.forEach((name) => {
      const button = el('button', 'chip', name);
      button.type = 'button';
      button.addEventListener('click', () => pick(name));
      line.append(button);
    });
  }

  const drop = el('button', 'chip apply', choices.length ? 'Leave it out' : 'Clear');
  drop.type = 'button';
  drop.addEventListener('click', () => pick(''));
  line.append(drop);

  wrap.append(line, feedback);
  return wrap;
}

/* The drink cell for a row we couldn't match: what they wrote, the near
   misses as one-click buttons, and the full menu behind a picker. Nothing is
   applied without someone choosing it. */
function drinkFixer(row) {
  const wrap = el('div', 'fixer');
  const feedback = el('div', 'why');
  const suggestions = (row.suggestions && row.suggestions.drink) || [];

  if (suggestions.length) {
    const line = el('div', 'suggest-line');
    line.append(el('span', 'why', 'Did you mean'));
    suggestions.forEach((name) => {
      const button = el('button', 'chip', name);
      button.type = 'button';
      button.addEventListener('click', () => setDrink(row.row_number, name, feedback));
      line.append(button);
    });
    wrap.append(line);
  }

  const picker = el('div', 'suggest-line');
  const input = el('input', 'drink-input');
  const listId = `menu-drinks-${row.row_number}`;
  const datalist = el('datalist');
  datalist.id = listId;
  input.setAttribute('list', listId);
  input.setAttribute('placeholder',
    suggestions.length ? 'or search the menu…' : 'search the menu…');
  input.setAttribute('aria-label', `Choose the drink for row ${row.row_number}`);
  input.setAttribute('autocomplete', 'off');

  // Fuzzy search on the server, so the box offers a handful of near matches
  // rather than all 135 drinks. Debounced, and last-response-wins so a slow
  // reply for "ta" can't overwrite the results for "taro".
  let timer = null;
  let latest = 0;
  input.addEventListener('input', () => {
    const text = input.value.trim();
    window.clearTimeout(timer);
    if (!text) { datalist.textContent = ''; return; }
    timer = window.setTimeout(async () => {
      const mine = ++latest;
      try {
        const response = await fetch(`/api/drinks?q=${encodeURIComponent(text)}&limit=6`);
        const data = await response.json();
        if (mine !== latest) return;
        datalist.textContent = '';
        (data.drinks || []).forEach((name) => {
          const option = document.createElement('option');
          option.value = name;
          datalist.append(option);
        });
      } catch (error) {
        /* the box still takes a typed name; the search is a convenience */
      }
    }, 120);
  });

  const apply = el('button', 'chip apply', 'Use this');
  apply.type = 'button';
  const submit = () => setDrink(row.row_number, input.value.trim(), feedback);
  apply.addEventListener('click', submit);
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); submit(); }
  });
  picker.append(input, apply, datalist);
  wrap.append(picker, feedback);
  return wrap;
}

function sourceText(source) {
  if (!source) return 'your sheet';
  if (source.kind === 'google_sheet') return 'your Google Sheet';
  return source.filename ? `"${source.filename}"` : 'your file';
}

function render(run, stages) {
  body.textContent = '';
  const stats = run.stats || {};
  const rows = run.rows || [];
  const fatal = (run.issues || []).some((issue) => issue.level === 'error');

  document.getElementById('source-line').textContent = fatal
    ? `We couldn't read ${sourceText(run.source)}.`
    : `Read ${plural(stats.drinks || 0, 'drink')} for ${plural(stats.people || 0, 'person', 'people')} `
      + `from ${sourceText(run.source)}.`;

  /* --- anything that stops us -------------------------------------------- */
  if (fatal) {
    const problem = card('That sheet didn\'t come through');
    problem.append(issueList((run.issues || []).filter((i) => i.level === 'error')));
    problem.append(el('p', 'muted',
      'The sheet needs a row of column titles with at least Name and Drink, and one row '
      + 'per drink underneath. The template on the import page is already set up that way.'));
    const actions = el('div', 'actions');
    const back = el('a', 'btn primary', 'Back to import');
    back.href = '/';
    actions.append(back);
    const template = el('a', 'btn', 'Download the template');
    template.href = '/template.csv';
    actions.append(template);
    problem.append(actions);
    return;
  }

  /* --- summary ------------------------------------------------------------ */
  const summary = card();
  const matched = run.match || null;
  const statList = el('ul', 'stats');
  const tiles = [
    [String(stats.drinks || 0), 'drinks'],
    [String(stats.people || 0), 'people'],
    [String(stats.rows || 0), 'rows read'],
  ];
  if (matched) {
    tiles.push([`${matched.ready || 0}/${(run.rows || []).length}`, 'on the menu']);
    tiles.push([money(matched.subtotal), 'before tax']);
  } else {
    tiles.push([String(stats.warnings || 0), 'to check']);
  }
  tiles.forEach(([value, label]) => {
    const item = el('li');
    item.append(el('b', null, value), el('span', null, label));
    statList.append(item);
  });
  summary.append(statList);

  const notes = (run.issues || []).filter((issue) => issue.level !== 'error');
  if (notes.length) summary.append(issueList(notes));

  const map = run.column_map || {};
  const mapped = Object.keys(map);
  if (mapped.length) {
    const detail = el('details');
    detail.append(el('summary', null, 'Which columns we used'));
    const list = el('div', 'pill-list');
    mapped.forEach((field) => {
      const pill = el('span', 'pill', map[field] === field ? field : `${map[field]} → ${field}`);
      list.append(pill);
    });
    detail.append(list);
    summary.append(detail);
  }

  /* --- the order ---------------------------------------------------------- */
  const orderCard = card('The order');
  const scroll = el('div', 'table-scroll');
  const table = el('table', 'orders');
  const head = el('thead');
  const headRow = el('tr');
  const columns = ['#', 'Name', 'Drink', 'Size', 'Sugar', 'Ice', 'Toppings', 'Milk', 'Qty'];
  if (matched) columns.push('Price');
  columns.forEach((label) => headRow.append(el('th', null, label)));
  head.append(headRow);
  table.append(head);

  const tbody = el('tbody');
  rows.forEach((row) => {
    const worst = row.ok
      ? (row.issues.some((i) => i.level === 'warning') ? 'warn' : '')
      : 'bad';
    const tr = el('tr', worst);
    const canonical = row.canonical || {};
    const found = row.match || {};
    // The item the cart will actually order, once there is one. Until then the
    // name the sheet gave, resolved as far as it went.
    const item = found.item || null;
    const name = (item && item.name) || canonical.drink;

    tr.append(el('td', 'row-no', String(row.row_number)));
    tr.append(el('td', 'who', row.person || '—'));
    const drink = el('td');
    if (name) {
      drink.append(document.createTextNode(name));
      if (row.drink && row.drink.toLowerCase() !== name.toLowerCase()) {
        drink.append(el('span', 'was', row.drink));
      }
      if (item && item.category) drink.append(el('span', 'was', item.category));
    } else if (row.drink) {
      drink.append(el('span', 'unresolved', row.drink));
    } else {
      drink.append(el('span', 'default-value', 'no drink'));
    }
    if (row.notes) drink.append(el('div', 'why', row.notes));
    // Nothing on the menu matched, so offer the ways out.
    if (!name) drink.append(drinkFixer(row));
    tr.append(drink);
    tr.append(optionCell(row, 'size', canonical.size, row.size || row.temperature));
    tr.append(optionCell(row, 'sugar', canonical.sugar, row.sugar));
    tr.append(optionCell(row, 'ice', canonical.ice, row.ice));
    tr.append(optionCell(row, 'toppings', (canonical.toppings || []).join(', '),
                         (row.toppings || []).join(', ')));
    tr.append(optionCell(row, 'milk', canonical.milk, row.milk));
    tr.append(el('td', 'qty', String(row.quantity)));
    if (matched) {
      tr.append(el('td', 'qty', found.status === 'ready' ? money(found.total) : '—'));
    }
    tbody.append(tr);

    if (row.issues && row.issues.length) {
      const noteRow = el('tr', worst);
      const cell = el('td', 'why');
      cell.colSpan = columns.length;
      cell.textContent = row.issues.map((issue) => issue.message).join(' · ');
      noteRow.append(cell);
      tbody.append(noteRow);
    }
  });
  table.append(tbody);
  scroll.append(table);
  orderCard.append(scroll);

  if (stats.errors) {
    orderCard.append(el('p', 'muted',
      `${plural(stats.errors, 'row')} can't be ordered and will be skipped. `
      + 'Fix them in the sheet and import again if that\'s not what you want.'));
  }

  const extras = rows.some((row) => Object.keys(row.extra || {}).length);
  if (extras) {
    orderCard.append(el('p', 'muted',
      'Columns we didn\'t recognise were kept with the order, not dropped.'));
  }

  orderCard.append(el('p', 'muted',
    'Values are shown as this store names them, with what your sheet said underneath. '
    + 'A dash means no choice was made, so the store\'s default is used. Anything '
    + 'underlined we couldn\'t find on the menu — it\'s still here, just check it.'));

  if (matched) {
    const captured = matched.menu_captured ? ` captured ${matched.menu_captured}` : '';
    orderCard.append(el('p', 'muted',
      `Matched against ${matched.menu_items} drinks on the ${matched.store} menu${captured}. `
      + 'Prices are that menu\'s, before tax and fees — the real total comes from the '
      + 'store when the cart is built.'));
  }

  /* --- next step ---------------------------------------------------------- */
  const next = card('Next: build the cart');
  const pending = (stages || []).filter((stage) => !stage.ready);

  const actions = el('div', 'actions');
  const go = el('button', 'btn primary', 'Build the cart');
  actions.append(go);
  const again = el('a', 'btn ghost', 'Import a different sheet');
  again.href = '/';
  actions.append(again);
  const download = el('a', 'btn ghost', 'Download as JSON');
  download.href = `/api/runs/${runId}`;
  download.setAttribute('download', `boba-order-${runId}.json`);
  actions.append(download);
  next.append(actions);

  const outcome = el('div', 'status');
  outcome.style.marginTop = '1rem';
  next.append(outcome);

  if (stages && stages.length) {
    const list = el('ul', 'stage-list');
    stages.forEach((stage) => {
      const item = el('li', stage.ready ? 'ready' : '');
      item.append(el('span', 'dot'));
      item.append(el('span', null, `${stage.description}${stage.ready ? '' : ' — not built yet'}`));
      list.append(item);
    });
    next.append(list);
  }

  go.addEventListener('click', async () => {
    go.disabled = true;
    outcome.className = 'status busy';
    outcome.textContent = 'Handing the order to the cart builder…';
    try {
      const response = await fetch(`/api/runs/${runId}/process`, { method: 'POST' });
      const data = await response.json();
      if (data.ok) {
        outcome.className = 'status';
        outcome.textContent = '';
        const handoff = data.run && data.run.handoff_url;
        if (handoff) {
          // Task 4 has landed: show the cart link rather than assuming a page
          // exists to show it on.
          outcome.append(el('strong', null, 'Your cart is ready.'));
          const link = el('a', 'btn primary', 'Open the cart');
          link.href = handoff;
          link.rel = 'noopener';
          link.target = '_blank';
          outcome.append(link);
        } else {
          outcome.textContent = 'The pipeline ran. Reload to see the result.';
        }
      } else if (data.pending) {
        // Everything up to the missing stage ran; show that rather than only
        // the apology for what didn't.
        if (data.run) render(data.run, data.stages);
        const spot = document.querySelector('.status');
        if (!spot) return;
        spot.className = 'status';
        spot.textContent = '';
        spot.append(el('strong', null, 'Your order is matched and waiting.'));
        spot.append(document.createTextNode(
          `The next step (${pending.length ? pending[0].description.toLowerCase() : 'the cart builder'}) `
          + 'isn\'t built yet. Nothing was lost — this import is saved and will flow straight '
          + 'through once it lands.'));
      } else {
        outcome.className = 'status err';
        outcome.textContent = data.error || 'That didn\'t work.';
      }
    } catch (error) {
      outcome.className = 'status err';
      outcome.textContent = 'Couldn\'t reach the server: ' + error.message;
    } finally {
      go.disabled = false;
    }
  });
}

fetch(`/api/runs/${runId}`)
  .then(async (response) => {
    const data = await response.json();
    if (!response.ok || !data.run) throw new Error(data.error || 'not found');
    render(data.run, data.stages);
  })
  .catch((error) => {
    body.textContent = '';
    const section = card('That import isn\'t here any more');
    section.append(el('p', 'muted', error.message));
    const back = el('a', 'btn primary', 'Back to import');
    back.href = '/';
    section.append(back);
  });
