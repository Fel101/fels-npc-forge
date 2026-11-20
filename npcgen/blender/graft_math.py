"""The numpy-only parts of attaching a detailed head to a whole-body model (npcgen/blender/head_graft.py, npcgen/headgraft.py).

They live apart from the Blender script, like hand_shape.py does for the hands, so they run (and are tested) outside Blender.

The idea (docs/detailed-head.md, "Attaching the head to the body"): the whole-body model's face is a mask, the detailed head's is
a face. The head's FACE (a patch of it, from the brows to the chin, cheek to cheek) is fitted on the body's face and laid over it:
the patch's rim lies on the body's surface and eases to its own shape, and the body's face behind it is laid flat on a smooth
"backing" just behind the patch, so nothing of the mask stands in front of the patch and every hole in the patch shows the body's
own skin, not the inside of the head.

Everything here works in the model's frame after Blender's import: x to the character's left, y away from the front camera (the
character faces -y), z up.
"""
import heapq
import math

import numpy as np

# The face's outline seen from the front, in units of u (the nose tip's distance to the chin's bottom): height above the nose tip, then
# the half-width there. Chin, jaw, mouth, cheeks, brows, forehead, and a round cap that reaches about the usual hairline (where the
# head's hair really starts is found from its paint: polar_cut).
FACE = np.array([[-1.15, 0.26], [-1.0, 0.40], [-0.75, 0.62], [-0.4, 0.82], [0.0, 0.95], [0.5, 0.95], [1.0, 0.88], [1.25, 0.80],
                 [1.45, 0.64], [1.6, 0.38], [1.72, 0.0]])
TOP = 1.72                 # the patch does not go higher than this above the nose tip (u)
TOP_UNTRIMMED = 1.0        # ...or this, when hair and skin could not be told apart in the head model's paint
OUTER_TOP = 0.75           # the "outer ring" of the outline, where hair is looked for: above this (u), or out beyond OUTER_SIDE
OUTER_SIDE = 0.66

# landmark numbers of SDPose's 68 face points (jaw 0-16, brows 17-26, nose 27-35, eyes 36-47, mouth 48-67)
FIT_POINTS = tuple(range(27, 60))                       # nose, eyes, mouth: where a face keeps its shape and a mask is still right
DEPTH_POINTS = (30, 31, 32, 33, 34, 35, 48, 51, 54, 57)  # nose and mouth: where the skin is (glasses stand in front of the eyes)
NOSE_TIP, CHIN = 30, 8
MIN_SCORE = 0.4
MIN_POINTS = 12


def half_width(dz):
    """The face's half-width (u) at heights dz (u) above the nose tip."""
    return np.interp(dz, FACE[:, 0], FACE[:, 1])


def _round_window(values, fn, window):
    """`fn` (np.min, np.mean) over the circular window of +-window entries round every entry of a 1D array."""
    return fn(np.stack([np.roll(values, k) for k in range(-window, window + 1)]), axis=0)


