"""Wires the pieces together: config, ComfyUI client, library and job worker. Shared by the web server and the CLI."""
import pathlib
import threading

from . import config as cfgmod
from .comfy import Comfy, LockBusy, _operation_lock
from .jobs import Jobs
from .library import Library
from .cast_packs import CastPacks
from .dressing import Dressings
from .facescan import FaceScans
from .itemstore import ItemStore
from .creatures import CreatureStore
from .scenes import Scenes
from .voice.voicelibrary import VoiceLibrary
from .scene_render import SceneRenderJobs

_ACTIVE_LIBRARIES = set()
_ACTIVE_LIBRARIES_LOCK = threading.Lock()


class App:
    def __init__(self, cfg=None):
        self.cfg = cfg or cfgmod.load()
        library_path = pathlib.Path(self.cfg["library_path"]).resolve()
        self._library_key = str(library_path).casefold()
        with _ACTIVE_LIBRARIES_LOCK:
            if self._library_key in _ACTIVE_LIBRARIES:
                raise RuntimeError(f"Another NPC Forge app is already using this library: {library_path}")
            _ACTIVE_LIBRARIES.add(self._library_key)
        self._library_lock = _operation_lock(f"library:{library_path}")
        try:
            self._library_lock.acquire(timeout=0)
        except LockBusy:
            with _ACTIVE_LIBRARIES_LOCK:
                _ACTIVE_LIBRARIES.discard(self._library_key)
            raise RuntimeError(f"Another NPC Forge process is already using this library: {library_path}") from None
        try:
            self.comfy = Comfy(self.cfg["comfy_url"], self.cfg["models"])
            self.lib = Library(library_path)
            library_cfg = self.cfg.get("library") or {}
            try:                                                # characters deleted long enough ago leave the trash for good
                self.lib.purge_trash(library_cfg.get("trash_days", 30))
            except (OSError, TypeError, ValueError):
                pass
            try:                                                # how many earlier versions of a redrawn picture are kept
                self.lib.history_keep = max(int(library_cfg.get("picture_history", self.lib.history_keep)), 0)
            except (TypeError, ValueError):
                pass
            try:                                                # and how many earlier 3D models
                self.lib.model_history_keep = max(int(library_cfg.get("model_history", self.lib.model_history_keep)), 0)
            except (TypeError, ValueError):
                pass
            self.voice_library = VoiceLibrary(self.lib)
            self.cast_packs = CastPacks(library_path)
            self.scenes = Scenes(self.lib)
            self.items = ItemStore(self.lib, self.cfg)
            self.creatures = CreatureStore(self.lib)
            self.jobs = Jobs(self.cfg, self.comfy, self.lib, scenes=self.scenes, items=self.items, creature_store=self.creatures)      # starts the single GPU worker thread
            self.faces = FaceScans(self.cfg, self.comfy, self.lib, busy=self.jobs.busy)                 # (a thread of its own, started by the first scan)
            self.dressings = Dressings(self.items, self.lib, cfg=self.cfg)                              # (the same, for clothes tried on a body: started by the first fit)
            self.scene_jobs = SceneRenderJobs(self.scenes)
        except BaseException:
            self._library_lock.release()
            with _ACTIVE_LIBRARIES_LOCK:
                _ACTIVE_LIBRARIES.discard(self._library_key)
            raise
        self.closed = False
        self.drama_studio_lock = threading.Lock()
        self._library_lock_released = False

    def _release_library_lock(self):
        if self._library_lock_released:
            return
        self._library_lock.release()
        self._library_lock_released = True
        with _ACTIVE_LIBRARIES_LOCK:
            _ACTIVE_LIBRARIES.discard(self._library_key)

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                try:
                    if getattr(self,'drama_studio',None):self.drama_studio.close()
                    self.scene_jobs.close()
                finally:
                    try:
                        self.items.close()
                    finally:
                        try:
                            self.faces.close()
                        finally:
                            try:
                                self.dressings.close()
                            finally:
                                self.jobs.close(timeout=None)
            finally:
                if not self.jobs.thread.is_alive():
                    self._release_library_lock()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
