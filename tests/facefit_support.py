"""A made-up face scan for the tests of npcgen/facefit.py, facescan.py and the glasses-on-a-face routes: what npcgen/blender/face_scan.py writes, built from
a dome with a nose ridge, a hair fringe, cheeks and a head that can be turned, so the fit can be tested without Blender or a GPU."""
import base64

import numpy as np

REACH_X, BELOW, ABOVE, STEP = 0.16, 0.10, 0.09, 0.001        # (as in face_scan.py, in metres of a 1.7 m adult)


def make_scan(scale=1.0, head=1.0, nose=0.022, fringe=0.0, cheek=0.0, temple=0.0, roll=0.0, yaw=0.0, source="sdpose", face_width=True, eyes_y=1.56, eyes_z=0.075,
              hole=None, centre_x=0.0, scores=True):
    """A face scan dict. `scale` makes the whole figure bigger or smaller than a 1.7 m adult (an adult's face is 63 mm between the eyes and 135 mm across);
    `head` makes the face bigger or smaller next to the figure; `nose`, `fringe`, `cheek` and `temple` are how far in metres (at the face's size) the nose ridge, a
    lock of hair above the brows, the cheek bones and hair at the sides of the face (beyond 45 mm from the middle) stand out; `roll` and `yaw` (radians) turn the line between the eyes; `hole` is (x0, x1, y0, y1) in
    metres from the middle of the eyes, cut out of the surface."""
    fs = scale * head                                         # the face's size
    ipd = 0.063 * fs
    ry = np.array([[np.cos(yaw), 0, np.sin(yaw)], [0, 1, 0], [-np.sin(yaw), 0, np.cos(yaw)]])
    rz = np.array([[np.cos(roll), -np.sin(roll), 0], [np.sin(roll), np.cos(roll), 0], [0, 0, 1]])
    v = ry @ rz @ np.array([ipd, 0, 0])
    mid = np.array([centre_x, eyes_y, eyes_z])
    left, right = mid + v / 2, mid - v / 2
    unit = scale                                              # height / 1.7
    x0, y0, step = centre_x - REACH_X * unit, eyes_y - BELOW * unit, STEP * unit
    nx, ny = int(2 * REACH_X * unit / step), int((BELOW + ABOVE) * unit / step)
    xs = x0 + (np.arange(nx) + 0.5) * step - centre_x
    ys = y0 + (np.arange(ny) + 0.5) * step - eyes_y
    x, y = np.meshgrid(xs, ys)
    u, w = x / (0.078 * fs), (y + 0.02 * fs) / (0.115 * fs)
    r2 = u * u + w * w
    inside = r2 < 1
    z0 = eyes_z + 0.010 * fs                               # the brow plane stands this far in front of the eye points
    z = z0 + 0.05 * fs * (np.sqrt(np.clip(1 - r2, 0, 1)) - np.sqrt(1 - (0.02 / 0.115) ** 2))      # (exactly z0 on the eye line, in the middle)
    below = -y / fs                                        # metres below the eye line, at an adult's size
    ridge = np.clip(below / 0.025, 0, 1) * np.clip((0.05 - below) / 0.02, 0, 1)
    z = z + nose * fs * np.exp(-((x / (0.011 * fs)) ** 2)) * ridge
    above = y / fs - 0.022
    z = z + fringe * fs * np.clip(above / 0.004, 0, 1) * (np.abs(x) < 0.05 * fs)
    z = z + cheek * fs * np.exp(-(((np.abs(x) - 0.035 * fs) / (0.015 * fs)) ** 2) - (((y + 0.025 * fs) / (0.02 * fs)) ** 2))
    z = z + temple * fs * np.clip((np.abs(x) - 0.045 * fs) / (0.006 * fs), 0, 1)
    z = np.where(inside, z, np.nan)
    if hole is not None:
        hx0, hx1, hy0, hy1 = hole
        z = np.where((x >= hx0) & (x <= hx1) & (y >= hy0) & (y <= hy1), np.nan, z)
    lo, hi = [centre_x - 0.2, 0.0, -0.1], [centre_x + 0.2, 1.7 * scale, 0.12]
    return {"version": 1, "source": source, "model": {"min": lo, "max": hi, "height": 1.7 * scale, "vertices": 1000, "triangles": 2000},
            "eyes": {"left": [float(c) for c in left], "right": [float(c) for c in right]},
            "points": None, "scores": [0.9] * 68 if scores and source == "sdpose" else None,
            "face_width": 0.135 * fs if face_width else None,
            "grid": {"x0": float(x0), "y0": float(y0), "step": float(step), "nx": nx, "ny": ny,
                     "z": base64.b64encode(z.astype("<f4").tobytes()).decode("ascii")}}
