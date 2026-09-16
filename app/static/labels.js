/* Pickup labels: one card per cup, shared by the cart review and saved orders.

   The server builds the labels (`app/labels.py`); this file only renders the
   `GET .../labels` answer (`{labels, text, cups, unplaced, title}`) the same
   way on both pages: an on-screen checklist grouped by person for the runner
   at the counter, a copyable plain-text version for chat, and cut-out cards
   for print (see the `printing-labels` rules in app.css).
*/

var BobaLabels = (function () {
  'use strict';

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function cupText(label) {
    return 'Cup ' + label.cup + ' of ' + label.person_cups;
  }

  function cupCard(label, checks, onToggle) {
    var card = el('label', 'label-card');
    var box = document.createElement('input');
    box.type = 'checkbox';
    box.className = 'pickup-check';
    box.setAttribute('aria-label',
      'Handed out: ' + label.drink + ' for ' + label.person + ' (' + cupText(label) + ')');
    checks.push(box);
    box.addEventListener('change', function () {
      if (card.classList) {
        if (box.checked) card.classList.add('handed-out');
        else card.classList.remove('handed-out');
      }
      onToggle();
    });
    var body = el('span', 'label-body');
    var top = el('span', 'label-top');
    top.append(el('strong', 'label-person', label.person),
      el('span', 'label-seq', '#' + label.seq));
    body.append(top);
    body.append(el('span', 'label-drink', label.drink));
    body.append(el('span', 'label-spec muted', label.spec));
    if (label.person_cups > 1) body.append(el('span', 'label-cup muted', cupText(label)));
    if (label.notes) body.append(el('span', 'label-notes', 'Note: ' + label.notes));
    card.append(box, body);
    return card;
  }

  function setBodyClass(on) {
    if (!document.body || !document.body.classList) return;
    if (on) document.body.classList.add('printing-labels');
    else document.body.classList.remove('printing-labels');
  }

  function render(container, data) {
    container.textContent = '';
    var cups = data.labels || [];
    var total = data.cups == null ? cups.length : data.cups;

    var head = el('div', 'pickup-print-head');
    head.append(el('strong', null, data.title || 'Boba pickup'),
      el('span', null, total === 1 ? '1 cup' : total + ' cups'));
    container.append(head);

    var actions = el('div', 'actions pickup-actions');
    var copy = el('button', 'btn ghost compact', 'Copy labels');
    copy.type = 'button';
    var printButton = el('button', 'btn ghost compact', 'Print labels');
    printButton.type = 'button';
    actions.append(copy, printButton);
    container.append(actions);

    var progress = el('p', 'muted pickup-progress', '0 of ' + total + ' handed out');
    container.append(progress);

    var checks = [];
    var update = function () {
      var done = checks.filter(function (box) { return box.checked; }).length;
      progress.textContent = done + ' of ' + total + ' handed out';
    };

    var current = null;
    var group = null;
    cups.forEach(function (label) {
      if (label.person !== current) {
        current = label.person;
        group = el('section', 'label-group');
        var heading = el('h4', null, current);
        heading.append(el('span', 'label-count',
          label.person_cups === 1 ? '1 cup' : label.person_cups + ' cups'));
        group.append(heading);
        container.append(group);
      }
      group.append(cupCard(label, checks, update));
    });

    copy.addEventListener('click', function () {
      var text = data.text || '';
      var done = function (message) {
        copy.textContent = message;
        window.setTimeout(function () { copy.textContent = 'Copy labels'; }, 1800);
      };
      var fallback = function () {
        if (window.prompt) window.prompt('Copy these pickup labels:', text);
        else if (window.alert) window.alert(text);
      };
      try {
        if (window.navigator && window.navigator.clipboard
            && window.navigator.clipboard.writeText) {
          window.navigator.clipboard.writeText(text).then(function () {
            done('Copied!');
          }, fallback);
        } else fallback();
      } catch (_error) {
        fallback();
      }
    });

    printButton.addEventListener('click', function () {
      setBodyClass(true);
      var cleanup = function () { setBodyClass(false); };
      try {
        if (window.addEventListener) window.addEventListener('afterprint', cleanup);
        if (window.print) window.print();
      } finally {
        window.setTimeout(cleanup, 1000);
      }
    });

    if (data.unplaced) {
      container.append(el('p', 'attention-callout',
        data.unplaced + (data.unplaced === 1 ? ' drink was' : ' drinks were')
        + ' not placed and ' + (data.unplaced === 1 ? 'has' : 'have') + ' no label.'));
    }
    return container;
  }

  return { render: render };
})();
