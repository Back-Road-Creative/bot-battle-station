// Exercise actual page handlers with a minimal DOM, without a browser dependency.
const fs = require("fs");
const assert = require("assert");
const js = fs.readFileSync(process.argv[2], "utf8").split("<script>").pop().split("</script>")[0];
const els = new Map();
function E() {
  return {
    className: "", dataset: {}, style: {setProperty() {}}, children: [], attrs: {}, parentNode: null,
    classList: {contains() { return false; }},
    append(...nodes) { for (const node of nodes) this.insertBefore(node, null); },
    prepend(...nodes) { for (const node of [...nodes].reverse()) this.insertBefore(node, this.firstChild); },
    insertBefore(node, ref) {
      if (node.parentNode) node.remove();
      const index = ref == null ? this.children.length : this.children.indexOf(ref);
      assert(index >= 0);
      this.children.splice(index, 0, node); node.parentNode = this;
    },
    replaceChildren(...nodes) { for (const child of [...this.children]) child.remove?.(); this.children = []; this.append(...nodes); },
    remove() {
      if (!this.parentNode) return;
      this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1);
      this.parentNode = null;
      if (document.activeElement === this) document.activeElement = null;
    },
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
    get firstChild() { return this.children[0]; },
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
const stableTile = grid.children[0], stableTitle = stableTile.children[2];
page.render();
assert.equal(grid.children[0], stableTile, "an unchanged session keeps its tile node across polls");
assert.equal(grid.children[0].children[2], stableTitle, "unchanged content is not rebuilt");
assert.equal(document.activeElement, stableTile, "a focused tile must survive the next poll");
grid.children[0].onkeydown({key: " ", preventDefault() {}, stopPropagation() {}});
assert.equal(document.activeElement.className, "close", "opening details moves focus to Close");
const oldClose = document.activeElement;
page.render();
assert.equal(document.activeElement.className, "close", "Close keeps focus after refresh");
assert.equal(document.activeElement, oldClose, "Close keeps the same node across polls");
page.closeFocus();
assert.equal(document.activeElement, grid.children[0], "closing details restores tile focus");
const second = structuredClone(s); second.sid = "session-2"; second.name = "Second";
const frame = sessions => ({sessions, waits: {answered: 0}, alerts: null, defcon: {level: 5, why: "quiet"},
  bots: {}, load: "0", cores: 1, untested: []});
page.setData(frame([s, second]));
page.render();
const firstTile = grid.children[0], secondTile = grid.children[1];
s.title = "Updated build"; s.name = "Renamed";
page.render();
assert.equal(grid.children[0], firstTile, "changed data updates the existing tile");
assert.equal(firstTile.children[1].textContent, "Updated build");
assert.equal(firstTile.attrs["aria-label"], "Open Renamed session details");
firstTile.focus();
page.setData(frame([second, s]));
page.render();
assert.deepEqual(grid.children, [secondTile, firstTile], "reordering moves the same nodes");
assert.equal(document.activeElement, firstTile, "focus follows a reordered tile");
firstTile.onkeydown({key: "Enter", preventDefault() {}, stopPropagation() {}});
const close = document.activeElement, detail = els.get("focus").children[0];
s.summary = "The work changed";
page.render();
assert.equal(els.get("focus").children[0], detail, "detail tile keeps its identity when data changes");
assert.equal(document.activeElement, close, "detail updates keep the Close button itself");
assert(detail.children.some(child => child.className === "sum" && child.textContent === "The work changed"));
page.setData(frame([second]));
page.render();
assert.equal(els.get("focus").className, "", "details close when their session disappears");
assert.equal(document.activeElement, secondTile, "a vanished detail returns focus to a visible tile");
page.setData(frame([]));
page.render();
assert.equal(document.activeElement, els.get("help"), "a vanished last tile returns focus to Help");
const empty = grid.children[0];
page.render();
assert.equal(grid.children[0], empty, "empty state does not rebuild every poll");
console.log("keyboard ok");
