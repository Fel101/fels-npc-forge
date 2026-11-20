// Godot 4.7's SpringBoneSimulator3D for the soft-body chains of a designed body (npcgen/softbody.py), in plain JavaScript, so the page can show the jiggle the engine will make.
//
// It is the engine's own arithmetic for a chain of two bones and a free end, the one the game runs for the chest, the butt, the belly and the thighs, followed by the small angle limiter
// of examples/godot/forge_secondary_motion.gd (SoftLimit). It was worked out from the engine's source (scene/3d/spring_bone_simulator_3d.cpp), written first in numpy and checked against
// recorded runs of Godot 4.7.2 to 0.0003 mm; this file is checked against the same recordings (tests/data/soft_godot_trace.json, scripts/check_soft_preview.mjs). What the engine does per
// joint and per step of `dt` seconds, in the space of the bone it takes as the centre of the movement:
//     next = current + (current - previous) * (1 - drag) + R_centre * (rotation_of_joint * (0, stiffness * dt, 0)) + gravity_direction * gravity * dt
// The point is put back at the joint's length from its origin, and the joint is turned by the shortest arc from where it points to where the point now is. The first joint's turned pose is the
// parent of the second. Two things worth knowing, both measured and both in the arithmetic here: (1) `dt` is the step the engine is given, so the movement depends on the frame rate (this file
// takes the step as an argument, and the page lets you choose it); (2) in 4.7 a bone used as the centre is applied as the bone's own pose and not as its inverse (so a centre that turns a lot
// makes a chain flap: the belly and the thighs name the skeleton's `root` bone, which never moves, and `centre: 'world'` here is that case).
//
// Vectors are [x, y, z]; a rotation is nine numbers, row by row; a quaternion is [x, y, z, w]; a pose is {R, t}. Nothing here needs a browser.

export const SOFT_KINDS = ['chest', 'butt', 'belly', 'thighs'];
const FORWARD_Y = 1;                                       // (the chain's bones point along +Y)
const IDENTITY = { R: [1, 0, 0, 0, 1, 0, 0, 0, 1], t: [0, 0, 0] };

// ---- small maths --------------------------------------------------------------------------------------------------------------------------
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const norm = a => Math.sqrt(dot(a, a));
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

export const mat3MulVec = (m, v) => [m[0] * v[0] + m[1] * v[1] + m[2] * v[2], m[3] * v[0] + m[4] * v[1] + m[5] * v[2], m[6] * v[0] + m[7] * v[1] + m[8] * v[2]];
export const mat3Transpose = m => [m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8]];
export function mat3Mul(a, b) {
  const out = new Array(9);
  for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) out[r * 3 + c] = a[r * 3] * b[c] + a[r * 3 + 1] * b[3 + c] + a[r * 3 + 2] * b[6 + c];
  return out;
}
export function mat3Inverse(m) {
  const [a, b, c, d, e, f, g, h, i] = m;
  const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
  const det = a * A + b * B + c * C;
  return [A / det, -(b * i - c * h) / det, (b * f - c * e) / det, B / det, (a * i - c * g) / det, -(a * f - c * d) / det, C / det, -(a * h - b * g) / det, (a * e - b * d) / det];
}

/** The rotation matrix of a quaternion [x, y, z, w]. */
export function quatToMat3(q) {
  const [x, y, z, w] = q;
  return [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
    2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
    2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)];
}

/** The unit quaternion [x, y, z, w] of a rotation matrix. */
export function mat3ToQuat(R) {
  const t = R[0] + R[4] + R[8];
  let q;
  if (t > 0) {
    const s = Math.sqrt(t + 1) * 2;
    q = [(R[7] - R[5]) / s, (R[2] - R[6]) / s, (R[3] - R[1]) / s, 0.25 * s];
  } else if (R[0] > R[4] && R[0] > R[8]) {
    const s = Math.sqrt(1 + R[0] - R[4] - R[8]) * 2;
    q = [0.25 * s, (R[1] + R[3]) / s, (R[2] + R[6]) / s, (R[7] - R[5]) / s];
  } else if (R[4] > R[8]) {
    const s = Math.sqrt(1 + R[4] - R[0] - R[8]) * 2;
    q = [(R[1] + R[3]) / s, 0.25 * s, (R[5] + R[7]) / s, (R[2] - R[6]) / s];
  } else {
    const s = Math.sqrt(1 + R[8] - R[0] - R[4]) * 2;
    q = [(R[2] + R[6]) / s, (R[5] + R[7]) / s, 0.25 * s, (R[3] - R[1]) / s];
  }
  const n = Math.hypot(q[0], q[1], q[2], q[3]);
  return [q[0] / n, q[1] / n, q[2] / n, q[3] / n];
}

