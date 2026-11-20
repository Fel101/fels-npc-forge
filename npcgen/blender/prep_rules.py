"""The rules the game-model step (creature_prep.py) checks its shape with. Plain Python, no bpy, so the tests run without Blender.

The reconstruction's own axes (Blender, Z up), before the prep turns the body: a side-view picture's body is along Z, its height
is along Y and its depth along X. Measured 2026-10-16 on the Road-hound (length : height 1.40) and the Spitter (2.30), from the
vertices of their raw models. A picture facing left puts its head at -Z; a picture facing right puts it at +Z. The Spitter gives
the left sign (its broad head is the wider end, and the serpent route turns it to -Y); the Reedcoil agrees, but its head is pointed,
so it is weaker evidence. The right sign is the mirror of the left.
Its height is along Y, and which way is up is the side its mass sits on (raw_up): the sign was never measured
before 2026-10-17, and the static prep turned both the Sand-crab and the Road-hound upside down (legs up).
"""
DRIFT_LIMIT = 0.04                       # the most any axis may change while the mesh is reduced to its triangle budget

FACING_HEAD = {"left": (0.0, 0.0, -1.0), "right": (0.0, 0.0, 1.0)}   # where the head is, in the raw model's Blender axes
HEAD_TARGET = (0.0, -1.0, 0.0)           # the game's forward: the head at -Y (Blender), which glTF turns into +Z (Godot forward)
UP_TARGET = (0.0, 0.0, 1.0)              # the game's up: Blender's Z


def drift(reference, after):
    """How far each axis moved, as a fraction of its size before (both are (x, y, z) sizes). Raises ValueError for a flat body."""
    out = {}
    for name, before, now in zip("xyz", reference, after):
        if before <= 0:
            raise ValueError(f"The {name} size before reducing is {before}: the model is flat")
        out[name] = round(abs(now - before) / before, 4)
    return out


def worst_drift(per_axis):
    return max(per_axis.values())


def elongation(length, across_a, across_b):
    """The body's length over the larger of its two other sizes. A serpent is long; a coil is about as long as it is wide."""
    across = max(across_a, across_b)
    if across <= 0:
        raise ValueError("The body has no width or height")
    return round(length / across, 3)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def raw_up(mass_offset_y):
    """The raw model's up, from where its mass sits along its height (Blender's Y), measured from the middle of that height: a body is
    heavier on its back than under its legs, so the side its mass is on is up. Measured 2026-10-17 on the Sand-crab (its mass 0.046 m
    on -Y) and the Road-hound (0.034 m on -Y): both backs are on -Y."""
    return (0.0, 1.0 if mass_offset_y > 0 else -1.0, 0.0)


def static_rotation(facing, up):
    """The rotation (rows of a 3 x 3 matrix) that turns a static body so its head is at -Y and its back is up (+Z). `up` is the raw
    model's up (raw_up): the head's raw direction goes to HEAD_TARGET, `up` goes to UP_TARGET, and the third axis follows."""
    if facing not in FACING_HEAD:
        raise ValueError(f"The facing must be one of: {', '.join(FACING_HEAD)}")
    head = FACING_HEAD[facing]
    src = [head, up, _cross(head, up)]
    dst = [HEAD_TARGET, UP_TARGET, _cross(HEAD_TARGET, UP_TARGET)]
    # src and dst are orthonormal, so the rotation is the sum of dst_k outer src_k
    rows = [[0.0] * 3 for _ in range(3)]
    for d, s in zip(dst, src):
        for i in range(3):
            for j in range(3):
                rows[i][j] += d[i] * s[j]
    return [[round(v, 9) + 0.0 for v in row] for row in rows]


def apply(rows, v):
    """The matrix (rows) times the vector v."""
    return tuple(sum(rows[i][j] * v[j] for j in range(3)) for i in range(3))


def refusal(budget, count, drift_now, limit=DRIFT_LIMIT):
    """The reason a reduction is refused, or None when it keeps the budget and the shape. Over the budget is said as such; a body
    that moved too far names its worst axis, so the message never blames a shape that is within the limit."""
    if count > budget:
        return (f"could not reduce the model to {budget} triangles (it has {count}). "
                "Check the picture and the triangle budget.")
    worst = max(drift_now, key=drift_now.get)
    if drift_now[worst] > limit:
        return (f"could not reduce the model to {budget} triangles and keep its shape: the {worst} axis changed by "
                f"{drift_now[worst] * 100:.0f}% (the limit is {limit * 100:.0f}%). Check the picture and the triangle budget.")
    return None


def determinant(rows):
    a, b, c = rows
    return (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0]))
