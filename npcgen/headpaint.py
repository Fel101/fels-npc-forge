"""Paint a detailed head's colour texture from the four pictures it was made from, with the exact cameras of Pixal3D's rig.

Why: Pixal3D's own colour for the head (its texture stage) is off where it matters. Measured on Natalie: the skin came out a
more orange, more saturated tan than her pictures, and everything thin, which is where a face has its detail, took a
muddy olive: the rims of the eyelids, the brows, the strands of hair at the sides. The pictures themselves have the right
colours, and the model was built to line up with them: Pixal3D reads a picture as seen by a camera at a known place with a
20 degree field of view, so the model can be painted from the pictures with those very cameras, exactly (perspective and
all), instead of fitting each picture to the model's outline the way npcgen/paint3d.py does for a whole body.

How, like paint3d: every texel of the model's texture is a point on its surface; each picture that sees it (a depth buffer
in the picture's camera, an outline test, how squarely it faces the camera) gives its colour, weighted; where no picture
sees the surface the model's own colour stays, shifted to the pictures' local tone. What is new:
  - the camera is exact, but each picture is still lined up with the model's silhouette by a small scale and shift (the
    four pictures were drawn at slightly different sizes, and Pixal3D made a compromise of them);
  - the side and back pictures are balanced to the front one where they see the same surface, so their different light
    (each was drawn by a separate edit) does not show as a seam.
The pictures are the framed ones of npcgen/head3d.py (one scale for all, the head on black) and their masks.
"""
import io
import math

import numpy as np
from PIL import Image

from . import three_d
from .paint3d import (Glb, _bilinear, _dilate_into, _fill_colour, _gain_field, _near_colour, _shift_reduce, _smooth_normals,
                      dot3, rasterize, texel_samples, textured_primitives)

SIZE = 1024                # the framed pictures
AZIMUTH = {"front": 0.0, "side_left": 90.0, "back": 180.0, "side_right": 270.0}     # the rig's orbit, in degrees
ORDER = ("front", "side_left", "side_right", "back")      # the front is the reference the others are balanced to
DEPTH_SCALE = 2            # depth buffers at twice the picture's resolution
VISIBLE_EPS = 0.006        # of the model height: how far behind the nearest surface still counts as visible
OCCLUSION_JUMP = 0.03      # of the model height: a nearer surface this close in the picture means an occluding edge
MASK_ERODE = 2             # picture pixels: stay off the outline, where the background bleeds in
ALPHA_FROM = 0.2           # how squarely the best picture faces a texel: from here the pictures start to replace the...
ALPHA_TO = 0.55            # ...generator's colour, and by here they replace it altogether
SEEN_FLOOR = 0.01          # view weight for a texel the picture sees only edge-on
# Measured on Natalie (power 1.5 to 8, the front picture's weight x1 to x4): a low power blends the pictures over wide
# strips, and each was drawn by a separate edit, so the eyes, brows and lips of one land beside those of another. 6 with
# the front picture x3 keeps the face to the front picture out to the cheeks and blends the others only where they face
# the surface far better. (How much the pictures replace the generator's colour does not follow the power: that is ALPHA_*.)
FACING_POWER = 6.0         # a picture's weight is how squarely it faces the surface, to this power
VIEW_BOOST = {"front": 3.0}  # a view's weight is multiplied by this: the front picture carries the face
WELL_PAINTED = 0.85        # a texel is "painted" for the tone and fill of what is left at this share of the pictures' colour
SEAM_DILATE = 6            # texels: carry the new colour past UV island edges so filtering never mixes in old colour
GOOD_OUTLINE = 0.85        # silhouette overlap at which a picture is fully trusted...
BAD_OUTLINE = 0.6          # ...and below which it is not used at all
BALANCE_WEIGHT = 0.3       # both views must see a texel at least this well for it to count in balancing their colour
BALANCE_MIN = 500          # texels needed for that
BALANCE_LIMIT = (0.8, 1.25)
HAIR_OPENING = 0.012       # of the picture: loose bits of the model's hair mask narrower than this are dropped (0.004 left specks on the face that were filled black)
HAIR_SHARE = 0.5           # a texel is on the model's hair where at least this share of the pictures that see it find hair there
HAIR_CLEAN = 0.05          # a hair texel is a colour source for the hair around it if no more than this share of its paint was refused


