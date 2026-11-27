"""Background removal for sprites generated on a flat, saturated key colour (Pillow + numpy only).

The generator asks the model for a solid magenta (or green) backdrop. We measure the real backdrop hue from
the image border, flood-fill inwards from the border so interior details of the same hue survive, then soften
the edge, remove colour spill and trim to the subject.
"""
import io

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def _hsv(rgb):
    hsv = np.asarray(Image.fromarray(rgb, "RGB").convert("HSV"), dtype=np.float32)
    return hsv[..., 0] * (360.0 / 255.0), hsv[..., 1] / 255.0, hsv[..., 2] / 255.0


def _hue_diff(h, center):
    d = np.abs(h - center)
    return np.minimum(d, 360.0 - d)


def _border_ring(a, width):
    ring = np.zeros(a.shape[:2], dtype=bool)
    ring[:width, :] = ring[-width:, :] = ring[:, :width] = ring[:, -width:] = True
    return ring


def _border_connected(candidate):
    """True where `candidate` pixels are connected (4-neighbour) to the image border."""
    # .copy(): fromarray() images are read-only, and floodfill silently does nothing on those
    img = Image.fromarray((candidate * 255).astype(np.uint8), "L").copy()
    h, w = candidate.shape
    seeds = [(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)] + \
            [(0, y) for y in range(h)] + [(w - 1, y) for y in range(h)]
    px = img.load()
    for xy in seeds:
        if px[xy] == 255:
            ImageDraw.floodfill(img, xy, 128)
    return np.asarray(img) == 128


def _enclosed_regions(pockets, min_area):
    """Blobs of key colour enclosed by the subject (e.g. the sliver between an arm and the torso).

    `pockets` is sparse, so exact 8-connected labelling with a small union-find is cheap. Blobs smaller than
    `min_area` px are kept as subject detail.
    """
    ys, xs = np.nonzero(pockets)
    out = np.zeros_like(pockets)
    n = len(ys)
    if n == 0 or n > 500_000:
        return out
    ys, xs = ys.tolist(), xs.tolist()
    index = {(y, x): i for i, (y, x) in enumerate(zip(ys, xs))}
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i, (y, x) in enumerate(zip(ys, xs)):
        for dy, dx in ((0, 1), (1, -1), (1, 0), (1, 1)):
            j = index.get((y + dy, x + dx))
            if j is not None:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    roots = [find(i) for i in range(n)]
    size = {}
    for r in roots:
        size[r] = size.get(r, 0) + 1
    keep = np.array([size[r] >= min_area for r in roots])
    out[np.array(ys)[keep], np.array(xs)[keep]] = True
    return out


def _dilate(mask, n=1):
    out = mask.copy()
    for _ in range(n):
        p = np.pad(out, 1, constant_values=False)
        out = (p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
               | p[:-2, :-2] | p[:-2, 2:] | p[2:, :-2] | p[2:, 2:])
    return out


def _bleed(rgb, known, wanted, rings=4):
    """(rgb, reached): the `wanted` pixels coloured from the `known` ones, a ring at a time, each the mean of its known
    neighbours; `reached` says which wanted pixels got a colour within `rings` rings."""
    rgb, known = rgb.copy(), known.copy()
    for _ in range(rings):
        todo = wanted & ~known
        if not todo.any():
            break
        padded_known = np.pad(known, 1)
        padded_rgb = np.pad(rgb, ((1, 1), (1, 1), (0, 0)))
        total, count = np.zeros_like(rgb), np.zeros(known.shape)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy or dx:
                    k = padded_known[1 + dy:1 + dy + known.shape[0], 1 + dx:1 + dx + known.shape[1]]
                    total += padded_rgb[1 + dy:1 + dy + known.shape[0], 1 + dx:1 + dx + known.shape[1]] * k[..., None]
                    count += k
        fill = todo & (count > 0)
        rgb[fill] = total[fill] / count[fill][:, None]
        known |= fill
    return rgb, known & wanted


SHADOW_STEP = 0.03          # the most a shadow's brightness changes from one pixel to the next (0-1)
SHADOW_LIGHT = 0.75         # a region kept as shadow is at least this bright, as a share of the backdrop's brightness
SHADOW_MIN_PX = 2000        # and at least this big


