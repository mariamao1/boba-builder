/* Preview page: show what we read from the sheet, then hand it to the pipeline. */

const runId = window.location.pathname.split('/').filter(Boolean).pop();
const body = document.getElementById('body');

/* Review & edit turns the whole table into controls. It lives outside render()
   because every save redraws the page from the server's answer, and the mode
   has to survive that. */
let editing = false;

/* So does the focus, for the controls you click repeatedly — pressing + three
   times should not mean finding the button again each time.

   Asked for per save, never remembered: a control that merely HAD focus once
   must not get it back on an unrelated redraw. It did, briefly, and clicking +
   would hand focus to whichever search box you had last touched — which then
   popped its typeahead list open. */
let focusKey = null;

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

/* Name a control, so a save can ask for the focus to land back on it. */
function mark(node, key) {
  node.setAttribute('data-focus', key);
  return node;
}

function restoreFocus() {
  const wanted = focusKey;
  focusKey = null;
  if (!wanted || !document.querySelector) return;
  const node = document.querySelector(`[data-focus="${wanted}"]`);
  if (!node || node.disabled) return;
  node.focus();
}

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

/* Send one row's correction and redraw from whatever comes back.

   Redrawing the whole page rather than patching the cell is deliberate: one
   change moves the price, the totals, the row's notes and sometimes a
   sheet-level warning, and the server has already worked all of that out. */
async function saveRow(rowNumber, changes, feedback, focusAfter) {
  if (feedback) feedback.textContent = 'Saving…';
  focusKey = focusAfter || null;
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
    else window.alert(error.message);
  }
}

const setDrink = (rowNumber, drink, feedback) =>
  drink ? saveRow(rowNumber, { drink }, feedback) : undefined;

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

  const pick = (value) => saveRow(
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

/* A text box with a typeahead list behind it. Two sources, one control: the
   drink list is 149 names and is searched on the server, where the ranking
   knows that "ta" should reach Taro Slush; a drink's own topping list is twenty
   names and already in the run, so the browser filters it. */
function searchBox({ key, value, placeholder, label, list, action, onPick }) {
  const wrap = el('div', 'suggest-line');
  // Named but never re-focused after a save. These commit on blur or Enter, so
  // by the time one saves the user has moved on — and putting the caret back
  // would both fight the Tab that got them out and pop the list open again.
  const input = mark(el('input', 'drink-input'), key);
  const datalist = el('datalist');
  datalist.id = `list-${key}`;
  input.setAttribute('list', datalist.id);
  input.setAttribute('placeholder', placeholder || '');
  input.setAttribute('aria-label', label);
  input.setAttribute('autocomplete', 'off');
  if (value) input.value = value;

  const fill = (names) => {
    datalist.textContent = '';
    (names || []).forEach((name) => {
      const option = document.createElement('option');
      option.value = name;
      datalist.append(option);
    });
  };

  if (list) {
    fill(list);
  } else {
    // Debounced, and last-response-wins so a slow reply for "ta" can't
    // overwrite the results for "taro".
    let timer = null;
    let latest = 0;
    input.addEventListener('input', () => {
      const text = input.value.trim();
      window.clearTimeout(timer);
      if (!text) { fill([]); return; }
      timer = window.setTimeout(async () => {
        const mine = ++latest;
        try {
          const response = await fetch(`/api/drinks?q=${encodeURIComponent(text)}&limit=6`);
          const data = await response.json();
          if (mine === latest) fill(data.drinks || []);
        } catch (error) {
          /* the box still takes a typed name; the search is a convenience */
        }
      }, 120);
    });
  }

  // Commit on Enter, on picking from the list, and on leaving the box — but
  // only when the text actually changed, so tabbing through a table of these
  // doesn't fire a save per column.
  let sent = null;
  const submit = () => {
    const text = input.value.trim();
    if (text === (value || '') || text === sent) return;
    sent = text;
    onPick(text);
  };
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); submit(); }
  });
  input.addEventListener('change', submit);

  wrap.append(input, datalist);
  if (action) {
    const button = el('button', 'chip apply', action);
    button.type = 'button';
    button.addEventListener('click', submit);
    wrap.append(button);
  }
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

  wrap.append(searchBox({
    key: `fix-drink-${row.row_number}`,
    placeholder: suggestions.length ? 'or search the menu…' : 'search the menu…',
    label: `Choose the drink for row ${row.row_number}`,
    action: 'Use this',
    onPick: (name) => setDrink(row.row_number, name, feedback),
  }), feedback);
  return wrap;
}

