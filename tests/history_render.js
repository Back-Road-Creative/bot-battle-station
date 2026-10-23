// Render the actual daily history tables against synthetic aggregates.
const assert = require("assert");
const fs = require("fs");
const script = fs.readFileSync(process.argv[2], "utf8").split("<script>").pop().split("</script>")[0];
const els = new Map();
function E() {
  return {children: [], className: "", textContent: "", style: {},
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this.children = nodes; }};
}
globalThis.document = {getElementById: id => els.get(id) || els.set(id, E()).get(id),
  createElement: () => E(), createTextNode: text => ({textContent: text}), addEventListener() {}};
globalThis.location = {hash: "", search: ""};
globalThis.localStorage = {getItem() { return null; }};
globalThis.setInterval = () => 0;
globalThis.fetch = () => new Promise(() => {});
const page = new Function(script + ";return {dayStats, setData(x) { data = x; }};")();
page.setData({alerts: {
  by_kind: {}, behind_by_job: {}, silent_by_type: {}, job_wait_by_hour: {},
  asks_by_hour: {}, compactions: {},
  asks_by_kind: {prompt: {count: 2, median: 15}},
  end_reasons: {"context near full": {condition_cleared: 1}},
}});
page.dayStats();
function text(node) { return [node.textContent || "", ...(node.children || []).map(text)].join(" "); }
const rendered = text(els.get("today"));
assert.match(rendered, /Asks, by kind/);
assert.match(rendered, /prompt/);
assert.match(rendered, /Warning endings/);
assert.match(rendered, /condition cleared/);
assert.match(rendered, /missing sessions/i);
console.log("history render ok");
