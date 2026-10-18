// Checks core/static/core/js/tg_weights.js: node tools/weights_check.js
const assert = require('assert');
const W = require('../core/static/core/js/tg_weights.js');
const near = (a, b, msg) => assert(Math.abs(a - b) < 1e-9, `${msg}: ${a} != ${b}`);
const sum = (o) => Object.values(o).reduce((a, b) => a + b, 0);

let w = W.create({ a: 10, b: 5, c: 3, d: 2 });
near(w.share('a'), 0.5, 'initial share');
w.set('a', 0.2);
near(w.share('a'), 0.2, 'set');
near(w.share('b') / w.share('c'), 5 / 3, 'others keep ratios');
near(sum(w.shares()), 1, 'sums to 1');
// Through the extremes and back.
w.set('a', 1); near(w.share('a'), 1, 'at 100 %'); near(w.share('b'), 0, 'others 0');
w.set('a', 0); near(w.share('a'), 0, 'at 0 %'); near(w.share('b') / w.share('d'), 5 / 2, 'ratios back');
w.set('a', 0.5); near(w.share('a'), 0.5, 'back to 50 %');
near(w.share('b'), 0.5 * 5 / 10, 'b'); near(w.share('c'), 0.5 * 3 / 10, 'c'); near(w.share('d'), 0.5 * 2 / 10, 'd');
// A at 100 %, then raising a hidden B keeps C : D for later.
w.set('a', 1); w.set('b', 0.3);
near(w.share('b'), 0.3, 'b over a'); near(w.share('a'), 0.7, 'a takes the rest'); near(w.share('c'), 0, 'c hidden');
w.set('a', 0); near(w.share('b'), 1, 'b alone at its level'); 
w.set('b', 0); near(w.share('c') / w.share('d'), 3 / 2, 'c : d survive two levels');
// Every other factor at 0: the rest is shared equally.
w = W.create({ a: 1, b: 0, c: 0 });
near(w.share('a'), 1, 'alone'); w.set('a', 0.4);
near(w.share('b'), 0.3, 'equal split'); near(w.share('c'), 0.3, 'equal split');
// Many small moves do not drift.
w = W.create({ a: 7, b: 11, c: 13 });
for (let i = 0; i < 10000; i++) w.set('a', (i % 101) / 100);
w.set('a', 0); near(w.share('b') / w.share('c'), 11 / 13, 'no drift');
// All at 0.
w = W.create({ a: 0, b: 0 }); near(sum(w.shares()), 0, 'all zero');
w.set('b', 0.5); near(w.share('a'), 0.5, 'from all zero');

// Display rounding adds up to exactly 100.
const r = W.roundShares({ a: 1 / 3, b: 1 / 3, c: 1 / 3 }, 1);
near(r.a + r.b + r.c, 100, 'thirds');
const r2 = W.roundShares(W.create({ a: 0, b: 5, c: 4, d: 1, e: 3, f: 3, g: 5, h: 2, i: 3, j: 1 }).shares(), 1);
near(Object.values(r2).reduce((x, y) => x + y, 0), 100, 'ten factors');
console.log('roundShares: all checks pass');