/* --- review & edit -------------------------------------------------------- */
/* One block per order, not ten columns.

   The read-only table is text and shares a line out happily. Controls don't:
   nine of them need roughly 1200px before a dropdown stops clipping its own
   value, and the page is 920px, so a table can only end in a sideways scroll.
   Stacking the fields costs vertical space, which is free, and buys every
   control a readable width at any window size.

   What each control offers comes from the matched drink, because the store's
   vocabulary and one drink's vocabulary are very different things — a slush has
   no ice level at all. Until a row has a drink there is nothing to ask, so it
   falls back to the whole store's. */

function axisChoices(row, run, axis) {
  const available = (row.match && row.match.available) || null;
  if (available && available[axis] && available[axis].length) return available[axis];
  if (available) return [];   // matched, and this drink genuinely hasn't got it
  return ((run.match && run.match.vocabulary) || {})[axis] || [];
}

/* A labelled control. The label is a real <label> where there is a single
   control to point it at, so clicking the word focuses the thing under it. */
function field(labelText, control, options) {
  const box = el('div', 'field' + ((options && options.className) ? ' ' + options.className : ''));
  const label = el('label', 'field-label', labelText);
  if (options && options.id) {
    control.id = options.id;
    label.setAttribute('for', options.id);
  }
  box.append(label, control);
  return box;
}

function selectField(row, run, axis, labelText, current) {
  const choices = axisChoices(row, run, axis);
  if (!choices.length) {
    const none = el('div', 'field-empty', 'not on this drink');
    return field(labelText, none, { className: 'field-off' });
  }

  const key = `${row.row_number}-${axis}`;
  const select = mark(el('select', 'cell-input'), key);
  const blank = document.createElement('option');
  blank.value = '';
  blank.textContent = 'store default';
  select.append(blank);

  let listed = false;
  choices.forEach((name) => {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    if (name === current) { option.selected = true; listed = true; }
    select.append(option);
  });
  if (current && !listed) {
    // The sheet asked for something this drink hasn't got. Keep it selected and
    // named rather than snapping the dropdown to a value nobody chose.
    const option = document.createElement('option');
    option.value = current;
    option.textContent = `${current} — not on this drink`;
    option.selected = true;
    select.append(option);
  }
  // A dropdown can keep the focus: it is already where the user is looking,
  // and focusing a select doesn't make it drop open the way a typeahead does.
  select.addEventListener('change', () =>
    saveRow(row.row_number, { [axis]: select.value }, null, key));
  return field(labelText, select, { id: `row-${key}` });
}

function toppingsField(row, run) {
  const holder = el('div', 'topping-field');
  const current = (row.canonical && row.canonical.toppings) || [];
  const counts = (row.canonical && row.canonical.topping_quantities) || {};
  // Send the count back along with the name, so editing one topping doesn't
  // quietly turn somebody's "2x pudding" into one pudding.
  const asText = (name) => (counts[name] > 1 ? `${counts[name]}x ${name}` : name);
  const save = (names) => saveRow(row.row_number, { toppings: names });

  current.forEach((name) => {
    const chip = el('button', 'chip topping',
      counts[name] > 1 ? `${name} ×${counts[name]}` : name);
    chip.type = 'button';
    chip.setAttribute('aria-label', `Remove ${name} from row ${row.row_number}`);
    chip.append(el('span', 'remove', '✕'));
    chip.addEventListener('click', () =>
      save(current.filter((other) => other !== name).map(asText)));
    holder.append(chip);
  });

  const choices = axisChoices(row, run, 'toppings');
  if (!choices.length) {
    holder.append(el('span', 'field-empty',
      current.length ? '' : 'this drink doesn\'t take toppings'));
    return field('Toppings', holder, { className: 'grow' });
  }
  holder.append(searchBox({
    key: `${row.row_number}-toppings`,
    placeholder: current.length ? 'add another…' : 'add a topping…',
    label: `Add a topping to row ${row.row_number}`,
    list: choices,
    action: 'Add',
    onPick: (name) => save(current.map(asText).concat([name])),
  }));
  return field('Toppings', holder, { className: 'grow' });
}

