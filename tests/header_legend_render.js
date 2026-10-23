// Exercise the page's real header renderer with Claude and Codex rows.
const assert = require("assert");
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const script = html.split("<script>").pop().split("</script>")[0];
const els = new Map();
function E() {
  return {children: [], className: "", textContent: "", title: "", style: {},
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this.children = nodes; }};
}
globalThis.document = {getElementById: id => els.get(id) || els.set(id, E()).get(id),
  createElement: () => E(), createTextNode: text => ({textContent: text}), addEventListener() {}};
globalThis.location = {hash: "", search: ""};
globalThis.localStorage = {getItem() { return null; }};
globalThis.setInterval = () => 0;
globalThis.fetch = () => new Promise(() => {});
const page = new Function(script + ";return {header, setData(x) { data = x; }};")();
const rows = [
  {bot: "claude", state: "RUNNING", plan: null, progress: {left: null}, tokens: {today: 1000, cache: 0},
    jobs: [], prs_today: [], context: {used: 100000, full: 200000}},
  {bot: "codex", state: "IDLE", plan: null, progress: {left: null}, tokens: {today: 0, cache: 0},
    jobs: [], prs_today: [], context: {used: 50000, full: 100000}},
];
page.setData({codex_count: 1, waits: {answered: 0, median: null, longest: null}, alerts: null, load: "1", cores: 4});
page.header(rows);
const stats = els.get("stats").children.map(s => ({value: s.children[0].textContent, label: s.children[1].textContent, title: s.title}));
const byLabel = Object.fromEntries(stats.map(s => [s.label, s]));
assert.equal(byLabel.sessions.value, 2);
assert.match(byLabel.sessions.title, /Claude Code and Codex/);
assert.equal(byLabel.codex.value, 1);
assert.equal(byLabel["context in use"].value, "50%");
assert.match(byLabel["context in use"].title, /150k of 300k/);
const legend = html.split('id="legend"')[1].split("</div></div>")[0];
const documented = [...legend.matchAll(/<tr><td>([^<]+)<\/td>/g)].map(match => match[1]);
for (const stat of stats) assert(documented.includes(stat.label), `No legend row for ${stat.label}`);
console.log("header and legend render ok");
