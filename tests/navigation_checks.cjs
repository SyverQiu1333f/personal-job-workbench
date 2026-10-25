// Node's standard library only; no browser or runtime package dependency.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../workbench/static/app.js'), 'utf8');

function environment() {
  const events = {}, windowEvents = {}, dialogEvents = {};
  const storage = new Map([['review-shown', '1']]);
  const buttons = [{disabled: false}, {disabled: false}];
  const editor = {isConnected: true, querySelectorAll: () => [buttons[0]]};
  const filter = {isConnected: true, querySelectorAll: () => [buttons[1]]};
  const opener = {expanded: 'false', setAttribute(name, value) { this.expanded = value; }};
  const parentText = {textContent: ''};
  const parentControl = {hidden: true, attrs: {}, dataset: {}, setAttribute(name, value) { this.attrs[name] = value; }, getAttribute(name) { return this.attrs[name]; }, querySelector: () => parentText};
  const dialog = {
    open: false, addEventListener(name, callback) { dialogEvents[name] = callback; },
    showModal() { this.open = true; }, close() { this.open = false; dialogEvents.close(); }
  };
  let confirms = 0, backs = 0;
  const document = {
    referrer: 'http://127.0.0.1/jobs', activeElement: null,
    documentElement: {style: {setProperty() {}}},
    addEventListener(name, callback) { (events[name] ||= []).push(callback); },
    getElementById: id => id === 'search-dialog' ? dialog : id === 'edit-panel' ? document.activePanel : null,
    querySelector: selector => selector === '[data-search-open]' ? opener : selector === '[data-module-parent]' ? parentControl : null,
    querySelectorAll: selector => selector === 'button' ? buttons : []
  };
  const sandbox = {
    document, URL, Set, JSON, console, scrollY: 0,
    HTMLElement: class {}, HTMLTextAreaElement: class {},
    location: {origin: 'http://127.0.0.1', pathname: '/jobs', search: '?view=', hash: '', href: 'http://127.0.0.1/jobs?view='},
    history: {length: 2, back() { backs++; }},
    sessionStorage: {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value)},
    window: {addEventListener(name, callback) { windowEvents[name] = callback; }},
    requestAnimationFrame: callback => callback(),
    confirm: () => { confirms++; return false; }
  };
  vm.createContext(sandbox); vm.runInContext(source, sandbox);
  events.DOMContentLoaded[0]();
  function emit(name, target, extra = {}) {
    const event = {target, prevented: false, preventDefault() { this.prevented = true; }, ...extra};
    (events[name] || []).forEach(callback => callback(event));
    return event;
  }
  return {editor, filter, buttons, opener, dialog, document, sandbox, windowEvents, emit, parentControl, parentText,
    confirms: () => confirms, backs: () => backs};
}

const dirty = environment();
dirty.emit('input', {closest: () => dirty.editor});
assert.equal(dirty.emit('submit', dirty.filter).prevented, true, 'cancel search submission while a different editor has pending input');
assert.equal(dirty.buttons[1].disabled, false, 'cancel keeps filter button usable');
assert.equal(dirty.confirms(), 1);
const leaving = {prevented: false, preventDefault() { this.prevented = true; }};
dirty.windowEvents.beforeunload(leaving);
assert.equal(leaving.prevented, true, 'cancelled filter must not disable later unsaved protection');
assert.equal(dirty.emit('submit', dirty.editor).prevented, false, 'saving the edited form remains possible');
assert.equal(dirty.confirms(), 1, 'saving the current editor needs no discard confirmation');
assert.equal(dirty.buttons[0].disabled, true, 'in-flight save blocks double click');
dirty.windowEvents.pageshow();
assert.equal(dirty.buttons[0].disabled, false, 'back navigation restores usable buttons');
dirty.editor.isConnected = false;
assert.equal(dirty.emit('submit', dirty.filter).prevented, false, 'removed HTMX panel does not leave phantom dirty state');

const clean = environment();
clean.emit('click', {closest: selector => selector === '[data-search-open]' ? clean.opener : null});
assert.equal(clean.dialog.open, true); assert.equal(clean.opener.expanded, 'true');
clean.emit('click', {closest: selector => selector === '[data-search-close]' ? {} : null});
assert.equal(clean.dialog.open, false); assert.equal(clean.opener.expanded, 'false');
const moduleBack = clean.emit('click', {closest: selector => ['[data-go-back]', '[data-module-parent]'].includes(selector) ? {} : null});
assert.equal(moduleBack.prevented, false, 'module parent follows its server-rendered link');
assert.equal(clean.backs(), 0, 'same-origin referrer must not override module hierarchy');
clean.document.referrer = 'https://external.example/';
const external = clean.emit('click', {closest: selector => selector === '[data-go-back]' ? {} : null});
assert.equal(external.prevented, false, 'external history uses the safe in-app fallback link');
assert.equal(clean.backs(), 0);
const inlinePanel = {id: 'edit-panel', cleared: false, querySelector: () => ({dataset: {moduleParentUrl: '/jobs?q=保留#job-1'}}), scrollIntoView() {}, replaceChildren() { this.cleared = true; clean.editor.isConnected = false; }};
clean.document.activePanel = inlinePanel;
clean.emit('htmx:afterSwap', inlinePanel);
assert.equal(clean.parentControl.hidden, false, 'an inline editing step exposes its module parent');
assert.equal(clean.parentControl.attrs.href, '/jobs?q=保留#job-1');
assert.equal(clean.parentText.textContent, '返回岗位列表');
clean.emit('input', {closest: () => clean.editor});
const inlineTarget = {closest: selector => selector === '[data-module-parent]' ? clean.parentControl : null};
clean.emit('click', inlineTarget);
assert.equal(inlinePanel.cleared, false, 'cancel keeps the inline draft and its parent button');
assert.equal(clean.parentControl.hidden, false);
clean.sandbox.confirm = () => true;
clean.emit('click', inlineTarget);
assert.equal(inlinePanel.cleared, true, 'parent closes the inline step even when its URL differs only by a hash');
assert.equal(clean.parentControl.hidden, true, 'the module root hides the button again');
assert.equal(clean.backs(), 0);
console.log('Navigation checks passed: unsaved input, submission, modal, and module-parent links.');
