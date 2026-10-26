// Engine checks. Run with: node tests/engine.test.js
'use strict';
const assert = require('assert');
const path = require('path');
const E = require(path.join(__dirname, '..', 'js', 'engine.js'));

let passed = 0;
function test(name, fn) {
  try { fn(); passed++; console.log('ok   ' + name); }
  catch (e) { console.error('FAIL ' + name + '\n     ' + e.message); process.exitCode = 1; }
}
const base = () => E.defaultScenario();

test('sample case runs and returns a recommendation', () => {
  const r = E.run(base());
  assert.ok(r.ok);
  assert.ok(r.recommendation && r.recommendation.bid > 0);
});

test('same seed gives identical results', () => {
  const a = E.run(base()), b = E.run(base());
  assert.strictEqual(a.recommendation.bid, b.recommendation.bid);
  assert.strictEqual(a.recommendation.pWin, b.recommendation.pWin);
});

test('recommendation never exceeds the walk-away maximum', () => {
  for (const w of [950, 1020, 1100, 1200]) {
    const s = base(); s.limits.walkAway = w; s.weights.emotional = 150; s.acquirer.emotionalValue = 500;
    const r = E.run(s);
    if (r.recommendation) assert.ok(r.recommendation.bid <= w + 1e-9, 'walk-away ' + w + ' breached');
  }
});

test('recommendation never exceeds committed financing', () => {
  const s = base(); s.limits.financing = 980; s.limits.walkAway = 1500;
  const r = E.run(s);
  if (r.recommendation) assert.ok(r.recommendation.bid <= 980 + 1e-9);
});

test('preferences cannot override hard limits', () => {
  const s = base(); s.weights.strategic = 150; s.acquirer.strategicValue = 2000; s.limits.walkAway = 1050;
  const r = E.run(s);
  assert.ok(r.recommendation.bid <= 1050 + 1e-9);
  assert.ok(r.warnings.some(w => w.title === 'Hard limits changed the decision'));
});

test('highest bid is not automatically recommended', () => {
  const r = E.run(base());
  const maxFeasible = Math.max(...r.curve.filter(c => c.feasible).map(c => c.bid));
  assert.ok(r.recommendation.bid < maxFeasible);
});

test('higher emotional value weakly raises the recommended bid', () => {
  const s1 = base(), s2 = base();
  s2.acquirer.emotionalValue = 150; s2.weights.emotional = 100;
  assert.ok(E.run(s2).recommendation.bid >= E.run(s1).recommendation.bid);
});

test('higher risk aversion weakly lowers the recommended bid', () => {
  const s1 = base(), s2 = base(); s1.weights.risk = 0; s2.weights.risk = 150;
  assert.ok(E.run(s2).recommendation.bid <= E.run(s1).recommendation.bid);
});

test('win probability rises with the bid', () => {
  const c = E.run(base()).curve;
  for (let i = 1; i < c.length; i++) assert.ok(c[i].pWin >= c[i - 1].pWin - 1e-12);
});

test('walk-away is recommended when nothing creates value', () => {
  const s = base(); s.acquirer.synergies = 0; s.acquirer.strategicValue = 0; s.acquirer.emotionalValue = 0;
  s.limits.walkAway = 900; s.competitors.forEach(c => { if (c.type === 'strategic') c.synergyPct = 20; });
  const r = E.run(s);
  assert.ok(r.walkAwayRecommended || r.recommendation.expFv >= 0);
});

test('observed bid outside the modelled band raises a warning', () => {
  const s = base(); s.competitors[0].observedBid = 1400;
  const r = E.run(s);
  assert.ok(r.warnings.some(w => /observed bid outside/.test(w.title)));
  assert.strictEqual(r.reliability, 'Low');
});

test('out-of-range rival parameters are flagged', () => {
  const s = base(); s.competitors[2].targetIrr = 8;
  const r = E.run(s);
  assert.ok(r.competitors[2].ood.length > 0);
});

test('invalid input is rejected with a message', () => {
  const s = base(); s.sim.runs = 10;
  const r = E.run(s);
  assert.strictEqual(r.ok, false);
  assert.ok(r.errors.length > 0);
});

test('ascending auction runs', () => {
  const s = base(); s.deal.format = 'english';
  assert.ok(E.run(s).ok);
});

test('winner\'s curse: value given a win is below unconditional value at the same price', () => {
  const s = base(); s.acquirer.synergies = 0; s.acquirer.proposedBid = 1000;
  const r = E.run(s);
  // Paying exactly the standalone value with no synergies: unconditional expectation is ~0,
  // but conditional on winning it is negative because we win when rivals' signals are low.
  assert.ok(r.proposed.expFvWin < 0);
});

test('maximum defensible bid is the tightest active hard limit', () => {
  const s = base(); s.limits.walkAway = 1020;
  const t = E.run(s).thresholds;
  assert.strictEqual(t.binding, 'walk');
  assert.ok(Math.abs(t.ceiling - 1020) < 1e-9);
  const active = t.rows.filter(r => r.active).map(r => r.max);
  assert.ok(Math.abs(t.ceiling - Math.min(...active)) < 1e-9);
});

test('recommended bid never exceeds the maximum defensible bid', () => {
  for (const w of [980, 1050, 1200]) {
    const s = base(); s.limits.walkAway = w; const r = E.run(s);
    if (r.recommendation) assert.ok(r.recommendation.bid <= r.thresholds.ceiling + 1.0);
  }
});

test('winner\'s curse lowers the value floor below simple break-even (sealed bid)', () => {
  const t = E.run(base()).thresholds;
  assert.ok(t.floorMax < t.naiveBreakEven);
});

test('adaptive rivals settle near an equilibrium with no profitable deviation', () => {
  const s = base(); s.sim.adaptive = true;
  const A = E.run(s).adaptation;
  assert.ok(A.status !== 'not', 'status ' + A.status);
  assert.ok(A.maxRivalGain <= 0.5);
});

test('fixed-rule results are unchanged when adaptation is off', () => {
  const r = E.run(base());
  assert.strictEqual(Math.round(r.recommendation.bid), 1000);
  assert.ok(Math.abs(r.recommendation.pWin - 0.29) < 0.005);
});

test('adaptive mode still respects hard limits', () => {
  const s = base(); s.sim.adaptive = true; s.limits.walkAway = 960;
  const r = E.run(s);
  if (r.recommendation) assert.ok(r.recommendation.bid <= 960 + 1e-9);
});

console.log('\n' + passed + ' checks passed');