/** Spherical interpolation by the short way; `t` above 1 goes on past `b`. */
export function slerp(a, b, t) {
  let d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
  let other = b;
  if (d < 0) { other = [-b[0], -b[1], -b[2], -b[3]]; d = -d; }
  if (d > 0.9995) {
    const q = [a[0] + t * (other[0] - a[0]), a[1] + t * (other[1] - a[1]), a[2] + t * (other[2] - a[2]), a[3] + t * (other[3] - a[3])];
    const n = Math.hypot(q[0], q[1], q[2], q[3]);
    return [q[0] / n, q[1] / n, q[2] / n, q[3] / n];
  }
  const theta = Math.acos(d), s = Math.sin(theta);
  const k = Math.sin((1 - t) * theta) / s, m = Math.sin(t * theta) / s;
  return [k * a[0] + m * other[0], k * a[1] + m * other[1], k * a[2] + m * other[2], k * a[3] + m * other[3]];
}

/** The rotation that takes the unit vector a to the unit vector b by the short way (the engine's get_from_to_rotation). */
export function shortestArc(a, b) {
  const c = dot(a, b), v = cross(a, b);
  if (c < -0.999999 || (norm(v) < 1e-12 && c < 0)) return IDENTITY.R.slice();
  const k = 1 / (1 + c);
  const vx = [0, -v[2], v[1], v[2], 0, -v[0], -v[1], v[0], 0];
  const vv = mat3Mul(vx, vx);
  return [1 + vx[0] + vv[0] * k, vx[1] + vv[1] * k, vx[2] + vv[2] * k, vx[3] + vv[3] * k, 1 + vx[4] + vv[4] * k, vx[5] + vv[5] * k, vx[6] + vv[6] * k, vx[7] + vv[7] * k, 1 + vx[8] + vv[8] * k];
}

const compose = (p, c) => ({ R: mat3Mul(p.R, c.R), t: add(mat3MulVec(p.R, c.t), p.t) });
const apply = (pose, point) => add(mat3MulVec(pose.R, point), pose.t);

/** The factor on every spring's stiffness for a softness from 0 (firm: 2.8 times as stiff) through 0.5 (as suggested) to 1 (very soft: 0.35 times); npcgen/softbody.py `firmness`. */
export const firmness = softness => 2 ** (1.5 * (1 - 2 * Math.min(1, Math.max(0, softness))));

/** How many whole steps of 1 / `fps` seconds are due after `elapsed` more seconds (a frame that took longer than a tenth of a second counts as a tenth: a hidden tab does not catch up), at most
 *  `limit` of them (the rest of the time is given up), and the time left over. The simulation is stepped like this whatever the screen's refresh rate. */
export function stepsDue(accumulated, elapsed, fps, limit = 6) {
  let rest = accumulated + Math.min(0.1, Math.max(0, elapsed));
  const step = 1 / fps;
  let steps = 0;
  while (rest >= step - 1e-9 && steps < limit) { rest -= step; steps++; }
  return { steps, accumulated: steps === limit ? 0 : Math.max(0, rest) };
}

/** A bone's turn from where it rests, kept to `scale` times what the simulator made of it (1 leaves it, 0 puts it back at rest): the page's sliders for how much of the skin follows. */
export const scaledTurn = (rest, turned, scale) => scale === 1 ? turned : slerp(rest, turned, scale);

// ---- the chain ------------------------------------------------------------------------------------------------------------------------------
/** One chain of two bones and a free end, as the file describes it.
 *  spec: { L1: {t, q}, L2: {t, q}  (the two bones' own rest transforms, translation and quaternion, in their parents' space),
 *          endLength, gravityDirection, stiffness, drag, gravity (the engine's numbers, metres and seconds),
 *          maxAngleDeg (the limiter's angle, or null), centre ('bone': the bone the chain hangs on, 'world': a bone that never moves) } */
export class SoftChain {
  constructor(spec) {
    this.L1 = { R: quatToMat3(spec.L1.q), t: spec.L1.t.slice() };
    this.L2 = { R: quatToMat3(spec.L2.q), t: spec.L2.t.slice() };
    this.rest1 = normalise(spec.L1.q);
    this.rest2 = normalise(spec.L2.q);
    this.endLength = spec.endLength;
    this.gravityDirection = spec.gravityDirection.slice();
    this.stiffness = spec.stiffness;
    this.drag = spec.drag;
    this.gravity = spec.gravity;
    this.limit = spec.maxAngleDeg == null ? null : spec.maxAngleDeg * Math.PI / 180;
    this.centre = spec.centre === 'world' ? 'world' : 'bone';
    this.state = null;
  }

  /** Forget the movement: the next step starts from the pose it is given (as the engine does after a reset). */
  reset() { this.state = null; }

