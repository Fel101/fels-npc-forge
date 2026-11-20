// web/soft-solver.js against recorded runs of Godot 4.7.2 (tests/data/soft_godot_trace.json), and the small maths around it.
// Run by tests/test_soft_preview.py (node --test scripts/check_soft_preview.mjs).
//
// The recordings are the engine's own: for each of five chains of a designed female body (chest, butt, belly, both thighs) the pose of the bone the chain hangs on in every frame (the
// solver's input) and the poses of the chain's two bones after the engine's SpringBoneSimulator3D and the helper's angle limiter (what it made of them). Four runs: a jog at 60 steps a
// second, a heavy sword combo (where the limiter acts), a jog at 30 steps a second and a dance.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { SoftChain, chainSpec, firmness, mat3Inverse, mat3Mul, mat3ToQuat, quatToMat3, scaledTurn, shortestArc, slerp, stepsDue } from '../web/soft-solver.js';

const trace = JSON.parse(readFileSync(new URL('../tests/data/soft_godot_trace.json', import.meta.url), 'utf8'));
const poseOf = ([qx, qy, qz, qw, x, y, z]) => ({ R: quatToMat3([qx, qy, qz, qw]), t: [x, y, z] });
const distance = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
/** The angle (degrees) between two rotation matrices. */
const turnBetween = (A, B) => {
  const q = mat3ToQuat(mat3Mul(A, tr(B)));
  return 2 * Math.acos(Math.min(1, Math.abs(q[3]))) * 180 / Math.PI;
};
const tr = m => [m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8]];
const spec = (name, overrides = {}) => {
  const c = trace.chains[name];
  return { ...chainSpec(c.block, c.rest.L1, c.rest.L2), ...overrides };
};

/** Runs a recording through the solver; the worst distance (mm) of the two bones' origins and the worst turn (degrees) of their rotations from the engine's, and how often the limiter held. */
function replay(runName, chainName, overrides = {}) {
  const run = trace.runs[runName], data = run.chains[chainName];
  const chain = new SoftChain(spec(chainName, overrides));
  const dt = 1 / run.fps;
  let origin = 0, turn = 0, held = 0, moved = 0;
  for (let f = 0; f < run.frames; f++) {
    const parent = poseOf(data.parent[f]);
    const { q1, q2, clamped } = chain.step(parent, dt);
    const { G1, G2 } = chain.globals(parent, q1, q2);
    const g1 = poseOf(data.bone1[f]), g2 = poseOf(data.bone2[f]);
    origin = Math.max(origin, distance(G1.t, g1.t), distance(G2.t, g2.t));
    turn = Math.max(turn, turnBetween(G1.R, g1.R), turnBetween(G2.R, g2.R));
    held += clamped ? 1 : 0;
    moved = Math.max(moved, distance(G2.t, mat3Apply(parent, chain.L1, chain.L2)));
  }
  return { origin: origin * 1000, turn, held, moved: moved * 1000 };
}
/** Where the second bone's origin is while the chain rests, for a parent pose. */
function mat3Apply(parent, L1, L2) {
  const world = (p, c) => ({ R: mat3Mul(p.R, c.R), t: [0, 1, 2].map(k => p.R[k * 3] * c.t[0] + p.R[k * 3 + 1] * c.t[1] + p.R[k * 3 + 2] * c.t[2] + p.t[k]) });
  return world(world(parent, L1), L2).t;
}

for (const run of Object.keys(trace.runs)) {
  for (const chain of Object.keys(trace.chains)) {
    test(`${trace.runs[run].clip} at ${trace.runs[run].fps} steps a second, ${chain}: the chain follows the engine to a hundredth of a millimetre`, () => {
      const result = replay(run, chain);
      assert.ok(result.origin < 0.01, `the bones' origins are ${result.origin.toFixed(4)} mm from the engine's`);
      assert.ok(result.turn < 0.01, `the bones' rotations are ${result.turn.toFixed(5)} degrees from the engine's`);
    });
  }
}

test('the recordings do move the chains (a still chain would pass any solver) and the sword combo reaches the angle limiter', () => {
  let farthest = 0, held = 0;
  for (const chain of Object.keys(trace.chains)) {
    const jog = replay('jog', chain), sword = replay('sword', chain);
    farthest = Math.max(farthest, jog.moved, sword.moved);
    held += sword.held;
  }
  assert.ok(farthest > 5, `the farthest a chain got from its rest in the recordings is ${farthest.toFixed(1)} mm`);
  assert.ok(held > 0, 'the limiter held a bone back in the sword combo');
});