def to_rig(rows):
    """glTF (x right, y up, z toward the front) as the rig's world (x right, y away from the front camera, z up)."""
    return np.stack([rows[:, 0], -rows[:, 2], rows[:, 1]], 1)


class Camera:
    """One camera of the rig: `back` points from the middle of the model to the camera, which looks at the middle."""

    def __init__(self, view):
        a = math.radians(AZIMUTH[view])
        tan = math.tan(math.radians(three_d.RIG_FOV) / 2)
        self.back = np.array([math.sin(a), -math.cos(a), 0.0])
        self.right = np.array([math.cos(a), math.sin(a), 0.0])
        self.up = np.cross(self.back, self.right)
        self.origin = self.back * (three_d.RIG_PAD * 0.5 / tan)
        self.focal = 0.5 * SIZE / tan

    def project(self, points):
        """(x, y, depth) of points in the picture: x right and y down in pixels, depth the distance along the view."""
        rel = points - self.origin
        depth = -dot3(rel, self.back)
        return (self.focal * dot3(rel, self.right) / depth + SIZE / 2,
                SIZE / 2 - self.focal * dot3(rel, self.up) / depth, depth)


def coverage(xy, tri, size=SIZE):
    """Which pixels the model covers, as the camera sees it."""
    pix, _t, _bary = rasterize(xy, tri, size, size)
    image = np.zeros(size * size, bool)
    image[pix] = True
    return image.reshape(size, size)


def _edges(image):
    """The boundary pixels of a boolean image (pixels with a background neighbour)."""
    padded = np.pad(image, 1)
    inner = padded[1:-1, 1:-1] & padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
    return image & ~inner


def _distance(edge):
    """Distance in pixels from every pixel to the nearest True one of `edge`: a two-pass chamfer (3-4 weights)."""
    h, w = edge.shape
    d = np.where(edge, 0.0, 1e6)
    for y in range(h):
        if y:
            d[y] = np.minimum(d[y], d[y - 1] + 1.0)
            d[y, 1:] = np.minimum(d[y, 1:], d[y - 1, :-1] + 1.4142)
            d[y, :-1] = np.minimum(d[y, :-1], d[y - 1, 1:] + 1.4142)
        for x in range(1, w):
            d[y, x] = min(d[y, x], d[y, x - 1] + 1.0)
    for y in range(h - 1, -1, -1):
        if y < h - 1:
            d[y] = np.minimum(d[y], d[y + 1] + 1.0)
            d[y, 1:] = np.minimum(d[y, 1:], d[y + 1, :-1] + 1.4142)
            d[y, :-1] = np.minimum(d[y, :-1], d[y + 1, 1:] + 1.4142)
        for x in range(w - 2, -1, -1):
            d[y, x] = min(d[y, x], d[y, x + 1] + 1.0)
    return d


