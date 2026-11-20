"""NPC Forge's side of the voice engines: starts a worker (worker.py) in the engine's own Python, sends it requests, and keeps
the GPU shared safely with ComfyUI.

MEMORY. There is one GPU. With config "gpu" -> "one_model_at_a_time" on (the default, npcgen/gpu.py), a clip first takes
ComfyUI's operation lock (so it never runs during a picture) and claims the GPU: whatever else is loaded (ComfyUI's models,
another voice engine) is unloaded and the card must read empty before the worker starts. The rules below apply either way:
  - at most one voice worker runs at a time; switching engine stops the previous one (its process ends, so all of its memory
    is returned at once);
  - before a worker starts, a big picture engine still loaded in ComfyUI is released, and the worker only starts when the free
    GPU memory and RAM reach what the engine needs (ComfyUI is asked to unload everything once if they do not);
  - before a big picture engine starts, every voice worker is stopped (engines.add_before_big);
  - a worker left idle for `idle_minutes` (config "voice" -> "idle_minutes", default 3) is stopped. It is not stopped the moment
    the queue empties: the voice designer sends one short job after another, and reloading a model for each would add 10-60 s
    to every "try a line".
A request that is cancelled or runs past its time limit kills the worker: that is the only way to stop a model mid-clip, and
the next request simply starts a fresh one.
"""
import contextlib
import ctypes
import json
import importlib.util
import os
import pathlib
import queue
import subprocess
import sys
import threading
import time

from .. import engines as picture_engines
from .. import gpu
from ..comfy import Cancelled
from . import engines, binding

WORKER = pathlib.Path(__file__).resolve().parent / "worker.py"
ROOT = pathlib.Path(__file__).resolve().parents[2]
START_SECONDS = 180            # importing torch and the engine's packages
LOAD_SECONDS = 900             # loading a model (and capturing its graphs) the first time
MIN_SECONDS = 90               # a clip's time limit: this, plus a share of the text length (see _time_limit)
MEMORY_WAIT_SECONDS = 60
_sleep, _clock = time.sleep, time.monotonic       # tests replace these


class VoiceError(RuntimeError):
    """The voice could not be made; the message is safe to show."""


