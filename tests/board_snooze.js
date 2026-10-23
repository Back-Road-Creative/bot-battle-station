// Run the real page handlers against a small DOM to verify per-session alert snooze.
const fs = require("fs");
const assert = require("assert");
const js = fs.readFileSync(process.argv[2], "utf8").split("<script>").pop().split("</script>")[0];
let now = 1_000_000;
Date.now = () => now;
const saved = new Map();
saved.set("siren", "on");
const notices = [];
let wails = 0;
function E(className = "") {
  return {
    className, dataset: {}, children: [], attrs: {}, style: {setProperty() {}},
    classList: {contains(name) { return this.className.split(" ").includes(name); }},
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this.children = nodes; },
    setAttribute(key, value) { this.attrs[key] = value; },
    focus() { document.activeElement = this; },
  };
}
const els = new Map();
globalThis.document = {
  activeElement: null, title: "", body: E(),
  getElementById: id => els.get(id) || els.set(id, E()).get(id),
  createElement: () => E(), createTextNode: text => ({textContent: text}),
  addEventListener() {}, querySelectorAll: () => [],
};
globalThis.location = {hash: "", pathname: "/", search: ""};
globalThis.history = {replaceState() {}};
globalThis.localStorage = {getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value)};
globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;
globalThis.requestAnimationFrame = () => 0;
globalThis.fetch = () => new Promise(() => {});
globalThis.Notification = function (title) { notices.push(title); };
Notification.permission = "granted";
class AudioContext {
  constructor() { this.state = "running"; this.currentTime = 0; this.destination = {}; }
  createOscillator() {
    wails++;
    return {frequency: {setValueAtTime() {}, exponentialRampToValueAtTime() {}},
      connect(node) { return node; }, start() {}, stop() {}};
  }
  createGain() {
    return {gain: {setValueAtTime() {}, exponentialRampToValueAtTime() {}}, connect() { return this; }};
  }
  resume() { return Promise.resolve(); }
}
globalThis.window = {Notification, AudioContext};
const page = new Function(js + ";return {needs, alarm, snooze, acknowledge, wakeAudio, setData(x) { data = x; }};")();
page.wakeAudio();
const a = {sid: "aaaaaaaa", name: "Alpha", state: "NEEDS YOU", ask: "Approve Alpha"};
const b = {sid: "bbbbbbbb", name: "Beta", state: "NEEDS YOU", ask: "Approve Beta"};
page.setData({sessions: [a, b], defcon: {level: 1, why: "2 sessions need you"}});
page.snooze(a.sid);
assert.equal(JSON.parse(saved.get("sessionSnoozes"))[a.sid], now + 15 * 60_000);
const reloaded = new Function(js + ";return {needs, setData(x) { data = x; }};")();
reloaded.setData({sessions: [a, b], defcon: {level: 1, why: "2 sessions need you"}});
reloaded.needs([a, b]);
assert.match(els.get("needs").children[1].children[1].textContent, /UNSNOOZE/, "reload preserves snooze");
page.needs([a, b]);
assert.equal(els.get("needs").children.length, 3, "both requests stay visible in the strip");
page.alarm([a, b]);
assert.equal(els.get("alert").className, "on", "the other session still takes over");
assert.equal(els.get("alert").children[0].textContent, "⚠ 1 NEEDS YOU");
assert.deepEqual(notices, ["⚠ Beta needs you"], "only the unsnoozed session notifies");
assert.equal(wails, 1, "the unsnoozed session sounds the siren");
const betaButton = els.get("alert").children[1].children[0].children[1];
betaButton.focus();
betaButton.onclick({stopPropagation() {}});
assert.equal(els.get("alert").className, "", "all snoozed requests hide the takeover");
assert.equal(wails, 1, "snoozed requests do not sound the siren");
assert.equal(document.title, "⚠ (2) needs you · Battle Station", "snooze does not hide requests");
assert.equal(document.activeElement, els.get("needs").children[2].children[1], "focus moves to visible unsnooze control");
now += 15 * 60_000 + 1;
page.alarm([a, b]);
assert.equal(els.get("alert").className, "on", "the takeover returns on expiry");
assert.deepEqual(notices, ["⚠ Beta needs you", "⚠ Alpha needs you", "⚠ Beta needs you"],
  "each request gets one new desktop notice on expiry");
assert.equal(wails, 2, "the siren returns when the snoozes expire");
assert.deepEqual(JSON.parse(saved.get("sessionSnoozes")), {}, "expired snoozes are removed");
page.acknowledge();
page.snooze(a.sid);
page.snooze(a.sid);
assert.equal(els.get("alert").className, "on", "manual unsnooze re-alerts even after an earlier acknowledgment");
const anotherPage = new Function(js + ";return {alarm, setData(x) { data = x; }};")();
anotherPage.setData({sessions: [a, b], defcon: {level: 1, why: "2 sessions need you"}});
anotherPage.alarm([a, b]);
const alphaButton = els.get("alert").children[1].children[0].children[1];
alphaButton.focus();
alphaButton.onclick({stopPropagation() {}});
assert.equal(els.get("alert").className, "on", "the other request remains in the takeover");
assert.equal(document.activeElement, els.get("alert").children[1].children[0].children[0],
  "focus moves to the remaining request when one alert is snoozed");
console.log("snooze ok");