function quantityField(row) {
  const stepper = el('div', 'stepper');
  // Each button keeps its own focus, and only its own: clicking + is a
  // quantity change and nothing else on the row should react to it.
  const step = (to, key) => saveRow(row.row_number, { quantity: to }, null, key);

  const downKey = `${row.row_number}-qty-down`;
  const down = mark(el('button', 'step', '−'), downKey);
  down.type = 'button';
  down.setAttribute('aria-label', `One fewer on row ${row.row_number}`);
  down.disabled = row.quantity <= 1;
  down.addEventListener('click', () => step(row.quantity - 1, downKey));

  const upKey = `${row.row_number}-qty-up`;
  const up = mark(el('button', 'step', '+'), upKey);
  up.type = 'button';
  up.setAttribute('aria-label', `One more on row ${row.row_number}`);
  up.disabled = row.quantity >= 20;
  up.addEventListener('click', () => step(row.quantity + 1, upKey));

  stepper.append(down, el('span', 'count', String(row.quantity)), up);
  return field('Qty', stepper, { className: 'field-qty' });
}

function nameField(row) {
  const input = mark(el('input', 'cell-input'), `${row.row_number}-person`);
  input.setAttribute('placeholder', 'nobody');
  input.value = row.person || '';
  input.addEventListener('change', () => {
    if (input.value.trim() !== (row.person || '')) {
      saveRow(row.row_number, { person: input.value.trim() });
    }
  });
  return field('Name', input, { id: `row-${row.row_number}-person`, className: 'field-name' });
}

function drinkField(row, item) {
  const holder = el('div', 'drink-field');
  holder.append(searchBox({
    key: `${row.row_number}-drink`,
    value: (item && item.name) || (row.canonical || {}).drink || row.drink || '',
    placeholder: 'search the menu…',
    label: `Drink for row ${row.row_number}`,
    action: 'Set',
    onPick: (name) => setDrink(row.row_number, name),
  }));
  if (item) {
    if (item.category) holder.append(el('span', 'was', item.category));
  } else {
    holder.append(el('span', 'unresolved',
      row.drink ? `${row.drink} — not on the menu` : 'no drink yet'));
    // The near misses are worth one click here too, not just in the read-only
    // view — this is the screen somebody came to to fix exactly this.
    const suggestions = (row.suggestions && row.suggestions.drink) || [];
    if (suggestions.length) {
      const line = el('div', 'suggest-line');
      suggestions.slice(0, 3).forEach((name) => {
        const button = el('button', 'chip', name);
        button.type = 'button';
        button.addEventListener('click', () => setDrink(row.row_number, name));
        line.append(button);
      });
      holder.append(line);
    }
  }
  return field('Drink', holder, { className: 'grow' });
}

