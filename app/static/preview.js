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
  const statList = el('ul', 'stats');
  [
    [stats.drinks || 0, 'drinks'],
    [stats.people || 0, 'people'],
    [stats.rows || 0, 'rows read'],
    [stats.warnings || 0, 'to check'],
  ].forEach(([value, label]) => {
    const item = el('li');
    item.append(el('b', null, String(value)), el('span', null, label));
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
  ['#', 'Name', 'Drink', 'Size', 'Sugar', 'Ice', 'Toppings', 'Milk', 'Qty']
    .forEach((label) => headRow.append(el('th', null, label)));
  head.append(headRow);
  table.append(head);

  const tbody = el('tbody');
  rows.forEach((row) => {
    const worst = row.ok
      ? (row.issues.some((i) => i.level === 'warning') ? 'warn' : '')
      : 'bad';
    const tr = el('tr', worst);
    tr.append(el('td', 'row-no', String(row.row_number)));
    tr.append(el('td', 'who', row.person || '—'));
    const drink = el('td');
    drink.append(document.createTextNode(row.drink || '—'));
    if (row.notes) drink.append(el('div', 'why', row.notes));
    tr.append(drink);
    tr.append(el('td', null, row.size || row.temperature || ''));
    tr.append(el('td', null, row.sugar || ''));
    tr.append(el('td', null, row.ice || ''));
    tr.append(el('td', null, (row.toppings || []).join(', ')));
    tr.append(el('td', null, row.milk || ''));
    tr.append(el('td', 'qty', String(row.quantity)));
    tbody.append(tr);

    if (row.issues && row.issues.length) {
      const noteRow = el('tr', worst);
      const cell = el('td', 'why');
      cell.colSpan = 9;
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
        outcome.className = 'status';
        outcome.textContent = '';
        outcome.append(el('strong', null, 'Your order is ready and waiting.'));
        outcome.append(document.createTextNode(
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
