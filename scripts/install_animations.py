"""Install the animation library the rig step copies clips from: Quaternius' Universal Animation Library 1 and 2, the
free Standard packs (CC0 1.0, https://quaternius.com). npcgen/blender/rig_library.py copies their clips onto a rigged model.

    python scripts/install_animations.py                     # download both packs from itch.io (about 35 MB)
    python scripts/install_animations.py --zip A.zip B.zip   # the two Standard zips, downloaded yourself

Only the two files the rig step reads (UAL1_Standard.glb, UAL2_Standard.glb, 16 MB) and the licence are kept, in
animations/quaternius/ (git-ignored). The packs come from Quaternius' itch.io pages the way a browser gets them: the
free "No thanks, just take me to the downloads" route. If itch.io changes and that fails, download
"Universal Animation Library[Standard].zip" and "Universal Animation Library 2[Standard].zip" from the pages below and
pass them with --zip.
"""
import argparse
import hashlib
import io
import json
import pathlib
import re
import struct
import sys
import urllib.parse
import urllib.request
import zipfile
from http.cookiejar import CookieJar

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEST = ROOT / "animations" / "quaternius"
PACKS = [
    {"page": "https://quaternius.itch.io/universal-animation-library",
     "zip": "Universal Animation Library[Standard].zip",
     "glb": "Universal Animation Library[Standard]/Unreal-Godot/UAL1_Standard.glb", "file": "UAL1_Standard.glb",
     "rm": "Universal Animation Library[Standard]/Unreal-Godot/UAL1_Standard_RM.glb",
     "licence": "Universal Animation Library[Standard]/License.txt"},
    {"page": "https://quaternius.itch.io/universal-animation-library-2",
     "zip": "Universal Animation Library 2[Standard].zip",
     "glb": "Universal Animation Library 2[Standard]/Unreal-Godot/UAL2_Standard.glb", "file": "UAL2_Standard.glb",
     "rm": "Universal Animation Library 2[Standard]/Unreal-Godot/UAL2_Standard_RM.glb",
     "licence": "Universal Animation Library 2[Standard]/License.txt"},
]
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NPC-Forge-installer"}
JOINTS = ("pelvis", "spine_01", "Head", "upperarm_l", "hand_r", "thumb_01_l", "thigh_r", "ball_l")


class Itch:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def get(self, url):
        return self.opener.open(urllib.request.Request(url, headers=HEADERS), timeout=120).read()

    def post(self, url, fields, referer):
        head = {**HEADERS, "X-Requested-With": "XMLHttpRequest", "Referer": referer}
        req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), headers=head)
        return json.loads(self.opener.open(req, timeout=120).read())

    @staticmethod
    def csrf(html):
        m = re.search(r'name="csrf_token" value="([^"]+)"', html)
        if not m:
            raise RuntimeError("no csrf token on the page (itch.io may have changed)")
        return m.group(1)

    def download(self, page, zip_name):
        """A free pack's zip, as the page's "No thanks, just take me to the downloads" link gets it."""
        html = self.get(page).decode("utf-8", "replace")
        answer = self.post(page + "/download_url", {"csrf_token": self.csrf(html)}, page)
        if not answer.get("url"):
            raise RuntimeError(f"no download page: {answer}")
        dl = self.get(answer["url"]).decode("utf-8", "replace")
        uploads = re.findall(r'data-upload_id="(\d+)".*?<strong title="([^"]+)"', dl, re.S)
        upload = next((u for u, name in uploads if name == zip_name), None)
        if upload is None:
            raise RuntimeError(f"{zip_name} is not on the download page (found: {[n for _u, n in uploads]})")
        query = urllib.parse.urlencode({"source": "game_download", "after_download_lightbox": "1", "as_props": "1"})
        answer = self.post(f"{page}/file/{upload}?{query}", {"csrf_token": self.csrf(dl)}, answer["url"])
        if not answer.get("url"):
            raise RuntimeError(f"no file link: {answer.get('errors') or answer}")
        return self.get(answer["url"])


def _json_of(glb):
    size = int.from_bytes(glb[12:16], "little")
    if glb[:4] != b"glTF" or glb[16:20] != b"JSON":
        raise RuntimeError("not a GLB file")
    return json.loads(glb[20:20 + size]), 20 + size