def _grow_smooth(start, allowed, v, step):
    """`start` grown into `allowed` pixels, one 4-neighbour step at a time, where brightness changes by less than `step`."""
    ys, xs = np.nonzero(allowed)
    grown = np.zeros_like(start)
    if not len(ys):
        return grown
    y0, y1, x0, x1 = max(ys.min() - 1, 0), ys.max() + 2, max(xs.min() - 1, 0), xs.max() + 2
    g, ok, vv = start[y0:y1, x0:x1].copy(), allowed[y0:y1, x0:x1], v[y0:y1, x0:x1]
    h, w = g.shape
    pv = np.pad(vv, 1, constant_values=-9.0)
    steps = [((dy, dx), ok & (np.abs(vv - pv[1 + dy:1 + dy + h, 1 + dx:1 + dx + w]) < step))
             for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1))]
    while True:
        pg = np.pad(g, 1)
        add = np.zeros_like(g)
        for (dy, dx), smooth in steps:
            add |= smooth & pg[1 + dy:1 + dy + h, 1 + dx:1 + dx + w]
        add &= ~g
        if not add.any():
            break
        g |= add
    grown[y0:y1, x0:x1] = g & ~start[y0:y1, x0:x1]
    return grown


def _regions(mask, min_px):
    """Each 4-connected region of `mask` with at least `min_px` pixels, as a mask."""
    img = Image.fromarray((mask * 255).astype(np.uint8), "L").copy()
    out = []
    for y, x in zip(*(axis.tolist() for axis in np.nonzero(mask))):
        if img.getpixel((x, y)) == 255:
            ImageDraw.floodfill(img, (x, y), 128)
            region = np.asarray(img) == 128
            if region.sum() >= min_px:
                out.append(region)
            img.paste(0, mask=Image.fromarray((region * 255).astype(np.uint8)))
    return out


def _cast_shadow(rgb, bg, h, s, v, key_hue, border_rgb, hue_tol):
    """The backdrop's own shadow, which the models draw between the feet or under a table though asked for none: the
    backdrop's colour, darker, and reached from it through small changes in brightness, so it is too far from the backdrop
    for the colour test but plainly backdrop. A figure of the backdrop's hue can be shaded as smoothly (a blue alien on a
    blue backdrop), so a region is kept as shadow only when it is big and at least SHADOW_LIGHT as bright as the backdrop:
    on 171 sprites every region that passed was backdrop, and every darker one that would have passed by sitting at the
    feet was partly a boot, trousers or a skirt."""
    f = rgb.astype(np.float32)
    dot = f[..., 0] * border_rgb[0] + f[..., 1] * border_rgb[1] + f[..., 2] * border_rgb[2]   # not `@`: anim._apply
    same_colour = dot / (np.sqrt((f ** 2).sum(axis=2)) * float(np.sqrt((border_rgb ** 2).sum())) + 1e-6) > 0.99
    allowed = (_hue_diff(h, key_hue) < hue_tol) & (s > 0.5) & same_colour & ~bg
    grown = _grow_smooth(bg, allowed, v, SHADOW_STEP)
    shadow = np.zeros_like(bg)
    if grown.sum() < SHADOW_MIN_PX:
        return shadow
    key_v = float(np.median(v[_border_ring(rgb, 6)]))
    for region in _regions(grown, SHADOW_MIN_PX):
        if float(np.median(v[region])) >= SHADOW_LIGHT * key_v:
            shadow |= region
    return shadow