def align(model_covers, mask, work=256, keep=0.6):
    """(scale, dx, dy, overlap): the small scale about the middle of the picture and shift, in picture pixels, that lay the model's
    outline on the picture's, and the overlap (intersection over union) of the two silhouettes they then have.

    Fitted on the outlines, by how far each point of the model's is from the picture's outline, averaged over the nearest
    `keep` of them only. The silhouettes themselves do not do: the four pictures were drawn separately, so a braid or a fall
    of hair is in one and not in another or in the model, and those differences pulled a silhouette overlap several percent
    of the head off (a side picture was scaled up 10% to cover a braid the model does not have) while the face's profile
    was already right."""
    k = SIZE // work
    cover = model_covers.reshape(work, k, work, k).mean((1, 3)) > 0.5
    picture = mask.reshape(work, k, work, k).mean((1, 3)) > 0.5
    distance = _distance(_edges(picture))
    ys, xs = np.nonzero(_edges(cover))
    c = work / 2

    def cost(s, dx, dy):
        u = np.clip(np.rint(c + s * (xs - c) + dx).astype(np.int64), 0, work - 1)
        v = np.clip(np.rint(c + s * (ys - c) + dy).astype(np.int64), 0, work - 1)
        d = np.sort(distance[v, u])
        return float(d[:max(1, int(len(d) * keep))].mean())

    s, dx, dy = 1.0, 0.0, 0.0
    best = cost(s, dx, dy)
    for ds, dd in ((0.03, 6.0), (0.01, 2.0), (0.004, 1.0), (0.002, 0.5)):
        moved = True
        while moved:
            moved = False
            for cand in ((s + ds, dx, dy), (s - ds, dx, dy), (s, dx + dd, dy), (s, dx - dd, dy), (s, dx, dy + dd), (s, dx, dy - dd)):
                if not 0.85 <= cand[0] <= 1.15 or abs(cand[1]) > 24 or abs(cand[2]) > 24:
                    continue
                value = cost(*cand)
                if value < best - 1e-3:
                    best, (s, dx, dy), moved = value, cand, True

    # the overlap of the two silhouettes with that fit, for the report and for how far the picture is trusted
    ys2, xs2 = np.mgrid[0:work, 0:work]
    u = np.rint(c + (xs2 - c - dx) / s).astype(np.int64)
    v = np.rint(c + (ys2 - c - dy) / s).astype(np.int64)
    ok = (u >= 0) & (u < work) & (v >= 0) & (v < work)
    moved_cover = np.zeros_like(cover)
    moved_cover[ok] = cover[v[ok], u[ok]]
    union = np.count_nonzero(moved_cover | picture)
    return s, dx * k, dy * k, (np.count_nonzero(moved_cover & picture) / union if union else 0.0)


def depth_buffers(camera, points, tri):
    """(nearest depth per pixel at DEPTH_SCALE x the picture, the same with each pixel's neighbours' nearest folded in)."""
    k = DEPTH_SCALE
    x, y, depth = camera.project(points)
    pix, t, bary = rasterize(np.stack([x * k, y * k], 1), tri, SIZE * k, SIZE * k)
    fragment = np.einsum("nk,nk->n", bary, depth[tri[t]])
    zbuf = np.full(SIZE * k * SIZE * k, np.inf)
    np.minimum.at(zbuf, pix, fragment)
    zbuf = zbuf.reshape(SIZE * k, SIZE * k)
    return zbuf, _shift_reduce(zbuf, 2 * k, np.minimum)


def clay(view, pos, tri):
    """RGB array (SIZE x SIZE): the bare model, shaded grey on white, drawn where the picture has it (the camera and the fit),
    so a mask found on it lines up with the picture pixel for pixel."""
    cam, (s, dx, dy) = view["camera"], view["fit"]
    x, y, depth = cam.project(pos)
    c = SIZE / 2
    pix, t, bary = rasterize(np.stack([c + s * (x - c) + dx, c + s * (y - c) + dy], 1), tri, SIZE, SIZE)
    fragment = np.einsum("nk,nk->n", bary, depth[tri[t]])
    order = np.lexsort((fragment, pix))                    # the nearest surface first at each pixel
    pix, first = np.unique(pix[order], return_index=True)
    t = t[order][first]
    face = np.cross(pos[tri[t, 1]] - pos[tri[t, 0]], pos[tri[t, 2]] - pos[tri[t, 0]])
    face /= np.maximum(np.linalg.norm(face, axis=1, keepdims=True), 1e-12)
    light = cam.back * 0.8 + cam.up * 0.5 + cam.right * 0.3
    shade = 0.35 + 0.65 * np.abs(np.einsum("ij,j->i", face, light / np.linalg.norm(light)))
    image = np.full((SIZE * SIZE, 3), 255.0)
    image[pix] = 200.0 * shade[:, None]
    return np.clip(image, 0, 255).astype(np.uint8).reshape(SIZE, SIZE, 3)


