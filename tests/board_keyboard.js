// Exercise actual page handlers with a minimal DOM, without a browser dependency.
const fs = require("fs");
const assert = require("assert");
const js = fs.readFileSync(process.argv[2], "utf8").split("<script>").pop().split("</script>")[0];
const els = new Map();
function E() {
  return {
    className: "", dataset: {}, style: {setProperty() {}}, children: [], attrs: {},
    classList: {contains() { return false; }},
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this.children = nodes; },
    setAttribute(key, value) { this.attrs[key] = value; },
    focus() { document.activeElement = this; },
    querySelector(selector) {
      for (const child of this.children) {
        if (child.className === selector.slice(1)) return child;
        const nested = child.querySelector?.(selector);
        if (nested) return nested;
      }
      return null;
    },
    get lastChild() { return this.children.at(-1); },
  };
}
globalThis.document = {
  activeElement: null, title: "", body: E(),
  getElementById: id => els.get(id) || els.set(id, E()).get(id),
  createElement: () => E(), createTextNode: text => ({textContent: text}),
  addEventListener() {}, querySelectorAll: () => [],
};
globalThis.location = {hash: "", pathname: "/", search: ""};
globalThis.history = {replaceState() {}};
globalThis.localStorage = {getItem() { return null; }, setItem() {}};
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;
globalThis.requestAnimationFrame = () => 0;
globalThis.fetch = () => new Promise(() => {});  // the page's initial poll is irrelevant to this handler test
globalThis.Notification = {permission: "denied"};
const page = new Function(js + ";return {tile, needs, render, closeFocus, setData(x) { data = x; }, setFocus(x) { focusSid = x; }, get focusSid() { return focusSid; }};")();
const s = {
  sid: "session-1", name: "Example", bot: "claude", state: "NEEDS YOU", title: "Build",
  ask: "Confirm build", waits_on: "", for_you: "", now: "", ago: "", progress: {pct: null, left: null, text: "?"},
  jobs: [], plan: null, meters: {working: null, shipped: 0, pace: null},
  tokens: {today: 0, helpers: 0, cache: 0}, timeline: [],
  context: {pct: 0, compactions: 0, used: 0, full: 1, basis: "assumed"},
  prs: [], prs_today: [], finished: 0, uptime: "1m", eta: "?", warn: [],
};
page.setData({sessions: [s]});
const tile = page.tile(s, false);
assert.equal(tile.attrs.role, "button");
assert.equal(tile.tabIndex, 0);
assert.match(tile.attrs["aria-label"], /Example/);
assert.equal(typeof tile.onkeydown, "function");
let prevented = false;
tile.onkeydown({key: "Enter", preventDefault() { prevented = true; }, stopPropagation() {}});
assert(prevented);
assert.equal(page.focusSid, s.sid);
page.needs([s]);
const ask = els.get("needs").children[1].children[0];
assert.equal(ask.attrs.role, "button");
assert.equal(ask.tabIndex, 0);
assert.equal(typeof ask.onkeydown, "function");
ask.focus();
page.needs([s]);
assert.equal(document.activeElement, els.get("needs").children[1].children[0], "an ask keeps focus after refresh");
s.state = "IDLE";
page.setFocus(null);
page.setData({sessions: [s], waits: {answered: 0}, alerts: null, defcon: {level: 5, why: "quiet"},
  bots: {}, load: "0", cores: 1, untested: []});
page.render();
const grid = els.get("grid");
grid.children[0].focus();
page.render();
assert.equal(document.activeElement, grid.children[0], "a focused tile must survive the next poll");
grid.children[0].onkeydown({key: " ", preventDefault() {}, stopPropagation() {}});
assert.equal(document.activeElement.className, "close", "opening details moves focus to Close");
const oldClose = document.activeElement;
page.render();
assert.equal(document.activeElement.className, "close", "Close keeps focus after refresh");
assert.notEqual(document.activeElement, oldClose);
page.closeFocus();
assert.equal(document.activeElement, grid.children[0], "closing details restores tile focus");
console.log("keyboard ok");
