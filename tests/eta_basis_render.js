// Exercise the actual job tooltip with and without finished-run history.
const assert = require("assert");
const fs = require("fs");
const page = fs.readFileSync(process.argv[2], "utf8").split("<script>").pop().split("</script>")[0];
function E() {
  return {className: "", children: [], style: {}, title: "", textContent: "",
    append(...nodes) { this.children.push(...nodes); }};
}
globalThis.document = {createElement: () => E(), createTextNode: text => ({textContent: text}),
  getElementById: () => E(), addEventListener() {}};
globalThis.location = {hash: "", search: ""};
globalThis.localStorage = {getItem() { return null; }};
globalThis.setInterval = () => 0;
globalThis.fetch = () => new Promise(() => {});
const {steps} = new Function(page + ";return {steps};")();
const job = (runs, when) => ({kind: "test", label: "suite", runs, when, watch: false, late: false, frac: null});
const tooltip = j => steps({plan: null, jobs: [j]}, 3).children[0].children[2].title;
assert.match(tooltip(job(2, "~5m left")), /2 finished runs/);
assert.match(tooltip(job(0, "first run · 1m")), /no finished runs/i);
assert.doesNotMatch(tooltip(job(0, "first run · 1m")), /~|based on 0/i);
console.log("ETA basis tooltip ok");
