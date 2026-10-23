// Run session-board.html in node with a local stub DOM: catches runtime errors a syntax check misses.
// This harness is maintained with the page contract; it has no downloaded JS dependencies.
// usage: node tests/session_board_render.js <page.html> <api.json> [offline]
//   default: render() the /api snapshot, print "render ok".
//   offline: fail every fetch while the clock moves 2 s per poll, then answer again; print when the
//            #link banner was up, as "t=<s> on|off" lines, so a test can check the delay and the all-clear.
const fs = require("fs");
const js = fs.readFileSync(process.argv[2], "utf8").split("<script>").pop().split("</script>")[0];
const api = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const els = new Map();
function E() {
  return new Proxy({style: {setProperty() {}}, dataset: {}, className: "", textContent: "", title: "",
    classList: {add() {}, remove() {}, toggle() {}, contains() { return false; }}, children: [], parentNode: null,
    append(...nodes) { for (const node of nodes) this.insertBefore(node, null); },
    prepend(...nodes) { for (const node of [...nodes].reverse()) this.insertBefore(node, this.children[0] || null); },
    replaceChildren(...nodes) { for (const child of [...this.children]) child.remove(); this.append(...nodes); },
    appendChild(node) { this.append(node); },
    insertBefore(node, ref) {
      node.remove();
      const index = ref == null ? this.children.length : this.children.indexOf(ref);
      this.children.splice(index, 0, node); node.parentNode = this;
    },
    remove() { if (this.parentNode) { this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1); this.parentNode = null; } },
    setAttribute() {}, querySelectorAll() { return []; },
    querySelector() { return E(); }, addEventListener() {},
    get lastChild() { return this.children.at(-1) || this._lastChild; }, _lastChild: {textContent: ""}},
  {get(t, k) { return k in t ? t[k] : undefined; }, set(t, k, v) { t[k] = v; return true; }});
}
globalThis.document = {getElementById: id => els.get(id) || els.set(id, E()).get(id), createElement: () => E(),
  createTextNode: () => E(), querySelectorAll: () => [], querySelector: () => E(), addEventListener() {}, title: "",
  body: E(), documentElement: E()};
globalThis.location = {hash: "", pathname: "/"}; globalThis.history = {replaceState() {}};
globalThis.localStorage = {getItem() { return null; }, setItem() {}}; globalThis.matchMedia = () => ({matches: false});
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0; globalThis.clearTimeout = () => {};
globalThis.requestAnimationFrame = () => 0; globalThis.Notification = {permission: "denied"};
let clock = 1e12, up = true;
Date.now = () => clock;
globalThis.fetch = async () => { if (!up) throw new Error("down"); return {json: async () => api}; };
const page = new Function(js + "\n;return {tick};")();
(async () => {
  try {
    if (process.argv[4] !== "offline") { await page.tick(); if (els.get("live").lastChild.textContent !== "LIVE") throw new Error(els.get("live").title || "not LIVE"); console.log("render ok"); return; }
    await page.tick();
    up = false;
    for (let t = 2; t <= 20; t += 2) { clock += 2000; await page.tick(); console.log(`t=${t} ${/\bon\b/.test(els.get("link").className) ? "on" : "off"}`); }
    up = true; clock += 2000; await page.tick();
    console.log(`back ${/\bon\b/.test(els.get("link").className) ? "on" : "off"}`);
  } catch (e) { console.log("RENDER ERROR", String(e.stack || e).split("\n").slice(0, 2).join(" | ")); process.exitCode = 1; }
})();