test('a wrong centre, a wrong step or no limiter is seen by the recordings', () => {
  const wrongCentre = replay('jog', 'belly_c', { centre: 'bone' });
  assert.ok(wrongCentre.origin > 1, `a belly chain with its own bone as the centre is only ${wrongCentre.origin.toFixed(2)} mm from the engine's`);
  const rightCentre = replay('jog', 'chest_l');
  const worldChest = replay('jog', 'chest_l', { centre: 'world' });
  assert.ok(worldChest.origin > rightCentre.origin + 1, 'the chest was tuned with its own bone as the centre, and the recordings tell the two apart');
  const noLimit = replay('sword', 'chest_l', { maxAngleDeg: null });
  const thighs = ['thighs_l', 'thighs_r'].map(name => replay('sword', name, { maxAngleDeg: null }).origin);
  assert.ok(noLimit.origin > 0.01 || Math.max(...thighs) > 0.01, 'without the limiter a sword combo does not match the engine, which has it');
  const wrongRun = new SoftChain(spec('chest_l'));
  const run = trace.runs.jog.chains.chest_l;
  let worst = 0;
  for (let f = 0; f < 60; f++) {
    const parent = poseOf(run.parent[f]);
    const { q1, q2 } = wrongRun.step(parent, 1 / 30);
    worst = Math.max(worst, distance(wrongRun.globals(parent, q1, q2).G2.t, poseOf(run.bone2[f]).t) * 1000);
  }
  assert.ok(worst > 0.05, 'the same jog stepped at 30 a second is not the jog the engine made at 60');
});

test('after a reset the chain starts again from the pose it is given, and the same input gives the same answer', () => {
  const run = trace.runs.dance, data = run.chains.thighs_l;
  const chain = new SoftChain(spec('thighs_l'));
  const play = () => {
    const out = [];
    for (let f = 0; f < 40; f++) out.push(chain.step(poseOf(data.parent[f]), 1 / run.fps).q2);
    return out;
  };
  const first = play();
  chain.reset();
  assert.deepEqual(play(), first);
  assert.notDeepEqual(play(), first, 'carried on, without a reset, from where it was, the chain does not repeat itself');
});

test('a chain that is handed rubbish rests instead of showing a broken body', () => {
  const chain = new SoftChain(spec('belly_c'));
  const parent = poseOf(trace.runs.jog.chains.belly_c.parent[0]);
  chain.step(parent, 1 / 60);
  const broken = chain.step({ R: [0, 0, 0, 0, 0, 0, 0, 0, 0], t: [0, 0, 0] }, 1 / 60);
  assert.deepEqual(broken.q1, chain.rest1);
  assert.deepEqual(broken.q2, chain.rest2);
  assert.equal(chain.state, null);
  const again = chain.step(parent, 1 / 60);
  assert.ok([...again.q1, ...again.q2].every(Number.isFinite));
});

test('chainSpec reads the file\'s block: the soft kinds, the root as a still centre, nothing for hair or cloth', () => {
  const belly = trace.chains.belly_c, chest = trace.chains.chest_l;
  assert.equal(chainSpec(belly.block, belly.rest.L1, belly.rest.L2).centre, 'world');
  assert.equal(chainSpec(chest.block, chest.rest.L1, chest.rest.L2).centre, 'bone');
  assert.equal(chainSpec(chest.block, chest.rest.L1, chest.rest.L2).maxAngleDeg, 32);
  assert.equal(chainSpec({ ...chest.block, kind: 'hair' }, chest.rest.L1, chest.rest.L2), null);
  assert.equal(chainSpec({ ...chest.block, bones: ['a', 'b', 'c'] }, chest.rest.L1, chest.rest.L2), null);
  assert.equal(chainSpec(null, chest.rest.L1, chest.rest.L2), null);
  const noLimit = { ...chest.block };
  delete noLimit.max_angle_deg;
  assert.equal(chainSpec(noLimit, chest.rest.L1, chest.rest.L2).maxAngleDeg, null);
});

test('softness makes the springs firmer or softer exactly as the file builder says', () => {
  assert.equal(firmness(0.5), 1);
  assert.ok(Math.abs(firmness(0) - 2 ** 1.5) < 1e-12);
  assert.ok(Math.abs(firmness(1) - 2 ** -1.5) < 1e-12);
  assert.equal(firmness(-3), firmness(0));
  assert.equal(firmness(7), firmness(1));
});