def joints_of(glb):
    """The node names of a GLB file (its JSON chunk)."""
    return {n.get("name") for n in _json_of(glb)[0].get("nodes", [])}


def _floats(js, glb, bin_start, index):
    a = js["accessors"][index]
    view = js["bufferViews"][a["bufferView"]]
    n = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[a["type"]]
    start = bin_start + 8 + view.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = view.get("byteStride") or 4 * n
    rows = [struct.unpack_from(f"<{n}f", glb, start + i * stride) for i in (0, a["count"] - 1)]
    return rows[0], rows[1]


def root_motion(glb):
    """How far each clip of a root-motion file (the packs' *_RM.glb) carries the character: {clip: {forward_m, up_m,
    left_m, seconds}} for clips that move it more than 5 cm, measured on the library's 1.83 m mannequin. The in-place files
    the rig step copies leave this out, so a scene that moves a character can match its steps with it."""
    js, bin_start = _json_of(glb)
    nodes = js.get("nodes", [])
    root = next((i for i, n in enumerate(nodes) if n.get("name") == "root"), None)
    moves = {}
    for anim in js.get("animations", []):
        for ch in anim["channels"]:
            if ch["target"].get("node") != root or ch["target"]["path"] != "translation":
                continue
            smp = anim["samplers"][ch["sampler"]]
            (t0,), (t1,) = _floats(js, glb, bin_start, smp["input"])
            p0, p1 = _floats(js, glb, bin_start, smp["output"])
            left, up, forward = (b - a for a, b in zip(p0, p1))      # glTF axes: the character faces +Z, its left is +X
            if (forward ** 2 + up ** 2 + left ** 2) ** 0.5 > 0.05:
                moves[anim["name"]] = {"forward_m": round(forward, 3), "up_m": round(up, 3), "left_m": round(left, 3),
                                       "seconds": round(t1 - t0, 3)}
    return moves


def install(zips):
    DEST.mkdir(parents=True, exist_ok=True)
    manifest = {"name": "Universal Animation Library 1 and 2 (Standard), by Quaternius", "licence": "CC0 1.0",
                "url": "https://quaternius.com/packs/universalanimationlibrary.html", "files": {}, "root_motion": {}}
    itch = Itch()
    for pack, local in zip(PACKS, zips):
        if local:
            data = pathlib.Path(local).read_bytes()
            print(f"{pack['zip']}: from {local}")
        else:
            print(f"{pack['zip']}: downloading from {pack['page']} ...", flush=True)
            data = itch.download(pack["page"], pack["zip"])
        print(f"  {len(data) / 1e6:.1f} MB, sha256 {hashlib.sha256(data).hexdigest()}")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            glb = z.read(pack["glb"])
            licence = z.read(pack["licence"])
            rm = z.read(pack["rm"]) if pack["rm"] in z.namelist() else None
        missing = [j for j in JOINTS if j not in joints_of(glb)]
        if missing:
            raise RuntimeError(f"{pack['file']} does not have the library's rig (no joint {missing[0]})")
        if rm is not None:
            for name, move in root_motion(rm).items():
                manifest["root_motion"].setdefault(name, move)
        (DEST / pack["file"]).write_bytes(glb)
        (DEST / ("License_" + pack["file"].split("_")[0] + ".txt")).write_bytes(licence)
        manifest["files"][pack["file"]] = {"from": pack["page"], "zip": pack["zip"], "bytes": len(glb),
                                           "sha256": hashlib.sha256(glb).hexdigest()}
        print(f"  -> {DEST / pack['file']} ({len(glb) / 1e6:.1f} MB)")
    (DEST / "manifest.json").write_text(json.dumps(manifest, indent=1), "utf-8")
    print("Installed. Rigs now get the library's clips (the Clips choice in the rig card, or --clips on the command line).")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--zip", nargs=2, metavar=("UAL1_ZIP", "UAL2_ZIP"),
                    help="the two Standard zips, downloaded yourself (Library 1 first)")
    a = ap.parse_args()
    try:
        install(a.zip or [None, None])
    except Exception as exc:
        sys.exit(f"Could not install the animation library: {exc}\nDownload the two Standard zips from "
                 f"{PACKS[0]['page']} and {PACKS[1]['page']} and run: python scripts/install_animations.py --zip A.zip B.zip")


if __name__ == "__main__":
    main()