def polar_cut(dx, dz, candidate, hair, centre=(0.0, 0.45), bins=144, window=4, slope=0.02, margin=0.03, min_slice=3, share=0.5,
              slice_width=0.05):
    """(within, info): the patch's SMOOTH outline. `dx`, `dz` are the faces' centres in the front plane (u, from the nose tip), `candidate`
    the faces inside the generous outline (FACE), `hair` the ones whose paint is hair. Seen from `centre`, every direction gets the
    radius where the hair starts: walking in from the outline in slices of `slice_width` (over this and the two next bins), as long
    as at least `share` of a slice is hair (slices with fewer than `min_slice` faces are skipped), so a strand or a brow that stands
    alone, or a band of skin between it and the hair, does not count. Those radii are eroded over +-`window` bins, and the curve may
    widen from each of them towards the outline by no more than `slope` (u) a bin, so it turns from the hairline into the outline
    gently. The curve is the smaller of that and the outline, smoothed over the same +-`window` (which, with the erosion, can never
    carry it out past the hair) and pulled in by `margin` (u). Returns which candidates are inside the curve, and the curve ("cut": a
    radius per bin) with a few counts."""
    dx, dz = np.asarray(dx, float), np.asarray(dz, float)
    angle = np.arctan2(dz - centre[1], dx - centre[0])
    radius = np.hypot(dx - centre[0], dz - centre[1])
    where = np.minimum(((angle + np.pi) / (2 * np.pi) * bins).astype(int), bins - 1)
    cand = np.nonzero(candidate)[0]
    outline = np.full(bins, -1.0)
    np.maximum.at(outline, where[cand], radius[cand])
    known = outline >= 0
    if not known.any():
        return np.zeros(len(dx), bool), {"cut": np.zeros(bins), "hair_bins": 0}
    middle = (np.arange(bins) + 0.5) / bins * 2 * np.pi - np.pi
    outline = np.interp(middle, middle[known], outline[known], period=2 * np.pi)
    order = np.argsort(where[cand], kind="stable")
    bounds = np.searchsorted(where[cand][order], np.arange(bins + 1))
    members = [cand[order[bounds[k]:bounds[k + 1]]] for k in range(bins)]
    hairline, hair_bins = np.full(bins, np.inf), 0
    for k in range(bins):
        ids = np.concatenate([members[(k + d) % bins] for d in (-1, 0, 1)])
        if len(ids) < 2 * min_slice:
            continue
        outward = radius[ids].max() - radius[ids]
        slot = np.floor(outward / slice_width).astype(int)
        total = np.bincount(slot)
        hairy = np.bincount(slot, weights=hair[ids].astype(float))
        found = None
        for i in range(len(total)):
            if total[i] < min_slice:
                continue
            if hairy[i] >= share * total[i]:
                found = radius[ids].max() - (i + 1) * slice_width
            else:
                break
        if found is not None:
            hairline[k] = found
            hair_bins += 1
    cone = _round_window(hairline, np.min, window)
    for _ in range(2):                                      # a cone of the given slope round every hairline radius, both ways round
        for k in range(1, 2 * bins):
            cone[k % bins] = min(cone[k % bins], cone[(k - 1) % bins] + slope)
        for k in range(2 * bins - 1, -1, -1):
            cone[k % bins] = min(cone[k % bins], cone[(k + 1) % bins] + slope)
    cut = np.minimum(outline, cone)
    for _ in range(2):
        cut = _round_window(cut, np.mean, max(1, window // 2))
    cut = cut - margin
    within = candidate & (radius <= np.interp(angle, middle, cut, period=2 * np.pi))
    return within, {"cut": cut, "hair_bins": hair_bins, "outline": outline}


def similarity_2d(src, dst):
    """(scale, 2x2 rotation, shift): the scale, turn and shift in a plane that take the points src onto dst best (Umeyama)."""
    ms, md = src.mean(0), dst.mean(0)
    xs, xd = src - ms, dst - md
    U, S, Vt = np.linalg.svd(xd.T @ xs / len(src))
    d = np.sign(np.linalg.det(U) * np.linalg.det(Vt))
    R = U @ np.diag([1.0, d]) @ Vt
    s = float((S * np.array([1.0, d])).sum() / ((xs ** 2).sum() / len(src)))
    return s, R, md - s * (R @ ms)


def fit_face(head, body, head_scores, body_scores, use=FIT_POINTS, depth_points=DEPTH_POINTS):
    """The similarity that lays the head model's face on the body's, from SDPose's face points lifted onto each model
    (head, body: 68 entries, each an (x, y, z) or None where the ray missed; the scores are SDPose's).

    Made in the front plane only: glasses, hair and a mask's flat nose make the DEPTH of the eye points useless, so the scale, the
    turn of the face in the picture (about the depth axis) and the shift across come from all the points, and the depth shift from
    the nose and mouth alone, where the skin is. Returns (scale, R 3x3, t 3, info); ValueError when too few points are found."""
    pairs = [i for i in use if head[i] is not None and body[i] is not None and min(head_scores[i], body_scores[i]) > MIN_SCORE]
    if len(pairs) < MIN_POINTS:
        raise ValueError("Too few face points were found on both the body and the head to fit them (%d of at least %d)" % (len(pairs), MIN_POINTS))
    src = np.array([head[i] for i in pairs], float)
    dst = np.array([body[i] for i in pairs], float)
    s, R2, t2 = similarity_2d(src[:, [0, 2]], dst[:, [0, 2]])
    R = np.array([[R2[0, 0], 0.0, R2[0, 1]], [0.0, 1.0, 0.0], [R2[1, 0], 0.0, R2[1, 1]]])     # a turn about y: x and z are the plane
    t = np.array([t2[0], 0.0, t2[1]])
    moved = s * (src @ R.T)
    picked = [k for k, i in enumerate(pairs) if i in depth_points] or list(range(len(pairs)))
    t[1] = float(np.median(dst[picked, 1] - moved[picked, 1]))
    residual = np.linalg.norm((moved + t)[:, [0, 2]] - dst[:, [0, 2]], axis=1)
    info = {"points": len(pairs), "scale": float(s), "turn_deg": float(np.degrees(np.arctan2(R2[1, 0], R2[0, 0]))),
            "residual_mean": float(residual.mean()), "residual_max": float(residual.max())}
    return float(s), R, t, info


def hair_split(lum, rgb, ratio=2.0, separation=2.0):
    """Which of a set of faces are hair, judged by the colour of their paint: (a boolean per face or None, a report).

    Hair is not skin: darker, and often more saturated (red, brown). Two tries: Otsu's split of the log luminance, then two clusters
    (k-means) in (log luminance, saturation). Each must be a real second population: well apart, each with a real share of the faces,
    the darker one hair. The first that is takes the dark class; None when neither is (a bald head, or hair the colour of skin)."""
    lum = np.asarray(lum, float)
    rgb = np.asarray(rgb, float)
    report = {}
    if len(lum) < 50:
        return None, {"reason": "too few faces"}
    x = np.log(lum + 0.02)
    hist, edges = np.histogram(x, bins=64)
    mid = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * mid) / np.maximum(w0, 1)
    m1 = (np.sum(hist * mid) - np.cumsum(hist * mid)) / np.maximum(w1, 1)
    cut = mid[int(np.argmax(w0 * w1 * (m0 - m1) ** 2))]
    a = x < cut
    dark_a = float(lum[a].mean()) if a.any() else 0.0
    bright_a = float(lum[~a].mean()) if (~a).any() else 0.0
    ok_a = bright_a > ratio * max(dark_a, 0.01) and 0.08 < a.mean() < 0.85
    report["luminance"] = {"ok": bool(ok_a), "dark": dark_a, "bright": bright_a, "share": float(a.mean())}
    top, bottom = rgb.max(1), rgb.min(1)
    feat = np.stack([x, (top - bottom) / np.maximum(top, 1e-3)], 1)
    feat = (feat - feat.mean(0)) / np.maximum(feat.std(0), 1e-6)
    order = np.argsort(feat[:, 0])
    k = max(5, len(feat) // 10)
    centres = np.stack([feat[order[:k]].mean(0), feat[order[-k:]].mean(0)])
    b = np.zeros(len(feat), bool)
    for _ in range(25):
        b = np.argmin(((feat[:, None, :] - centres[None]) ** 2).sum(2), axis=1) == 0
        for j, m in ((0, b), (1, ~b)):
            if m.any():
                centres[j] = feat[m].mean(0)
    apart = float(np.linalg.norm(centres[0] - centres[1]))
    within = float(np.sqrt(np.mean([(feat[m] - centres[j]).var(0).sum() for j, m in ((0, b), (1, ~b)) if m.any()])))
    dark_b = float(lum[b].mean()) if b.any() else 0.0
    bright_b = float(lum[~b].mean()) if (~b).any() else 0.0
    ok_b = apart > separation * within and 0.08 < b.mean() < 0.85 and dark_b < bright_b
    report["clusters"] = {"ok": bool(ok_b), "separation": apart, "within": within, "share": float(b.mean())}
    if ok_a:
        return a, report
    if ok_b:
        return b, report
    return None, report


def rasterize(xy, tri, width, height, chunk=3_000_000):
    """Every pixel centre inside every triangle: (pixel index, triangle index, barycentric weights). xy are positions in pixels."""
    a, b, c = xy[tri[:, 0]], xy[tri[:, 1]], xy[tri[:, 2]]
    lo = np.maximum(np.floor(np.minimum(np.minimum(a, b), c) - 0.5).astype(np.int64) + 1, 0)
    hi = np.floor(np.maximum(np.maximum(a, b), c) - 0.5).astype(np.int64)
    hi[:, 0] = np.minimum(hi[:, 0], width - 1)
    hi[:, 1] = np.minimum(hi[:, 1], height - 1)
    span = np.maximum(hi - lo + 1, 0)
    count = span[:, 0] * span[:, 1]
    area = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    keep = np.nonzero((count > 0) & (np.abs(area) > 1e-12))[0]
    pixels, tris, barys = [], [], []
    start, ends = 0, np.cumsum(count[keep])
    while start < len(keep):
        stop = max(int(np.searchsorted(ends, (ends[start - 1] if start else 0) + chunk, side="right")), start + 1)
        ids = keep[start:stop]
        n = count[ids]
        t = np.repeat(ids, n)
        local = np.arange(len(t)) - np.repeat(np.cumsum(n) - n, n)
        w = span[t, 0]
        px, py = lo[t, 0] + local % w, lo[t, 1] + local // w
        cx, cy = px + 0.5, py + 0.5
        ar = area[t]
        w0 = ((b[t, 0] - cx) * (c[t, 1] - cy) - (b[t, 1] - cy) * (c[t, 0] - cx)) / ar
        w1 = ((c[t, 0] - cx) * (a[t, 1] - cy) - (c[t, 1] - cy) * (a[t, 0] - cx)) / ar
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
        pixels.append((py * width + px)[inside])
        tris.append(t[inside])
        barys.append(np.stack([w0, w1, w2], 1)[inside])
        start = stop
    if not pixels:
        return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros((0, 3))
    return np.concatenate(pixels), np.concatenate(tris), np.concatenate(barys)


def front_depth(positions, tris, size, origin, pixel):
    """The nearest surface in a front view (-y is the front) as maps over a grid of `size` = (width, height) pixels of `pixel`
    model units, whose top left is (origin x, origin z): (depth per pixel or inf, the triangle that gives it or -1), both flat."""
    width, height = size
    xy = np.stack([(positions[:, 0] - origin[0]) / pixel, (origin[1] - positions[:, 2]) / pixel], 1)
    pix, tt, bary = rasterize(xy, tris, width, height)
    frag = np.einsum("nk,nk->n", bary, positions[:, 1][tris[tt]])
    order = np.lexsort((frag, pix))                       # nearest first at each pixel
    pix, first = np.unique(pix[order], return_index=True)
    depth = np.full(width * height, np.inf)
    triangle = np.full(width * height, -1, np.int64)
    depth[pix] = frag[order][first]
    triangle[pix] = tt[order][first]
    return depth, triangle


def deepest_within(depth, radius):
    """A maximum filter over the finite depths of a 2D map (inf stays where nothing is within `radius` pixels): the deepest surface
    round each pixel, so thin things in front of the skin (wire glasses, lashes, strands) do not count."""
    covered = np.isfinite(depth)
    deep = np.where(covered, depth, -np.inf)
    for axis in (0, 1):
        acc = deep.copy()
        for k in range(1, radius + 1):
            for sign in (1, -1):
                acc = np.maximum(acc, np.roll(deep, sign * k, axis))
        deep = acc
    return np.where(covered, deep, np.inf)


def fill_holes_and_depth(depth, rounds=400, smooth=20):
    """(hull, filled) for a 2D depth map (inf where nothing): the hull is the covered area with its holes filled (what the outside
    cannot reach by spreading in from the map's frame), and `filled` has a depth on all of it, spread in from the covered pixels and
    smoothed where it was filled."""
    covered = np.isfinite(depth)
    h, w = covered.shape
    outside = np.zeros((h, w), bool)
    outside[0, :] = outside[-1, :] = outside[:, 0] = outside[:, -1] = True
    outside &= ~covered
    for _ in range(h + w):
        grown = outside.copy()
        grown[1:] |= outside[:-1]
        grown[:-1] |= outside[1:]
        grown[:, 1:] |= outside[:, :-1]
        grown[:, :-1] |= outside[:, 1:]
        grown &= ~covered
        if (grown == outside).all():
            break
        outside = grown
    hull = ~outside
    filled = np.where(covered, depth, np.nan)
    known = covered.copy()
    for _ in range(rounds):
        todo = hull & ~known
        if not todo.any():
            break
        acc = np.zeros((h, w))
        cnt = np.zeros((h, w))
        for shift, axis in ((1, 0), (-1, 0), (1, 1), (-1, 1)):
            acc += np.roll(np.where(known, filled, 0.0), shift, axis)
            cnt += np.roll(known, shift, axis)
        new = todo & (cnt > 0)
        filled[new] = acc[new] / cnt[new]
        known |= new
    holes = hull & ~covered
    for _ in range(smooth):
        near = (np.roll(filled, 1, 0) + np.roll(filled, -1, 0) + np.roll(filled, 1, 1) + np.roll(filled, -1, 1)) / 4
        filled = np.where(holes & known, near, filled)
    return hull, filled


BACKING = {"env": 3.0, "back": 1.0, "fill_rounds": 400, "pixel": 0.4}      # mm of a 1.7 m person; head_graft.py's defaults, headgraft.py's too


def backing_maps(positions, tris, pixel, env_pixels, rounds):
    """The smooth surface just behind a patch, seen from the front: {"origin": (x, z) of the top left corner, "size": (width, height),
    "hull": the patch's area with its holes filled, "filled": the depth of the surface behind it over the hull (its deepest surface
    within `env_pixels`, so thin things in front of the skin do not count, and its holes filled from round them), "covered": where
    the patch itself is} over a grid of `pixel` model units."""
    lo_x, hi_z = float(positions[:, 0].min() - 2 * pixel), float(positions[:, 2].max() + 2 * pixel)
    width = int(math.ceil((positions[:, 0].max() - lo_x) / pixel)) + 3
    height = int(math.ceil((hi_z - positions[:, 2].min()) / pixel)) + 3
    depth, _tri = front_depth(positions, tris, (width, height), (lo_x, hi_z), pixel)
    depth = depth.reshape(height, width)
    hull, filled = fill_holes_and_depth(deepest_within(depth, env_pixels), rounds=rounds)
    return {"origin": (lo_x, hi_z), "size": (width, height), "hull": hull, "filled": filled, "covered": np.isfinite(depth)}


def sample_bilinear(image, u, v):
    """Colours of an (h, w, c) image at texture coordinates u, v (0..1, v downwards like glTF's), clamped at the borders."""
    h, w = image.shape[:2]
    x = np.clip(np.asarray(u, float) * w - 0.5, 0, w - 1)
    y = np.clip(np.asarray(v, float) * h - 0.5, 0, h - 1)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    return (image[y0, x0] * (1 - fx) * (1 - fy) + image[y0, x1] * fx * (1 - fy)
            + image[y1, x0] * (1 - fx) * fy + image[y1, x1] * fx * fy)


def front_colours(positions, uvs, tris, image, size, origin, pixel):
    """The colour of the surface seen from the front (-y is the front) at every pixel of a grid like backing_maps': (height, width, 3)
    colours (0 where nothing is) and a (height, width) mask of where something is. `image` is (h, w, 3) floats."""
    width, height = size
    xy = np.stack([(positions[:, 0] - origin[0]) / pixel, (origin[1] - positions[:, 2]) / pixel], 1)
    pix, tt, bary = rasterize(xy, tris, width, height)
    frag = np.einsum("nk,nk->n", bary, positions[:, 1][tris[tt]])
    order = np.lexsort((frag, pix))
    pix_sorted, first = np.unique(pix[order], return_index=True)
    win = order[first]
    uv = np.einsum("nk,nkd->nd", bary[win], uvs[tris[tt[win]]])
    colour = np.zeros((width * height, image.shape[2]))
    colour[pix_sorted] = sample_bilinear(image, uv[:, 0], uv[:, 1])
    covered = np.zeros(width * height, bool)
    covered[pix_sorted] = True
    return colour.reshape(height, width, -1), covered.reshape(height, width)


def spread_colour(values, known, region, rounds=80, smooth=6):
    """(values, filled): the pixels of `region` that are not `known` take the colours round them, spread in ring by ring (a new pixel is
    the mean of its known neighbours), and what was filled is smoothed. `values` is (h, w, c)."""
    values = np.asarray(values, float).copy()
    have = known.copy()
    for _ in range(rounds):
        todo = region & ~have
        if not todo.any():
            break
        acc = np.zeros_like(values)
        cnt = np.zeros(have.shape)
        for shift, axis in ((1, 0), (-1, 0), (1, 1), (-1, 1)):
            acc += np.roll(values * have[..., None], shift, axis)
            cnt += np.roll(have, shift, axis)
        new = todo & (cnt > 0)
        values[new] = acc[new] / cnt[new][:, None]
        have |= new
    filled = have & ~known
    for _ in range(smooth):
        near = (np.roll(values, 1, 0) + np.roll(values, -1, 0) + np.roll(values, 1, 1) + np.roll(values, -1, 1)) / 4
        values = np.where(filled[..., None], near, values)
    return values, filled


def chamfer_inside(mask):
    """Distance in pixels from every pixel of a boolean mask to the nearest pixel outside it (two passes, 1 per step)."""
    d = np.where(mask, 1e6, 0.0)
    h, w = d.shape
    for _ in range(2):
        for y in range(1, h):
            d[y] = np.minimum(d[y], d[y - 1] + 1)
        for x in range(1, w):
            d[:, x] = np.minimum(d[:, x], d[:, x - 1] + 1)
        for y in range(h - 2, -1, -1):
            d[y] = np.minimum(d[y], d[y + 1] + 1)
        for x in range(w - 2, -1, -1):
            d[:, x] = np.minimum(d[:, x], d[:, x + 1] + 1)
    return d


def boundary_loops(edges):
    """The connected pieces of a set of boundary edges ((n, 2) vertex numbers): a list of vertex lists."""
    neighbours = {}
    for a, b in np.asarray(edges).tolist():
        neighbours.setdefault(a, []).append(b)
        neighbours.setdefault(b, []).append(a)
    seen, loops = set(), []
    for v0 in neighbours:
        if v0 in seen:
            continue
        piece, stack = [], [v0]
        seen.add(v0)
        while stack:
            x = stack.pop()
            piece.append(x)
            for y in neighbours[x]:
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        loops.append(piece)
    return loops


def outer_loop(positions, loops, axes=(0, 2)):
    """The loop with the widest outline over `axes` (the patch's outer edge; the others are holes in it)."""
    best, best_area = [], -1.0
    for piece in loops:
        box = positions[piece]
        area = float((box[:, axes[0]].max() - box[:, axes[0]].min()) * (box[:, axes[1]].max() - box[:, axes[1]].min()))
        if area > best_area:
            best, best_area = piece, area
    return best


def surface_distance(positions, edges, sources):
    """Distance along the edges of a mesh from a set of source vertices, and which source each vertex is nearest: (distance, source).
    Dijkstra, multi-source; vertices that no edge reaches stay at inf and -1."""
    n = len(positions)
    edges = np.asarray(edges)
    adjacent = [[] for _ in range(n)]
    lengths = np.linalg.norm(positions[edges[:, 0]] - positions[edges[:, 1]], axis=1)
    for (a, b), w in zip(edges.tolist(), lengths.tolist()):
        adjacent[a].append((b, w))
        adjacent[b].append((a, w))
    dist = np.full(n, np.inf)
    origin = np.full(n, -1, np.int64)
    heap = []
    for v in sources:
        dist[v], origin[v] = 0.0, v
        heap.append((0.0, int(v)))
    heapq.heapify(heap)
    while heap:
        d, v = heapq.heappop(heap)
        if d > dist[v]:
            continue
        for u, w in adjacent[v]:
            if d + w < dist[u]:
                dist[u], origin[u] = d + w, origin[v]
                heapq.heappush(heap, (d + w, u))
    return dist, origin


def smoothstep(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def opening(faces, allowed, neighbours, rings):
    """Take thin strips and stair-steps off a set of faces: `rings` rounds of removing the faces of `allowed` that touch the outside
    of the set, then as many of giving back the removed faces of `allowed` that touch what is left. neighbours[f] = the faces next
    to face f. Faces that are not `allowed` are never removed."""
    keep = set(faces)
    for _ in range(rings):
        edge = {f for f in keep if f in allowed and any(g not in keep for g in neighbours[f])}
        keep -= edge
    for _ in range(rings):
        grow = {g for f in keep for g in neighbours[f] if g in faces and g not in keep and g in allowed}
        keep |= grow
    return keep
