"""The preview picture of an item that is built from a recipe (glasses: npcgen/glasses.py).

The model is ready the moment it is saved (a few hundredths of a second), but a picture of it for the Items list takes a few
seconds of Blender (npcgen/blender/item_poster.py: Cycles on the CPU, so it never touches the GPU the other jobs use). So the
picture is drawn in the background, one at a time, and put where the list looks for it when it is done; saving never waits for it.
Asked again while one is being drawn (the model changed), the item is drawn once more from its newest model.
"""
import pathlib
import tempfile
import threading

from . import licences, media, rig3d
from .comfy import ComfyError

SCRIPT = pathlib.Path(__file__).resolve().parent / "blender" / "item_poster.py"
SIZE = (640, 480)
SAMPLES = 32
PREVIEW_FILE = "model_3d_preview.png"
TIMEOUT_SECONDS = 180


def render(cfg, glb, view="three_quarter", size=SIZE, samples=SAMPLES, timeout=TIMEOUT_SECONDS):
    """PNG bytes: a lit still of a GLB seen from `view` ("three_quarter", "front", "side", "back", "top")."""
    with tempfile.TemporaryDirectory(prefix="npcforge-poster-") as tmp:
        tmp = pathlib.Path(tmp)
        src, out = tmp / "model.glb", tmp / "poster.png"
        src.write_bytes(glb)
        rig3d.run_script(cfg, SCRIPT, [str(src), str(out), "--view", view, "--size", f"{size[0]}x{size[1]}", "--samples", str(samples)],
                         tmp, out, "Drawing the picture", "Blender could not draw the picture", timeout=timeout)
        return out.read_bytes()


class Posters:
    """Draws the preview pictures one at a time on a thread of its own. `draw(cfg, glb) -> PNG bytes` is Blender (`render`), or a stand-in in tests."""

    def __init__(self, store, cfg, draw=None):
        self.store, self.cfg, self.draw = store, cfg, draw or render
        self._wanted = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._stop = False
        self._thread = None

    def request(self, item_id):
        """Draw (or draw again) this item's picture soon."""
        with self._lock:
            if item_id not in self._wanted:
                self._wanted.append(item_id)
            self._idle.clear()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="item-posters", daemon=True)
                self._thread.start()
        self._wake.set()

    def wait_idle(self, timeout=30.0):
        """Block until nothing is waiting or being drawn (for tests and the command line)."""
        return self._idle.wait(timeout)

    def close(self, timeout=5.0):
        self._stop = True
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout)

    def _loop(self):
        while not self._stop:
            with self._lock:
                item_id = self._wanted.pop(0) if self._wanted else None
                if item_id is None:
                    self._idle.set()
            if item_id is None:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            try:
                self._draw_one(item_id)
            except KeyError:
                pass                                         # the item was deleted meanwhile
            except Exception as exc:                         # (one picture failing must not stop the next)
                self._note(item_id, str(exc) or type(exc).__name__)

    def _draw_one(self, item_id):
        item = self.store.get(item_id)
        meta = (item.get("assets") or {}).get("model_3d") or {}
        stamp = meta.get("build") or meta.get("created")        # which model this picture is of
        if not meta.get("file"):
            return
        if meta.get("preview_of") == stamp and meta.get("preview") and self.store.resolve(item_id, meta["preview"]).exists():
            return
        try:
            glb = self.store.read_asset(item_id, meta["file"])
            png = self.draw(self.cfg, glb)
        except ComfyError as exc:
            self._note(item_id, str(exc))
            return
        fresh = {"ok": False}

        def apply(n):                                        # (under the store's lock, and only if the item is still there: a deleted
            model = (n.get("assets") or {}).get("model_3d") or {}     # item's folder is not made again for a late picture)
            if (model.get("build") or model.get("created")) != stamp:      # rebuilt while this was being drawn: it will be asked for again
                return
            self.store.write_asset(item_id, PREVIEW_FILE, media.label(png, licences.shape_models(meta, glb),
                                                                    ai_generated=media.read_label(glb).get("ai-generated", False)))
            model.update(preview=PREVIEW_FILE, preview_of=stamp)
            model.pop("preview_error", None)
            fresh["ok"] = True
        self.store.update(item_id, apply)
        if fresh["ok"]:
            self.store.make_thumb(item_id)

    def _note(self, item_id, text):
        def apply(n):
            model = (n.get("assets") or {}).get("model_3d")
            if model is not None:
                model["preview_error"] = text[:300]
        try:
            self.store.update(item_id, apply)
        except KeyError:
            pass