def remove_background(png_bytes, hard_edges=False, trim=True, pad=6, hue_tol=24.0):
    """Return (rgba_png_bytes, info). On failure returns the original bytes and info['keyed'] == False."""
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    rgb = np.asarray(im, dtype=np.uint8)
    h, s, v = _hsv(rgb)

    ring = _border_ring(rgb, 6)
    chroma = ring & (s > 0.25) & (v > 0.2)
    if chroma.sum() < 0.6 * ring.sum():
        return png_bytes, {"keyed": False, "reason": "backdrop is not a flat saturated colour"}
    ang = np.deg2rad(h[chroma])
    key_hue = float(np.rad2deg(np.arctan2(np.sin(ang).mean(), np.cos(ang).mean())) % 360.0)

    # Hue alone can join blue clothes to a blue backdrop (or pink clothes to a
    # magenta one), carving large holes through the figure. Keep the candidate
    # near the measured border colour as well. Allow extra room for a textured
    # backdrop, measured from its own border rather than from the subject.
    border_rgb = np.median(rgb[chroma].astype(np.float32), axis=0)
    border_distance = np.linalg.norm(rgb[chroma].astype(np.float32) - border_rgb, axis=1)
    colour_tol = max(70.0, min(120.0, float(np.percentile(border_distance, 99)) * 2.5))
    colour_distance = np.linalg.norm(rgb.astype(np.float32) - border_rgb, axis=2)
    candidate = ((_hue_diff(h, key_hue) < hue_tol) & (s > 0.28) & (v > 0.18)
                 & (colour_distance < colour_tol))
    bg = _border_connected(candidate)
    if bg.mean() < 0.15:
        return png_bytes, {"keyed": False, "reason": "could not isolate the backdrop"}
    # The backdrop's shadow is measured from the backdrop the border reaches, as on the 171 sprites it was checked on, and
    # added after the pockets below, whose measured backdrop colour its darker pixels would otherwise move.
    shadow = _cast_shadow(rgb, bg, h, s, v, key_hue, border_rgb, hue_tol)

    # Pockets the border fill could not reach: only pixels close to the measured backdrop colour, so genuine
    # same-hue details (lips, a scarf) are not swallowed.
    bg_rgb = np.median(rgb[bg].astype(np.float32), axis=0)
    close = np.linalg.norm(rgb.astype(np.float32) - bg_rgb, axis=2) < 70
    bg |= _enclosed_regions(candidate & close & ~bg, min_area=max(24, int(bg.size * 0.00003)))
    bg |= shadow
    bg = _dilate(bg, 1)  # eat the 1px fringe where subject and backdrop blend
    alpha_img = Image.fromarray(((~bg) * 255).astype(np.uint8), "L")
    if not hard_edges:
        alpha_img = alpha_img.filter(ImageFilter.GaussianBlur(0.8))
    alpha = np.asarray(alpha_img)

    # remove key-colour spill on pixels near the cut edge
    near = _dilate(bg, 3) & ~bg
    out = rgb.astype(np.float32)
    r, g, b = out[..., 0], out[..., 1], out[..., 2]
    if 240 <= key_hue <= 350:            # magenta / pink family
        spill = np.clip(np.minimum(r, b) - g, 0, None)
        r[near] -= 0.8 * spill[near]
        b[near] -= 0.8 * spill[near]
    elif 70 <= key_hue <= 170:           # green family
        spill = np.clip(g - np.maximum(r, b), 0, None)
        g[near] -= 0.8 * spill[near]
    # The softened edge gives the backdrop pixels next to the figure some opacity, and they are still the backdrop's
    # colour: a magenta fringe round the whole figure. Give them the colour of the figure pixels beside them instead.
    edge = bg & (alpha > 0)
    if edge.any():
        out, reached = _bleed(out, ~bg, edge)
        alpha = np.where(edge & ~reached, 0, alpha).astype(np.uint8)
    out = np.clip(out, 0, 255).astype(np.uint8)

    rgba = np.dstack([out, alpha])
    result = Image.fromarray(rgba, "RGBA")
    if trim:
        ys, xs = np.where(alpha > 8)
        if len(xs):
            box = (max(xs.min() - pad, 0), max(ys.min() - pad, 0),
                   min(xs.max() + 1 + pad, result.width), min(ys.max() + 1 + pad, result.height))
            result = result.crop(box)
    buf = io.BytesIO()
    result.save(buf, "PNG", optimize=True)
    return buf.getvalue(), {"keyed": True, "key_hue": round(key_hue), "size": list(result.size)}


def grey_image(im):
    """The picture in shades of grey, in the same mode (RGB or RGBA) so later steps need not care, transparency kept.

    For a monochrome style (manga): its words ask for no colour, but a character described in colour (red hair, a green cloak)
    still comes out coloured on every engine measured, and the negative prompt is ignored by the cfg-1 engines. Applied after
    the backdrop is cut out, since keying needs the coloured backdrop."""
    rgba = im.convert("RGBA")
    grey = rgba.convert("L")
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        return Image.merge("RGBA", (grey, grey, grey, rgba.getchannel("A")))
    return Image.merge("RGB", (grey, grey, grey))


def grey_png(png):
    """grey_image() for PNG bytes."""
    buf = io.BytesIO()
    grey_image(Image.open(io.BytesIO(png))).save(buf, "PNG", optimize=True)
    return buf.getvalue()