test('a turn kept to a share of itself: none puts the bone at rest, all leaves it, half is half the angle', () => {
  const rest = [0, 0, 0, 1];
  const half = Math.sin(0.25), turned = [half, 0, 0, Math.cos(0.25)];                 // 0.5 radians about x
  assert.equal(scaledTurn(rest, turned, 1), turned);
  assert.ok(Math.abs(2 * Math.acos(scaledTurn(rest, turned, 0.5)[3]) - 0.25) < 1e-12);
  assert.ok(Math.abs(scaledTurn(rest, turned, 0)[3] - 1) < 1e-12);
  assert.ok(Math.abs(2 * Math.acos(scaledTurn(rest, turned, 2)[3]) - 1.0) < 1e-12, 'twice goes on past it');
  const flipped = turned.map(v => -v);                                                // (the same turn written with the other sign)
  assert.ok(Math.abs(2 * Math.acos(Math.abs(scaledTurn(rest, flipped, 0.5)[3])) - 0.25) < 1e-12);
});

test('rotations, quaternions and arcs agree with each other', () => {
  for (const q of [[0, 0, 0, 1], [1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0.5, 0.5, 0.5, 0.5], [0.1, -0.7, 0.3, 0.6]]) {
    const n = Math.hypot(...q), unit = q.map(v => v / n);
    const back = mat3ToQuat(quatToMat3(unit));
    const same = Math.abs(back[0] * unit[0] + back[1] * unit[1] + back[2] * unit[2] + back[3] * unit[3]);
    assert.ok(Math.abs(same - 1) < 1e-12, `quaternion ${unit} came back as ${back}`);
    const m = quatToMat3(unit), inv = mat3Inverse(m), eye = mat3Mul(m, inv);
    assert.ok(eye.every((v, i) => Math.abs(v - (i % 4 === 0 ? 1 : 0)) < 1e-12));
  }
  const a = [0, 1, 0], b = [Math.sin(0.4), Math.cos(0.4), 0];
  const R = shortestArc(a, b);
  const turned = [R[0] * a[0] + R[1] * a[1] + R[2] * a[2], R[3] * a[0] + R[4] * a[1] + R[5] * a[2], R[6] * a[0] + R[7] * a[1] + R[8] * a[2]];
  assert.ok(turned.every((v, i) => Math.abs(v - b[i]) < 1e-12));
  assert.deepEqual(shortestArc([0, 1, 0], [0, -1, 0]), [1, 0, 0, 0, 1, 0, 0, 0, 1], 'straight back has no short way: it is left alone, as the engine does');
  assert.deepEqual(slerp([0, 0, 0, 1], [0, 0, 0, 1], 0.3), [0, 0, 0, 1]);
});

test('the simulation is stepped in whole steps of the frame time of the game whatever the refresh rate of the screen', () => {
  const run = (fps, frameSeconds, frames) => {
    let accumulated = 0, steps = 0;
    for (let i = 0; i < frames; i++) { const due = stepsDue(accumulated, frameSeconds, fps); accumulated = due.accumulated; steps += due.steps; }
    return steps;
  };
  assert.equal(run(60, 1 / 60, 600), 600, 'a 60 a second screen and a 60 a second game: one step a frame');
  assert.equal(run(60, 1 / 144, 1440), 600, 'a 144 a second screen steps the same ten seconds of game');
  assert.equal(run(60, 1 / 30, 300), 600, 'a slow screen takes two steps a frame');
  assert.equal(run(120, 1 / 60, 600), 1200);
  assert.equal(run(30, 1 / 60, 600), 300);
  assert.ok(Math.abs(run(60, 0.0123, 1000) - 738) <= 1, 'an odd frame time keeps the average: 12.3 seconds are 738 steps at 60 a second');
});

test('a frame that took too long is not caught up with, and a backwards clock gives no steps', () => {
  const late = stepsDue(0, 5, 60);
  assert.ok(late.steps <= 6, `${late.steps} steps for five seconds`);
  assert.equal(late.accumulated, 0, 'the rest of the time is given up when the cap is reached');
  assert.equal(stepsDue(0.001, -3, 60).steps, 0);
  assert.equal(stepsDue(0.01, -3, 60).accumulated, 0.01, 'a clock that went backwards does not take what was already owed');
  assert.equal(stepsDue(0, 5, 30).steps, 3, 'a frame that took five seconds counts as a tenth: three steps at 30 a second');
  const some = stepsDue(0.005, 0.0125, 60);
  assert.equal(some.steps, 1);
  assert.ok(Math.abs(some.accumulated - (0.005 + 0.0125 - 1 / 60)) < 1e-12);
  assert.equal(stepsDue(0, 0.1, 60).steps, 6, 'a tenth of a second at 60 a second is six steps: the cap is not a loss');
  const fast = stepsDue(0, 0.1, 120);
  assert.equal(fast.steps, 6, 'a tenth of a second at 120 a second is twelve steps: six are taken');
  assert.equal(fast.accumulated, 0, 'and the other six are given up, not carried');
});
