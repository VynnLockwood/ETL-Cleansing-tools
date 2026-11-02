"use strict";
// Pure layout for the use case ERD. Loaded by usecases.html and by node --test.
(function (root) {
  var NODE_W = 170;
  var PAD = 16;
  var GAP_X = 150;
  var GAP_Y = 24;
  var HEAD_H = 24;
  var LINE_H = 14;

  function nodeHeight(node) {
    return HEAD_H + LINE_H * ((node.fields || []).length) + 6;
  }

  // nodes: [{table, role, fields}], edges: [{from, to, status}]
  // Layers by distance from the start table over foreign-key edges (undirected);
  // everything not reached that way goes into one extra last layer.
  function layoutErd(nodes, edges, startTable) {
    if (!nodes.length) return { positions: {}, width: 0, height: 0 };
    var byName = {};
    nodes.forEach(function (n) { byName[n.table] = n; });
    var adj = {};
    edges.forEach(function (e) {
      if (e.status !== "fk" || !byName[e.from] || !byName[e.to]) return;
      (adj[e.from] = adj[e.from] || []).push(e.to);
      (adj[e.to] = adj[e.to] || []).push(e.from);
    });
    var layer = {};
    var maxLayer = -1;
    if (byName[startTable]) {
      layer[startTable] = 0;
      maxLayer = 0;
      var queue = [startTable];
      while (queue.length) {
        var cur = queue.shift();
        (adj[cur] || []).sort().forEach(function (nx) {
          if (layer[nx] === undefined) {
            layer[nx] = layer[cur] + 1;
            if (layer[nx] > maxLayer) maxLayer = layer[nx];
            queue.push(nx);
          }
        });
      }
    }
    var rest = nodes.filter(function (n) { return layer[n.table] === undefined; });
    if (rest.length) {
      maxLayer += 1;
      rest.forEach(function (n) { layer[n.table] = maxLayer; });
    }
    var columns = [];
    for (var i = 0; i <= maxLayer; i++) columns.push([]);
    nodes.forEach(function (n) { columns[layer[n.table]].push(n); });
    var positions = {};
    var height = 0;
    columns.forEach(function (col, idx) {
      col.sort(function (p, q) { return p.table < q.table ? -1 : p.table > q.table ? 1 : 0; });
      var y = PAD;
      col.forEach(function (n) {
        positions[n.table] = { x: PAD + idx * (NODE_W + GAP_X), y: y };
        y += nodeHeight(n) + GAP_Y;
      });
      if (y - GAP_Y + PAD > height) height = y - GAP_Y + PAD;
    });
    var width = 2 * PAD + columns.length * NODE_W + (columns.length - 1) * GAP_X;
    return { positions: positions, width: width, height: height };
  }

  var api = { layoutErd: layoutErd, nodeHeight: nodeHeight, NODE_W: NODE_W, GAP_X: GAP_X };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Erd = api;
})(typeof window !== "undefined" ? window : this);
