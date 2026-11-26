"""Draw web/icon.ico, the icon of Setup.exe and the Start menu, from web/icon.svg (the page's anvil).

    python scripts/make_icon.py            write web/icon.ico
    python scripts/make_icon.py --check    exit 1 when web/icon.ico is not what icon.svg draws now

Pillow cannot read SVG, so this draws the few shapes icon.svg uses (one rounded square, filled paths of straight lines and
cubic curves) at eight times each size and scales them down, one picture per size, so the small ones stay sharp.
"""
import argparse
import io
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "web" / "icon.svg"
ICO = ROOT / "web" / "icon.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)
SUPERSAMPLE = 8
CURVE_STEPS = 24
_TOKEN = re.compile(r"[MmLlHhVvCcZz]|-?(?:\d+\.?\d*|\.\d+)")


def _colour(text):
    text = text.lstrip("#")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


def path_points(d):
    """The outline of an SVG path as lists of (x, y) points, one list per subpath. Only M, L, H, V, C and Z, as icon.svg uses."""
    tokens = _TOKEN.findall(d)
    shapes, points = [], []
    x = y = start_x = start_y = 0.0
    command, i = None, 0

    def number():
        nonlocal i
        value = float(tokens[i])
        i += 1
        return value

    while i < len(tokens):
        if tokens[i].isalpha():
            command = tokens[i]
            i += 1
            if command in "Zz":
                if points:
                    shapes.append(points)
                points, x, y = [], start_x, start_y
                continue
        if command is None:
            raise ValueError(f"The path starts with a number: {d[:20]}")
        relative = command.islower()
        kind = command.upper()
        if kind == "M":
            if points:
                shapes.append(points)
            x, y = (x + number(), y + number()) if relative else (number(), number())
            start_x, start_y, points = x, y, [(x, y)]
            command = "l" if relative else "L"          # further pairs after M are lines
        elif kind == "L":
            x, y = (x + number(), y + number()) if relative else (number(), number())
            points.append((x, y))
        elif kind == "H":
            x = x + number() if relative else number()
            points.append((x, y))
        elif kind == "V":
            y = y + number() if relative else number()
            points.append((x, y))
        elif kind == "C":
            values = [number() for _ in range(6)]
            if relative:
                values = [value + (x if n % 2 == 0 else y) for n, value in enumerate(values)]
            x1, y1, x2, y2, x3, y3 = values
            for step in range(1, CURVE_STEPS + 1):
                t = step / CURVE_STEPS
                u = 1 - t
                points.append((u**3 * x + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3,
                               u**3 * y + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3))
            x, y = x3, y3
        else:
            raise ValueError(f"icon.svg uses a path command this script does not draw: {command}")
    if points:
        shapes.append(points)
    return shapes


def draw(size, svg=SVG):
    """icon.svg as a size x size RGBA picture."""
    root = ET.parse(svg).getroot()
    view = [float(part) for part in root.get("viewBox").split()]
    big = size * SUPERSAMPLE
    scale = big / view[2]
    picture = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    pen = ImageDraw.Draw(picture)
    for element in root:
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "rect":
            x, y, w, h, rx = (float(element.get(name, 0)) * scale for name in ("x", "y", "width", "height", "rx"))
            stroke = float(element.get("stroke-width", 0)) * scale
            # SVG centres a stroke on the edge; Pillow draws its outline inside the box, so the box grows by half of it.
            box = (x - stroke / 2, y - stroke / 2, x + w + stroke / 2, y + h + stroke / 2)
            pen.rounded_rectangle(box, radius=rx + stroke / 2, fill=_colour(element.get("fill")),
                                  outline=_colour(element.get("stroke")) if element.get("stroke") else None, width=round(stroke))
        elif tag == "path":
            for shape in path_points(element.get("d")):
                pen.polygon([(px * scale, py * scale) for px, py in shape], fill=_colour(element.get("fill")))
        else:
            raise ValueError(f"icon.svg has a <{tag}>, which this script does not draw.")
    return picture.resize((size, size), Image.LANCZOS)


def ico_bytes(svg=SVG):
    pictures = [draw(size, svg) for size in SIZES]
    output = io.BytesIO()
    pictures[-1].save(output, format="ICO", sizes=[(size, size) for size in SIZES], append_images=pictures[:-1])
    return output.getvalue()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Draw web/icon.ico from web/icon.svg.")
    parser.add_argument("--check", action="store_true", help="only check that web/icon.ico is up to date")
    args = parser.parse_args(argv)
    data = ico_bytes()
    if args.check:
        if not ICO.is_file() or ICO.read_bytes() != data:
            print("web/icon.ico is not what web/icon.svg draws. Run python scripts/make_icon.py.")
            return 1
        print("web/icon.ico matches web/icon.svg.")
        return 0
    ICO.write_bytes(data)
    print(f"Wrote {ICO.relative_to(ROOT)} ({len(data):,} bytes, sizes {', '.join(map(str, SIZES))}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