def free_ram_mb():
    """Free physical RAM in MB, or None when it cannot be read on this system."""
    try:
        if os.name == "nt":
            class MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            stat = MemStatus()
            stat.dwLength = ctypes.sizeof(MemStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return stat.ullAvailPhys // 2 ** 20
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return None


class Worker:
    """One running worker process and its reply pipe."""

    def __init__(self, engine_id, python, env, log_path):
        self.engine_id = engine_id
        self.native_runtime_handle = None
        self.loaded = set()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log = open(log_path, "ab")
        self.log.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} starting {engine_id} worker ====\n".encode())
        self.log.flush()
        full_env = {**os.environ, **env, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        flags = 0x08000000 if os.name == "nt" else 0                    # CREATE_NO_WINDOW: no console pops up on Windows
        try:
            self.proc = subprocess.Popen([python, "-u", str(WORKER), "--engine", engine_id], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=self.log, env=full_env, cwd=str(ROOT),
                                         creationflags=flags)
        except OSError as e:
            self.log.close()
            raise VoiceError(f"Could not start the {engine_id} voice engine ({e}). Check its Python path in config.json.") from None
        self.replies = queue.Queue()
        self.next_id = 1
        try:
            threading.Thread(target=self._read, daemon=True, name=f"voice-{engine_id}-reader").start()
        except BaseException:
            self.kill()
            raise

    def _read(self):
        for raw in self.proc.stdout:
            try:
                self.replies.put(json.loads(raw.decode("utf-8")))
            except ValueError:
                pass
        self.replies.put(None)                                           # the pipe closed: the process ended

    def alive(self):
        return self.proc.poll() is None

    def wait_ready(self, cancel=None):
        reply = self._wait(0, START_SECONDS, cancel, "start")
        if not reply.get("ok"):
            raise VoiceError(f"The {self.engine_id} voice engine could not start: {reply.get('error')}")
        if self.engine_id=='drama3' and os.name=='nt':
            # Windows venv redirectors may launch a second Python. Hold its actual
            # process handle so cancellation kills the owner of the native job,
            # even if the redirector exits first or its PID is later reused.
            from ctypes import wintypes
            pid=reply.get('pid')
            if not isinstance(pid,int) or (pid!=self.proc.pid and reply.get('parent_pid')!=self.proc.pid):
                raise VoiceError('Drama 3 worker did not establish owned process identity')
            self.native_kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            self.native_kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
            self.native_kernel.OpenProcess.restype=wintypes.HANDLE
            self.native_kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
            self.native_kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT]
            self.native_kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            self.native_runtime_handle=self.native_kernel.OpenProcess(0x0001|0x00100000,False,pid)
            if not self.native_runtime_handle:raise VoiceError('Cannot own the Drama 3 worker process')

    def call(self, request, seconds, cancel=None):
        rid = self.next_id
        self.next_id += 1
        try:
            self.proc.stdin.write((json.dumps({**request, "id": rid}) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except OSError:
            raise VoiceError(f"The {self.engine_id} voice engine stopped unexpectedly (see {self.log.name}).") from None
        reply = self._wait(rid, seconds, cancel, request.get("cmd"))
        if not reply.get("ok"):
            raise VoiceError(f"{engines.ENGINES.get(self.engine_id, {}).get('label', self.engine_id)}: {reply.get('error')}")
        return reply

    def _wait(self, rid, seconds, cancel, what):
        deadline = _clock() + seconds
        while True:
            if cancel is not None and cancel.is_set():
                self.kill()
                raise Cancelled()
            try:
                reply = self.replies.get(timeout=0.25)
            except queue.Empty:
                if _clock() > deadline:
                    self.kill()
                    label = engines.ENGINES.get(self.engine_id, {}).get("label", self.engine_id)
                    doing = {"start": "start up", "load": "load its model"}.get(what, "make the clip")
                    raise VoiceError(f"The {label} voice engine took longer than {seconds:.0f} s to {doing}, so it was stopped.") from None
                continue
            if reply is None:
                raise VoiceError(f"The {self.engine_id} voice engine stopped unexpectedly (see {self.log.name}).")
            if reply.get("id") == rid:
                return reply

    def kill(self):
        try:
            if getattr(self,'native_runtime_handle',None):
                if self.native_kernel.WaitForSingleObject(self.native_runtime_handle,0)==0x102:
                    self.native_kernel.TerminateProcess(self.native_runtime_handle,1)
                    self.native_kernel.WaitForSingleObject(self.native_runtime_handle,5000)
                self.native_kernel.CloseHandle(self.native_runtime_handle)
                self.native_runtime_handle=None
            if self.alive():
                self.proc.kill()
            self.proc.wait(timeout=10)
        except Exception:
            pass
        finally:
            try:
                self.log.close()
            except Exception:
                pass

    def close(self):
        """Ask politely (close stdin: the worker's loop ends), then kill it if it does not go within 10 s."""
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            pass
        self.kill()


class VoiceRuntime:
    def __init__(self, cfg, log_dir, comfy=None):
        self.cfg, self.comfy = cfg, comfy
        self.log_dir = pathlib.Path(log_dir)
        self.lock = threading.RLock()
        self.worker = None
        self.last_used = _clock()
        voice_cfg = (cfg or {}).get("voice") or {}
        self.idle_seconds = float(voice_cfg.get("idle_minutes", 3)) * 60
        self.closed = False
        self._closed_event = threading.Event()
        self.lab_ownership = None
        lab = engines.lab_root(cfg)
        if lab and (lab / "local_ownership.py").is_file():
            module_spec = importlib.util.spec_from_file_location("npcforge_lab_ownership", lab / "local_ownership.py")
            module = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(module)
            self.lab_ownership = module.LocalOwnership(lab / "out/runtime-locks/model-residency.lock")
        picture_engines.add_before_big(self.release)
        threading.Thread(target=self._reaper, daemon=True, name="voice-idle").start()

    # ---- state ---------------------------------------------------------------------------------------------------------
    def status(self):
        """{engine id: True} for the loaded engine (at most one)."""
        w = self.worker
        return {w.engine_id: True} if w and w.alive() else {}

    def _reaper(self):
        while not self._closed_event.wait(10):
            self._reap_once()

    def _reap_once(self):
        def expired():
            return self.worker and (not self.worker.alive() or
                (self.idle_seconds > 0 and _clock() - self.last_used > self.idle_seconds))
        if expired() and self.lock.acquire(blocking=False):
            try:
                if expired():
                    self._stop_locked()
            finally:
                self.lock.release()

    def release(self):
        """Stop the running worker, if any (before a big picture engine, when idle, on shutdown). Never raises."""
        with self.lock:
            self._stop_locked()

    def _stop_locked(self):
        if self.worker is not None:
            engine_id = self.worker.engine_id
            try:
                self.worker.close()
            except Exception:
                pass
            if self.worker.alive():
                return  # Retain the ownership lease while its worker is still alive.
            self.worker = None
            gpu.drop(("voice", engine_id))
        if self.lab_ownership:
            self.lab_ownership.release()

    def close(self):
        self._closed_event.set()
        self.closed = True
        picture_engines.remove_before_big(self.release)
        self.release()

    # ---- memory ---------------------------------------------------------------------------------------------------------
    def _prepare_memory(self, spec, cancel):
        need_vram, need_ram = spec["min_free_vram_mb"], spec["min_free_ram_mb"]
        comfy, freed = self.comfy, False
        if comfy is not None:
            try:
                picture_engines.release(comfy)                # a big picture model left loaded would leave no room
            except Exception:
                pass
        deadline = _clock() + MEMORY_WAIT_SECONDS
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            free_vram = None
            if comfy is not None:
                try:
                    _, _, _, free_vram = picture_engines._stats(comfy)
                except Exception:
                    free_vram = None                           # ComfyUI not running: nothing of its is on the GPU
            free_ram = free_ram_mb()
            ok_vram = free_vram is None or free_vram >= need_vram
            ok_ram = free_ram is None or free_ram >= need_ram
            if ok_vram and ok_ram:
                return
            if not ok_vram and not freed:
                picture_engines._free(comfy)                   # ComfyUI keeps Mage-Flow loaded between pictures
                freed = True
            if _clock() > deadline:
                have = []
                if free_vram is not None:
                    have.append(f"{free_vram // 1024} GB of GPU memory free, about {need_vram // 1024} GB needed")
                if free_ram is not None:
                    have.append(f"{free_ram // 1024} GB of RAM free, about {need_ram // 1024} GB needed")
                raise VoiceError(f"Not enough free memory to start {spec['label']} ({'; '.join(have)}). Close other programs "
                                 "that use the GPU (LM Studio, a big picture job) and try again.")
            _sleep(2)

    # ---- requests -------------------------------------------------------------------------------------------------------
    def _worker_for(self, engine_id, cancel, frozen=None):
        spec = engines.spec(engine_id)
        if spec is None:
            raise VoiceError(f"Unknown voice engine: {engine_id}")
        w = self.worker
        if w is not None and w.engine_id == engine_id and w.alive() and getattr(w,'binding_id',None)==(frozen or {}).get('id'):
            return w
        self._stop_locked()
        if self.worker is not None:
            raise VoiceError("Previous voice worker did not exit; its model ownership is retained")
        conf = {'python':frozen['python'],'env':frozen['env']} if frozen else engines.engine_config(self.cfg, engine_id)
        if frozen is None and engine_id != "fake" and not engines.installed(self.cfg, engine_id):
            hint = next(row["install_hint"] for row in engines.choices(self.cfg) if row["id"] == engine_id)
            raise VoiceError(f"{spec['label']} is not installed. {hint}")
        self._prepare_memory(spec, cancel)
        if engine_id != "fake" and self.lab_ownership and not self.lab_ownership.acquire():
            raise VoiceError("Voice Lab owns the model residency lock. Unload its model or wait for idle unload before generating in Forge.")
        python = conf["python"] or sys.executable
        started = None
        try:
            started = Worker(engine_id, python, conf["env"], self.log_dir / f"{engine_id}.log")
            started.wait_ready(cancel)
        except BaseException:
            if started is not None:
                started.kill()
            if started is not None and started.alive():
                self.worker = started
            elif self.lab_ownership:
                self.lab_ownership.release()
            raise
        self.worker = started
        started.binding_id=(frozen or {}).get('id')
        return started

    @staticmethod
    def _time_limit(request):
        if request.get('engine')=='drama3' and request.get('_studio') is True:return 3600
        spec = engines.spec(request.get("engine")) or {}
        return MIN_SECONDS + 0.5 * len(request.get("text") or "") * max(1.0, float(spec.get("rtf") or 1.0))

    def run(self, engine_id, request, out_path, cancel=None):
        """Make one clip: request is {"cmd": "design"|"clone"|"stock", "text", ...}; the WAV is written to out_path.
        Returns the worker's reply ({"seconds", "audio_seconds", "sample_rate"})."""
        part = {"design": "design", "clone": "clone", "stock": "stock"}.get(request.get("cmd"))
        if part is None:
            raise VoiceError(f"Unknown voice request: {request.get('cmd')}")
        request=dict(request)
        frozen=request.pop('_runtime_binding',None)
        if engine_id in binding.PINS:
            try:
                if frozen and frozen.get('unavailable'):raise ValueError(frozen['unavailable'])
                if frozen is None:frozen=binding.capture(self.cfg,engine_id,[part],source='execution_legacy')
                binding.verify(frozen,engine_id,part)
            except (KeyError,OSError,ValueError) as error:raise VoiceError(str(error)) from None
        lock = getattr(self.comfy, "operation_lock", None)
        with lock.hold(cancel) if lock is not None else contextlib.nullcontext():
            gpu.claim(("voice", engine_id), self.release, comfy=self.comfy, cancel=cancel)
            return self._run_locked(engine_id, request, out_path, part, cancel, frozen)

    def _run_locked(self, engine_id, request, out_path, part, cancel, frozen=None):
        with self.lock:
            if self.closed:
                raise VoiceError("NPC Forge is shutting down")
            try:
                w = self._worker_for(engine_id, cancel, frozen)
                self.last_used = _clock()
                if part not in w.loaded:
                    if w.loaded and gpu.GATE.strict and (engines.spec(engine_id) or {}).get("separate_parts"):
                        w.call({"cmd": "unload"}, LOAD_SECONDS, cancel)       # one model at a time, even inside one engine
                        w.loaded.clear()
                    w.call({"cmd": "load", "parts": [part],
                            **({'cpu_only':True} if engine_id=='drama3' and request.get('_studio') else {}),
                            **({'_runtime_binding':frozen} if frozen else {})}, LOAD_SECONDS, cancel)
                    w.loaded.add(part)
                reply = w.call({**request, "out": str(out_path)}, self._time_limit({**request, "engine": engine_id}), cancel)
                if frozen and ((reply.get('model_identity') or {}).get('queue_binding') or {}).get('id')!=frozen['id']:
                    raise VoiceError('The worker did not confirm the queued voice runtime binding')
                self.last_used = _clock()
                return reply
            except BaseException:
                if self.worker is not None and not self.worker.alive():
                    self._stop_locked()
                raise