def _png(rgb):
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "PNG")
    return buf.getvalue()


def _found(find, png, name, what):
    mask = np.asarray(find(png, name, what), bool)
    if mask.shape != (SIZE, SIZE):
        mask = np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).resize((SIZE, SIZE), Image.NEAREST)) > 127
    return mask


def hair_masks(find, view, rgb, pos, tri):
    """(the picture's hair, the model's hair) as boolean arrays in the picture's pixels, from `find(png, name, what)`
    (npcgen/hairmask.py: SAM 3.1). The model's is found on its bare shape drawn as that picture sees it, without its ears
    and eyes, which are never hair and which a segmenter takes for it now and then."""
    picture = _found(find, _png(np.asarray(rgb, np.uint8)), f"{view['name']}_picture", "hair")
    shape = _png(clay(view, pos, tri))
    model = (_found(find, shape, f"{view['name']}_model", "hair")
             & ~_found(find, shape, f"{view['name']}_model_ear", "ear")
             & ~_found(find, shape, f"{view['name']}_model_eye", "eye"))
    r = max(1, round(HAIR_OPENING * SIZE))
    model = _shift_reduce(_shift_reduce(model.astype(np.uint8), r, np.minimum), r, np.maximum).astype(bool)
    return picture, model


def paint(model_glb, pictures, masks, report=None, find=None):
    """The GLB with its base colour texture painted from the framed pictures ({view: RGB uint8 array}) and their masks
    ({view: float array 0..1}, 1 = the head), seen by the rig's cameras.

    `find` (npcgen/hairmask.py's finder: (PNG bytes, name, what) -> boolean mask): where a picture shows no hair on surface
    that the model has hair on, that picture gives the surface no colour, and hair no picture paints takes the colour of the
    well-painted hair around it. The model's hair is often not the pictures': Pixal3D turns loose curls seen from the front
    into flaps of hair beside the cheek, which a side picture, where there is only cheek, paints the colour of skin."""
    glb = Glb(model_glb)
    prims = textured_primitives(glb)
    if not prims:
        raise ValueError("The head has no UV-mapped colour texture to paint")
    report = report if report is not None else {}
    report["views"] = {}
    pos_all = np.concatenate([to_rig(p[0]) for p in prims])
    starts = np.cumsum([0] + [len(p[0]) for p in prims[:-1]])
    tri_all = np.concatenate([p[3] + s for p, s in zip(prims, starts)])
    height = float(pos_all[:, 2].max() - pos_all[:, 2].min())

    views = {}
    for view in ORDER:
        if view not in pictures:
            continue
        camera = Camera(view)
        x, y, _ = camera.project(pos_all)
        covers = coverage(np.stack([x, y], 1), tri_all)
        mask = masks[view] > 0.5
        s, dx, dy, overlap = align(covers, mask)
        trust = float(np.clip((overlap - BAD_OUTLINE) / (GOOD_OUTLINE - BAD_OUTLINE), 0, 1))
        zbuf, near = depth_buffers(camera, pos_all, tri_all)
        inner = _shift_reduce(mask.astype(np.uint8), MASK_ERODE, np.minimum).astype(bool)
        views[view] = dict(name=view, camera=camera, fit=(s, dx, dy), trust=trust, zbuf=zbuf, near=near, inner=inner,
                           rgb=np.asarray(pictures[view], np.float32), picture_hair=None, model_hair=None)
        report["views"][view] = {"outline_match": round(float(overlap), 3), "scale": round(float(s), 3),
                                 "shift": [round(float(dx), 1), round(float(dy), 1)], "trust": round(trust, 2)}
    if not any(v["trust"] > 0 for v in views.values()):
        raise ValueError("None of the four pictures lines up with the head (the outlines overlap by less than %d%%)"
                         % round(BAD_OUTLINE * 100))

    if find is not None:
        report["hair"] = {"views": {}}
        for view, v in views.items():
            if v["trust"] <= 0:
                continue
            v["picture_hair"], v["model_hair"] = hair_masks(find, v, pictures[view], pos_all, tri_all)
            report["hair"]["views"][view] = {"picture_hair": int(v["picture_hair"].sum()), "model_hair": int(v["model_hair"].sum()),
                                             "model_hair_not_in_picture": int((v["model_hair"] & ~v["picture_hair"]).sum())}

    def seen(camera_view, points, normals):
        """The colour of each point in one picture, its weight and quality there, and what was refused as hair."""
        cam, (s, dx, dy) = camera_view["camera"], camera_view["fit"]
        x, y, depth = cam.project(points)
        k = DEPTH_SCALE
        ix = np.clip((x * k).astype(np.int64), 0, SIZE * k - 1)
        iy = np.clip((y * k).astype(np.int64), 0, SIZE * k - 1)
        visible = ((depth <= camera_view["zbuf"][iy, ix] + VISIBLE_EPS * height)
                   & (depth <= camera_view["near"][iy, ix] + OCCLUSION_JUMP * height))
        c = SIZE / 2
        px, py = c + s * (x - c) + dx, c + s * (y - c) + dy
        row, column = np.clip(py.astype(np.int64), 0, SIZE - 1), np.clip(px.astype(np.int64), 0, SIZE - 1)
        in_picture = camera_view["inner"][row, column]
        if camera_view["model_hair"] is not None:
            model_here = camera_view["model_hair"][row, column]
            behind = model_here & ~camera_view["picture_hair"][row, column]      # the picture shows what is behind the model's hair
        else:
            model_here = behind = np.zeros(len(points), bool)
        toward = cam.origin - points
        toward /= np.maximum(np.linalg.norm(toward, axis=1, keepdims=True), 1e-12)
        facing = np.einsum("ij,ij->i", normals, toward)
        # a small floor keeps the picture's colour on surface it sees only edge-on (the sides of the thin lids and strands)
        weight = np.maximum(np.clip((facing - 0.05) / 0.95, 0, 1) ** FACING_POWER, SEEN_FLOOR) * visible * in_picture
        quality = np.clip(facing, 0, 1) * visible * in_picture * camera_view["trust"]      # how well this picture sees a texel
        weight *= camera_view["trust"] * VIEW_BOOST.get(camera_view["name"], 1.0)
        return dict(colour=_bilinear(camera_view["rgb"], px, py), weight=weight * ~behind, quality=quality * ~behind,
                    refused=weight * behind, seen=weight, hair=weight * model_here)

    images = {}
    for pos, nrm, uv, tri, image_index in prims:
        if image_index not in images:
            base = glb.image(image_index)
            images[image_index] = {"pixels": np.asarray(base.convert("RGB")).astype(np.float32), "parts": []}
        entry = images[image_index]
        th, tw = entry["pixels"].shape[:2]
        pix, points, normals = texel_samples(pos, nrm, uv, tri, (tw, th))
        points, normals = to_rig(points), to_rig(normals)
        normals = _smooth_normals(points, normals, height)
        layers = {view: seen(v, points, normals) for view, v in views.items()}
        # balance each picture's colour to the ones already counted, where they see the same surface well
        total = np.zeros((len(points), 3))
        weight = np.zeros(len(points))
        best = np.zeros(len(points))
        refused, hair, seen_weight = np.zeros(len(points)), np.zeros(len(points)), np.zeros(len(points))
        for view in ORDER:
            if view not in layers:
                continue
            colour, w, quality = layers[view]["colour"], layers[view]["weight"], layers[view]["quality"]
            refused += layers[view]["refused"]
            hair += layers[view]["hair"]
            seen_weight += layers[view]["seen"]
            best = np.maximum(best, quality)
            if weight.any():
                both = (w > BALANCE_WEIGHT) & (weight > BALANCE_WEIGHT)
                if both.sum() >= BALANCE_MIN:
                    shared = total[both] / weight[both, None]
                    gain = np.clip(shared.mean(0) / np.maximum(colour[both].mean(0), 1.0), *BALANCE_LIMIT)
                    colour = np.clip(colour * gain, 0, 255)
                    report["views"][view]["balance"] = [round(float(g), 3) for g in gain]
            total += colour * w[:, None]
            weight += w
            report["views"][view]["texels"] = report["views"][view].get("texels", 0) + int((w > 0).sum())
        entry["parts"].append((pix, points, total, weight, best, refused, hair, seen_weight))

    for image_index, entry in images.items():
        th, tw = entry["pixels"].shape[:2]
        old = entry["pixels"].reshape(-1, 3)
        pix = np.concatenate([p[0] for p in entry["parts"]])
        points = np.concatenate([p[1] for p in entry["parts"]])
        total = np.concatenate([p[2] for p in entry["parts"]])
        weight = np.concatenate([p[3] for p in entry["parts"]])
        best = np.concatenate([p[4] for p in entry["parts"]])
        refused = np.concatenate([p[5] for p in entry["parts"]])
        hair = np.concatenate([p[6] for p in entry["parts"]])
        seen_weight = np.concatenate([p[7] for p in entry["parts"]])
        pix, first = np.unique(pix, return_index=True)
        points, total, weight, best = points[first], total[first], weight[first], best[first]
        refused, hair, seen_weight = refused[first], hair[first], seen_weight[first]
        projected = total / np.maximum(weight, 1e-9)[:, None]
        alpha = np.clip((best - ALPHA_FROM) / (ALPHA_TO - ALPHA_FROM), 0, 1)[:, None]
        if find is not None:
            # Hair that no picture paints well (every picture that sees it shows what is behind it, or none faces it) takes
            # the colour of the well-painted hair around it, not the skin behind it and not the generator's muddy colour.
            on_hair = (seen_weight > 0) & (hair >= HAIR_SHARE * seen_weight)
            unpainted = on_hair & (alpha[:, 0] < 0.5) & (refused > 0)        # a picture showed what is behind it
            source = on_hair & (alpha[:, 0] >= 0.9) & (refused <= HAIR_CLEAN * (weight + refused))
            if unpainted.any() and source.any():
                near = np.flatnonzero(unpainted | source)
                fill = _fill_colour(points[near], source[near], projected[near], height, smooth=True)
                projected[near[unpainted[near]]] = fill[unpainted[near]]
                alpha[unpainted] = 1.0
            report["hair"]["refused_texels"] = report["hair"].get("refused_texels", 0) + int((refused > 0).sum())
            report["hair"]["filled_texels"] = report["hair"].get("filled_texels", 0) + int(unpainted.sum())
        # where no picture sees the surface, keep the generator's texture but shift it to the pictures' local tone...
        gain = _gain_field(points, alpha[:, 0], projected, old[pix], height)
        hidden = np.clip(old[pix] * gain, 0, 255)
        # ...unless painted surface lies within a couple of millimetres (the inside of a groove, behind a strand of hair)
        near, found = _near_colour(points, alpha[:, 0] >= WELL_PAINTED, projected, height)
        hidden[found] = near[found]
        colour = alpha * projected + (1 - alpha) * hidden
        new = old.copy()
        new[pix] = colour
        covered = np.zeros(th * tw, bool)
        covered[pix] = True
        new = _dilate_into(new.reshape(th, tw, 3), covered.reshape(th, tw), SEAM_DILATE)
        buf = io.BytesIO()
        Image.fromarray(np.clip(new, 0, 255).astype(np.uint8), "RGB").save(buf, "PNG", optimize=False, compress_level=6)
        glb.replace_image(image_index, buf.getvalue())
        report.setdefault("textures", []).append({"size": [tw, th], "painted_share": round(float((alpha > 0.99).mean()), 3)})
    return glb.dumps()