  /** One step of `dt` seconds with the parent bone at `parent` ({R, t}: its animated pose in the skeleton's space). Returns {q1, q2, clamped}: the two bones' local rotations after the
   *  limiter (quaternions [x, y, z, w], to put on the bones), and whether the limiter held either of them back. */
  step(parent, dt) {
    const centre = this.centre === 'bone' ? parent : IDENTITY;
    const Rc = centre.R, Rci = mat3Inverse(Rc);
    const W1 = compose(parent, this.L1), W2 = compose(W1, this.L2);
    const tailRest = W2.t;
    if (!this.state) {
      const c1 = apply(centre, tailRest), c2 = apply(centre, add(mat3MulVec(W2.R, [0, this.endLength, 0]), W2.t));
      this.state = { cur: [c1, c2], prev: [c1.slice(), c2.slice()] };
    }
    const state = this.state;
    const turned = [];
    let Wj = W1, previous = null;
    for (let j = 0; j < 2; j++) {
      if (j === 1) Wj = compose(previous, this.L2);
      const curRot = Wj.R;
      const origin = apply(centre, Wj.t);
      const length = j === 0 ? norm(sub(tailRest, W1.t)) : this.endLength;
      const cur = state.cur[j], prev = state.prev[j];
      const push = mat3MulVec(Rc, mat3MulVec(curRot, [0, FORWARD_Y * this.stiffness * dt, 0]));
      const free = [0, 0, 0].map((_, k) => cur[k] + (cur[k] - prev[k]) * (1 - this.drag) + push[k] + this.gravityDirection[k] * (this.gravity * dt));
      const away = sub(free, origin), far = norm(away);
      const next = far > 1e-12 ? add(origin, away.map(v => v / far * length)) : add(origin, mat3MulVec(Rc, mat3MulVec(curRot, [0, length, 0])));
      state.prev[j] = cur;
      state.cur[j] = next;
      const from = mat3MulVec(curRot, [0, 1, 0]), to = mat3MulVec(Rci, sub(next, origin));
      const nf = norm(from), nt = norm(to);
      const delta = shortestArc(from.map(v => v / nf), to.map(v => v / nt));
      previous = { R: mat3Mul(delta, curRot), t: Wj.t };
      turned.push(previous);
    }
    const [A1, A2] = turned;
    const one = this.limited(this.rest1, mat3Mul(mat3Transpose(parent.R), A1.R));
    const two = this.limited(this.rest2, mat3Mul(mat3Transpose(A1.R), A2.R));
    const out = { q1: one.q, q2: two.q, clamped: one.clamped || two.clamped };
    if (![...out.q1, ...out.q2].every(Number.isFinite)) {                // (a safety net for a page that is being dragged about: start again rather than show a broken body)
      this.reset();
      return { q1: this.rest1.slice(), q2: this.rest2.slice(), clamped: false };
    }
    return out;
  }

  /** The helper's angle limiter: a bone turned more than the limit from its rest rotation is pulled back to the limit. */
  limited(rest, poseR) {
    const q = mat3ToQuat(poseR);
    if (this.limit == null) return { q, clamped: false };
    const angle = 2 * Math.acos(Math.min(1, Math.abs(rest[0] * q[0] + rest[1] * q[1] + rest[2] * q[2] + rest[3] * q[3])));
    return angle > this.limit ? { q: slerp(rest, q, this.limit / angle), clamped: true } : { q, clamped: false };
  }

  /** The chain's two bones in the skeleton's space for the local rotations `q1`, `q2` under `parent` (for checking against the engine). */
  globals(parent, q1, q2) {
    const G1 = { R: mat3Mul(parent.R, quatToMat3(q1)), t: add(mat3MulVec(parent.R, this.L1.t), parent.t) };
    const G2 = { R: mat3Mul(G1.R, quatToMat3(q2)), t: add(mat3MulVec(G1.R, this.L2.t), G1.t) };
    return { G1, G2 };
  }
}

function normalise(q) {
  const n = Math.hypot(q[0], q[1], q[2], q[3]);
  return [q[0] / n, q[1] / n, q[2] / n, q[3] / n];
}

/** A chain's spec from the `forge_secondary` block on the first bone of the chain in the file, and the two bones' rest transforms ({t, q} each). Null for a block that is not a soft one. */
export function chainSpec(block, rest1, rest2) {
  if (!block || !SOFT_KINDS.includes(block.kind) || !Array.isArray(block.bones) || block.bones.length !== 2) return null;
  return { L1: rest1, L2: rest2, endLength: block.end_length_m, gravityDirection: block.gravity_direction, stiffness: block.stiffness, drag: block.drag, gravity: block.gravity,
    maxAngleDeg: block.max_angle_deg ?? null, centre: block.center_bone === 'root' ? 'world' : 'bone' };
}
