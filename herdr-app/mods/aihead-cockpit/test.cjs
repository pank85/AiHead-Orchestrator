const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(`${__dirname}/main.js`, "utf8")
  .replace(/^import .*;\n/gm, "")
  .replace("export default class AiHeadCockpit", "class AiHeadCockpit")
  .concat("\nthis.Cockpit = AiHeadCockpit;");

// GPUI's children() is a method; keep the recorded nodes separately.
function ui() {
  const node = { nodes: [] };
  const proxy = new Proxy(node, { get(target, key) {
    if (key in target) return target[key];
    if (key === "child") return (value) => { target.nodes.push(value); return proxy; };
    if (key === "children") return (values) => { target.nodes.push(...values); return proxy; };
    if (key === "on_click") return (fn) => { target.click = fn; return proxy; };
    if (key === "id") return (id) => { target.id = id; return proxy; };
    if (key === "when") return (condition, fn) => condition ? fn(proxy) : proxy;
    return () => proxy;
  } });
  return proxy;
}
const calls = [];
let agentData = [];
let terminalData = [];
let spaceData = [];
let paneData = [];
const context = { View: class {}, div: ui, v_flex: ui, h_flex: ui,
  agents: () => agentData, terminals: () => terminalData, theme: () => ({}),
  spaces: () => spaceData, panes: () => paneData,
  run: (command, args) => calls.push([command, args]) };
vm.runInNewContext(source, context);
const cockpit = new context.Cockpit();
const flatten = (node) => typeof node === "string" ? [node] : [node, ...node.nodes.flatMap(flatten)];
let nodes = flatten(cockpit.render());
assert(nodes.some((node) => typeof node === "string" && node.includes("No reported agents")));
assert(nodes.includes("No space selected."));
assert(!nodes.some((node) => typeof node.id === "string" && node.id.startsWith("tab-")));
assert.equal(calls.length, 0);
spaceData = [
  { id: "remote/s1", name: "Project", active: true, machine: "remote", tabs: [{ id: "remote/tab1", name: "Review", status: "blocked" }] },
  { id: "s2", name: "Other", active: false, tabs: [] },
];
paneData = [
  { tab: "remote/tab1", terminal: "remote/t1" },
  { tab: "remote/tab1", terminal: null },
  { tab: "other", terminal: "t9" },
];
agentData = [{ address: "remote/t1", pane: "p1", status: "blocked", title: "Review", space_name: "Work" }];
terminalData = [
  { address: "remote/t2", parent: "remote/t1", state: "live", name: "Worker", status: { state: "working" } },
  { address: "remote/t1", parent: "remote/t0", state: "live" },
  { address: "remote/t3", parent: "remote/t1", state: "exited" },
];
nodes = flatten(cockpit.render());
assert(nodes.some((node) => typeof node === "string" && node.includes("blocked: 1")));
assert(nodes.includes("remote  |  1 tabs  |  1 terminal panes"));
assert(nodes.includes("Herdr live data - not an AiHead job list"));
assert(nodes.some((node) => node === "working"));
assert.equal(nodes.filter((node) => node.id === "child-remote/t1").length, 0);
assert.equal(nodes.filter((node) => node.id === "child-remote/t3").length, 0);
nodes.find((node) => node.id === "agent-remote/t1").click();
nodes.find((node) => node.id === "child-remote/t2").click();
nodes.find((node) => node.id === "tab-remote/tab1").click();
assert.equal(JSON.stringify(calls), JSON.stringify([
  ["pane.focus", { pane: "p1" }],
  ["terminal.peek", { terminal: "remote/t2" }],
  ["tab.select", { id: "remote/tab1" }],
]));
calls.length = 0;
agentData = [{ address: "t4", status: { state: "done" } }];
nodes = flatten(cockpit.render());
assert(nodes.some((node) => typeof node === "string" && node.includes("done: 1")));
nodes.find((node) => node.id === "agent-t4").click();
assert.equal(JSON.stringify(calls), JSON.stringify([["terminal.peek", { terminal: "t4" }]]));
calls.length = 0;
agentData = [{ address: "remote/h1", pane: "remote/p1", status: "idle" }];
paneData = [{ pane: "remote/p1", terminal: "remote/h1", tab: "remote/tab1" }];
terminalData = [
  { address: "remote/h1", state: "live", labels: { "aihead.role": "head", "aihead.project": "abc" } },
  { address: "h2", state: "live", labels: { "aihead.role": "head" }, parent: "h1" },
  { address: "h3", state: "finished", labels: { "aihead.role": "head" } },
  { address: "h4", state: "live", name: "aihead", labels: { "aihead.role": "worker" }, parent: "h1" },
  { address: "h5", state: "live", name: "aihead", parent: "h1" },
];
nodes = flatten(cockpit.render());
assert(nodes.includes("AiHead terminals (2)"));
assert(nodes.find((node) => node.id === "agent-remote/h1"));
assert(nodes.find((node) => node.id === "child-h2"));
assert(nodes.find((node) => node.id === "child-h4"));
assert(nodes.find((node) => node.id === "child-h5"));
assert(!nodes.find((node) => node.id === "aihead-h3" || node.id === "aihead-h4" || node.id === "aihead-h5"));
nodes.find((node) => node.id === "aihead-remote/h1").click();
nodes.find((node) => node.id === "aihead-h2").click();
assert.equal(JSON.stringify(calls), JSON.stringify([
  ["pane.focus", { pane: "remote/p1" }],
  ["terminal.peek", { terminal: "h2" }],
]));
console.log("Cockpit mock behavior passed");