function editCard(row, run, matched) {
  const item = (row.match && row.match.item) || null;
  const canonical = row.canonical || {};
  const found = row.match || {};
  const worst = row.ok
    ? (row.issues.some((issue) => issue.level === 'warning') ? ' warn' : '')
    : ' bad';
  const card = el('div', 'edit-row' + worst);

  const top = el('div', 'edit-line');
  top.append(el('span', 'row-no', String(row.row_number)));
  top.append(nameField(row), drinkField(row, item));
  if (matched) {
    const price = el('div', 'edit-price');
    price.append(el('span', 'field-label', 'Price'));
    price.append(el('span', null, found.status === 'ready' ? money(found.total) : '—'));
    top.append(price);
  }
  card.append(top);

  const options = el('div', 'edit-line');
  options.append(selectField(row, run, 'size', 'Size', canonical.size));
  options.append(selectField(row, run, 'sugar', 'Sugar', canonical.sugar));
  options.append(selectField(row, run, 'ice', 'Ice', canonical.ice));
  options.append(selectField(row, run, 'milk', 'Milk', canonical.milk));
  options.append(quantityField(row));
  card.append(options);

  const extras = el('div', 'edit-line');
  extras.append(toppingsField(row, run));
  card.append(extras);

  // Everything we did to this row, on as many lines as it takes.
  if (row.issues && row.issues.length) {
    const notes = el('ul', 'edit-notes');
    row.issues.forEach((issue) => {
      notes.append(el('li', issue.level, issue.message));
    });
    card.append(notes);
  }
  return card;
}

/* Everything we did to a row, under the row. Same in both modes: while you are
   editing is exactly when you want to see what the sheet said. */
function appendNotes(tbody, row, worst, width) {
  if (!row.issues || !row.issues.length) return;
  const noteRow = el('tr', worst);
  const cell = el('td', 'why');
  cell.colSpan = width;
  cell.textContent = row.issues.map((issue) => issue.message).join(' · ');
  noteRow.append(cell);
  tbody.append(noteRow);
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
  const orderCard = card();
  orderCard.id = 'check-order';
  const header = el('div', 'card-head');
  header.append(el('h2', null, editing ? 'Review & edit' : 'The order'));
  const toggle = el('button', 'btn' + (editing ? ' primary' : ''),
    editing ? 'Done editing' : 'Review & edit');
  toggle.type = 'button';
  toggle.addEventListener('click', () => {
    editing = !editing;
    render(run, stages);
  });
  header.append(toggle);
  orderCard.append(header);
  if (editing) {
    orderCard.append(el('p', 'muted',
      'Every change saves as you make it and the order re-matches, so the prices and '
      + 'the notes under each drink stay honest. What your sheet said is kept either way.'));
    const list = el('div', 'edit-list');
    rows.forEach((row) => list.append(editCard(row, run, matched)));
    orderCard.append(list);
    appendOrderFooter(orderCard, run, rows, stats, matched);
    return renderNextStep(run, stages);
  }

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
    appendNotes(tbody, row, worst, columns.length);
  });
  table.append(tbody);
  scroll.append(table);
  orderCard.append(scroll);
  appendOrderFooter(orderCard, run, rows, stats, matched);
  return renderNextStep(run, stages);
}

/* The small print under the order, the same in both views. */
function appendOrderFooter(orderCard, run, rows, stats, matched) {
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

  if (!editing) {
    orderCard.append(el('p', 'muted',
      'Values are shown as this store names them, with what your sheet said underneath. '
      + 'A dash means no choice was made, so the store\'s default is used. Anything '
      + 'underlined we couldn\'t find on the menu — it\'s still here, just check it.'));
  }

  if (matched) {
    const captured = matched.menu_captured ? ` captured ${matched.menu_captured}` : '';
    orderCard.append(el('p', 'muted',
      `Matched against ${matched.menu_items} drinks on the ${matched.store} menu${captured}. `
      + 'Prices are that menu\'s, before tax and fees — the real total comes from the '
      + 'store when the cart is built.'));
  }
}

function renderNextStep(run, stages) {
  const next = card('Next: build the cart');
  const pending = (stages || []).filter((stage) => !stage.ready);

  const actions = el('div', 'actions');
  const go = el('button', 'btn primary', 'Build the cart');
  actions.append(go);
  const again = el('a', 'btn ghost', '← Back to import');
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
          const back = el('a', 'btn ghost', '← Back to check order');
          back.href = '#check-order';
          const actions = el('div', 'actions');
          actions.append(link, back);
          outcome.append(actions);
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

  restoreFocus();
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
