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

    # Pockets the border fill could not reach: only pixels close to the measured backdrop colour, so genuine
    # same-hue details (lips, a scarf) are not swallowed.
    bg_rgb = np.median(rgb[bg].astype(np.float32), axis=0)
    close = np.linalg.norm(rgb.astype(np.float32) - bg_rgb, axis=2) < 70
    bg |= _enclosed_regions(candidate & close & ~bg, min_area=max(24, int(bg.size * 0.00003)))
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
