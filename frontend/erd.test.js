"use strict";
const test = require("node:test");
const assert = require("node:assert");
const { layoutErd, nodeHeight, NODE_W } = require("./erd.js");

const node = (table, role, fields) => ({ table, role: role || "reached", fields: fields || [] });
const fk = (from, to) => ({ from, to, columns: ["x"], status: "fk", cardinality: "many-to-one" });

test("layers by distance from the start table", () => {
  const nodes = [node("c"), node("a", "start"), node("b")];
  const edges = [fk("a", "b"), fk("b", "c")];
  const { positions } = layoutErd(nodes, edges, "a");
  assert.ok(positions.a.x < positions.b.x && positions.b.x < positions.c.x);
});

test("unreachable nodes go in the last layer", () => {
  const nodes = [node("a", "start"), node("b"), node("z", "unreachable")];
  const edges = [fk("a", "b"), { from: "z", to: "a", columns: ["q"], status: "suggested", cardinality: "many-to-one" }];
  const { positions } = layoutErd(nodes, edges, "a");
  assert.ok(positions.z.x > positions.b.x);
});

test("positions of nodes in one layer do not overlap", () => {
  const nodes = [node("a", "start")];
  const edges = [];
  for (let i = 0; i < 6; i++) { nodes.push(node("t" + i, "reached", ["f1", "f2"])); edges.push(fk("a", "t" + i)); }
  const { positions } = layoutErd(nodes, edges, "a");
  const col = nodes.filter((n) => n.table !== "a").map((n) => ({ y: positions[n.table].y, h: nodeHeight(n) }));
  col.sort((p, q) => p.y - q.y);
  for (let i = 1; i < col.length; i++) assert.ok(col[i].y >= col[i - 1].y + col[i - 1].h);
});

test("order inside a layer is stable and by name", () => {
  const edges = [fk("a", "m"), fk("a", "b"), fk("a", "z")];
  const one = layoutErd([node("a", "start"), node("z"), node("m"), node("b")], edges, "a").positions;
  const two = layoutErd([node("b"), node("a", "start"), node("m"), node("z")], edges, "a").positions;
  assert.deepStrictEqual(one, two);
  assert.ok(one.b.y < one.m.y && one.m.y < one.z.y);
});

test("single node", () => {
  const r = layoutErd([node("a", "start")], [], "a");
  assert.deepStrictEqual(r.positions, { a: { x: 16, y: 16 } });
  assert.ok(r.width >= NODE_W && r.height >= nodeHeight(node("a")));
});

test("empty", () => {
  const r = layoutErd([], [], "");
  assert.deepStrictEqual(r.positions, {});
  assert.strictEqual(r.width, 0);
  assert.strictEqual(r.height, 0);
});

test("start table missing from the nodes puts everything in one layer", () => {
  const { positions } = layoutErd([node("a"), node("b")], [fk("a", "b")], "nope");
  assert.strictEqual(positions.a.x, positions.b.x);
});
