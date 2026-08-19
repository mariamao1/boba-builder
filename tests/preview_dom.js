/* Just enough DOM to run app/static/preview.js outside a browser.

   The preview page is where a wrong wire shows up as "clicking + opens the
   toppings list" — a class of bug no Python test can see, because it lives in
   which control reacts to which event. This shim records focus() calls and
   fetch() bodies so those questions have answers.

   Not a browser. It has no layout, no CSS, no real event dispatch and no
   bubbling: a test reaches for the node it means and fires the listener on it.
   That is enough to check what is wired to what, which is the failure mode.

   Driven by tests/test_preview_js.py; needs a JavaScript engine on the machine
   (macOS ships one with JavaScriptCore) and is skipped when there isn't one. */

function Node(tag) {
  this.tagName = tag;
  this.children = [];
  this.attrs = {};
  this.listeners = {};
  this.className = '';
  this.style = {};
  this.id = '';
  this.disabled = false;
  this._text = '';
  this._value = '';
}

Node.prototype.append = function () {
  for (var i = 0; i < arguments.length; i++) {
    var kid = arguments[i];
    if (typeof kid === 'string') kid = new TextNode(kid);
    kid.parent = this;
    this.children.push(kid);
  }
};

Object.defineProperty(Node.prototype, 'textContent', {
  get: function () {
    var out = this._text;
    for (var i = 0; i < this.children.length; i++) out += this.children[i].textContent;
    return out;
  },
  set: function (value) { this._text = value; this.children = []; },
});

Object.defineProperty(Node.prototype, 'value', {
  get: function () { return this._value; },
  set: function (value) { this._value = value; },
});

Node.prototype.setAttribute = function (name, value) { this.attrs[name] = value; };
Node.prototype.getAttribute = function (name) { return this.attrs[name]; };
Node.prototype.addEventListener = function (name, fn) {
  (this.listeners[name] = this.listeners[name] || []).push(fn);
};
Node.prototype.dispatch = function (name, event) {
  var fired = this.listeners[name] || [];
  for (var i = 0; i < fired.length; i++) {
    fired[i](event || { preventDefault: function () {}, key: '' });
  }
  return fired.length;
};
Node.prototype.focus = function () {
  focused.push(this.attrs['data-focus'] || ('<' + this.tagName + '>'));
};
Node.prototype.querySelector = function () { return null; };
Node.prototype.walk = function (visit) {
  visit(this);
  for (var i = 0; i < this.children.length; i++) {
    if (this.children[i].walk) this.children[i].walk(visit);
  }
};

function TextNode(text) { this.children = []; this._text = text; }
Object.defineProperty(TextNode.prototype, 'textContent', {
  get: function () { return this._text; },
  set: function (value) { this._text = value; },
});

var focused = [];      // every focus() the page asked for, in order
var posted = [];       // every save it sent
var nodes = {};

var document = {
  createElement: function (tag) { return new Node(tag); },
  createTextNode: function (text) { return new TextNode(text); },
  getElementById: function (id) {
    if (!nodes[id]) nodes[id] = new Node('div');
    return nodes[id];
  },
  querySelector: function (selector) {
    var wanted = /\[data-focus="([^"]*)"\]/.exec(selector);
    if (!wanted) return null;
    return byFocus(wanted[1]);
  },
};

var window = {
  location: { pathname: '/preview/testrun' },
  setTimeout: function () {},
  clearTimeout: function () {},
  alert: function (message) { alerted.push(message); },
};
var alerted = [];

/* The server, answering every save with the same run. The page's job here is to
   send the right change and redraw; what comes back is Python's business. */
var reply = null;
function fetch(url, init) {
  posted.push({ url: url, body: (init && init.body) || null });
  return Promise.resolve({
    ok: true,
    status: 200,
    json: function () { return Promise.resolve(reply); },
  });
}

function byFocus(key) {
  var hit = null;
  nodes.body.walk(function (node) {
    if (!hit && node.attrs && node.attrs['data-focus'] === key) hit = node;
  });
  return hit;
}

function byText(className, text) {
  var hit = null;
  nodes.body.walk(function (node) {
    if (!hit && node.className === className && node.textContent === text) hit = node;
  });
  return hit;
}

/* Load the page and put it in the mode the test wants. `let` inside eval() is
   scoped to the eval, so the mode is set by rewriting the declaration. */
function loadPreview(source, options) {
  source = source.replace(/fetch\(`\/api\/runs\/\$\{runId\}`\)[\s\S]*$/, '');
  if (options && options.editing) {
    source = source.replace('let editing = false;', 'let editing = true;');
  }
  return source;
}

function report(result) {
  print(JSON.stringify(result));
}
