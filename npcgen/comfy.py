"""Minimal ComfyUI HTTP client plus the Mage-Flow graph builders (stdlib only)."""
import errno
import hashlib
import json
import os
import pathlib
import socket
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_MODELS = {
    "unet_t2i": "mage_flow_int8_convrot.safetensors",
    "unet_edit": "mage_flow_edit_int8_convrot.safetensors",
    "clip": "qwen3vl_4b_bf16.safetensors",
    "vae": "mage_flow_vae_bf16.safetensors",
}


class ComfyError(RuntimeError):
    code = None         # a machine-readable reason where one is known (comfy_down, comfy_busy...: npcgen/jobs.py failure_code)


class ComfyUnreachable(ComfyError):
    """ComfyUI did not answer. `busy` is true when the connection was made and then no answer came in time (it is alive, and most likely in the middle of heavy work), false when
    nothing is listening (it is closed, or starting)."""

    def __init__(self, message, busy=False):
        super().__init__(message)
        self.busy = busy
        self.code = "comfy_busy" if busy else "comfy_down"


class Cancelled(Exception):
    pass


class LockBusy(ComfyError):
    code = "comfy_busy"


# How long a job waits for another user of the same ComfyUI (another job, another NPC Forge, a test run) to finish before
# the step fails with a message naming it. A picture takes under a minute and the longest ComfyUI graph about 20 minutes, so
# half an hour is a wait that something has gone wrong with; waiting for ever just hid it (a step "running" for two hours).
LOCK_WAIT_SECONDS = 30 * 60
_DEFAULT_WAIT = object()

# How long the polls of a graph that is running may go unanswered before the wait gives up. ComfyUI in the middle of heavy work (a mesh made on the GPU, a model read from disk) can stop
# answering HTTP for minutes while the graph goes on and finishes: giving up at once lost the finished model (it stayed in ComfyUI's output folder, never seen by the job). A refused connection
# (nothing is listening) is given far less: that is ComfyUI closed or starting, and its graph is gone either way.
BUSY_SILENCE_SECONDS, DOWN_SILENCE_SECONDS = 15 * 60, 30


def beat(cancel, note=None):
    """Tell whoever owns this cancel flag that the wait using it is still alive, and what it waits for (a job's flag shows
    it on the running step; any other Event has no `beat` and is left alone)."""
    signal = getattr(cancel, "beat", None)
    if signal is not None:
        signal(note)


def tell(cancel, note):
    """Leave a sentence on the running step for good, where a beat's note goes away again (a job's flag adds it to the
    step's note; any other Event has no `tell` and is left alone)."""
    signal = getattr(cancel, "tell", None)
    if signal is not None:
        signal(note)


class _EndpointOperationLock:
    """A re-entrant lock shared by all clients targeting one ComfyUI endpoint, including other processes."""

    def __init__(self, endpoint):
        self.endpoint = endpoint
        digest = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
        self.path = pathlib.Path(tempfile.gettempdir()) / f"npcforge-comfy-{digest}.lock"
        self.holder_path = self.path.with_suffix(".holder")      # who holds it, for the message of a wait that ran out
        self._threads = threading.RLock()
        self._owner = None
        self._depth = 0
        self._local = threading.local()

    def _note_holder(self):
        try:
            who = f"{threading.current_thread().name} in {pathlib.Path(sys.argv[0] or 'python').name}"
            self.holder_path.write_text(json.dumps({"pid": os.getpid(), "who": who, "since": time.strftime("%H:%M:%S")}), "utf-8")
        except OSError:
            pass

    def _forget_holder(self):
        try:
            self.holder_path.unlink()
        except OSError:
            pass

    def _busy_message(self):
        """'... (held by process 123, npc-worker in server.py, since 18:12:04)' when the holder left a note."""
        try:
            note = json.loads(self.holder_path.read_text("utf-8"))
            held = f" It is held by process {note['pid']} ({note['who']}) since {note['since']}."
        except (OSError, ValueError, KeyError, TypeError):
            held = ""
        return (f"Timed out waiting for ComfyUI at {self.endpoint} to become free: another job, another NPC Forge or another "
                f"program is still using it.{held}")

    def _open_and_lock(self, cancel, timeout=None):
        handle = self.path.open("a+b")
        deadline = None if timeout is None else time.monotonic() + max(0, timeout)
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return handle
                except OSError as exc:
                    busy = isinstance(exc, BlockingIOError) or exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK)
                    busy = busy or getattr(exc, "winerror", None) in (33, 36)
                    if not busy:
                        raise
                    if deadline is not None and time.monotonic() >= deadline:
                        raise LockBusy(self._busy_message()) from None
                    beat(cancel, "waiting for ComfyUI: another program is using it")
                    time.sleep(0.1)
        except BaseException:
            handle.close()
            raise

    @staticmethod
    def _unlock_and_close(handle):
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()

    def acquire(self, cancel=None, timeout=None):
        # RLock.acquire() can block indefinitely. Poll it so a job cancelled while another local thread owns the
        # endpoint can leave promptly without ever calling ComfyUI's process-wide interrupt endpoint.
        deadline = None if timeout is None else time.monotonic() + max(0, timeout)
        while True:
            if timeout == 0:
                acquired = self._threads.acquire(blocking=False)
            else:
                wait = 0.1 if deadline is None else max(0, min(0.1, deadline - time.monotonic()))
                acquired = self._threads.acquire(timeout=wait)
            if acquired:
                break
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if deadline is not None and time.monotonic() >= deadline:
                raise LockBusy(self._busy_message())
            beat(cancel, "waiting for ComfyUI: another job is using it")
        ident = threading.get_ident()
        outermost = self._owner != ident
        try:
            if outermost:
                remaining = None if deadline is None else max(0, deadline - time.monotonic())
                handle = self._open_and_lock(cancel, remaining)
                self._owner, self._depth = ident, 1
                self._local.handles = [handle]
                self._note_holder()
                beat(cancel, None)
            else:
                self._depth += 1
                self._local.handles.append(None)
        except BaseException:
            self._threads.release()
            raise
        return True

    def release(self):
        handles = getattr(self._local, "handles", None)
        if not handles:
            raise RuntimeError("Cannot release an unlocked ComfyUI operation lock")
        handle = handles.pop()
        self._depth -= 1
        try:
            if handle is not None:
                self._owner = None
                self._forget_holder()
                try:
                    self._unlock_and_close(handle)
                finally:
                    del self._local.handles
        finally:
            self._threads.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, tb):
        self.release()

    @contextmanager
    def hold(self, cancel=None, timeout=_DEFAULT_WAIT):
        """Hold the lock for a block. Waits LOCK_WAIT_SECONDS for another user at most (`timeout=None` waits for ever)."""
        self.acquire(cancel, LOCK_WAIT_SECONDS if timeout is _DEFAULT_WAIT else timeout)
        try:
            yield self
        finally:
            self.release()


_OPERATION_LOCKS = {}
_OPERATION_LOCKS_GUARD = threading.Lock()


def _endpoint_key(base):
    """Normalize host spelling and trailing slashes without conflating distinct reverse-proxy paths."""
    parsed = urllib.parse.urlsplit(base)
    if not parsed.hostname:
        return base.rstrip("/")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port
    if port and not ((parsed.scheme.lower() == "http" and port == 80) or
                     (parsed.scheme.lower() == "https" and port == 443)):
        host += f":{port}"
    return urllib.parse.urlunsplit((parsed.scheme.lower(), host, parsed.path.rstrip("/"), "", ""))


def _operation_lock(endpoint):
    with _OPERATION_LOCKS_GUARD:
        return _OPERATION_LOCKS.setdefault(endpoint, _EndpointOperationLock(endpoint))


class Comfy:
    def __init__(self, base_url="http://127.0.0.1:8188", models=None):
        self.base = base_url.rstrip("/")
        self.client_id = "npc-generator-" + uuid.uuid4().hex
        self.models = {**DEFAULT_MODELS, **(models or {})}
        # ComfyUI's queue and interrupt endpoint are shared by every client. The endpoint lock serializes full runs
        # both within this process and across other NPC Forge processes pointed at the same ComfyUI server.
        self.operation_lock = _operation_lock(_endpoint_key(self.base))
        self._claiming = threading.local()       # the cancel flag of the run this thread is making, for the GPU hand-off in submit()

    # ---- low level -------------------------------------------------------
    def _request(self, method, path, data=None, headers=None, timeout=30):
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise ComfyError(_format_http_error(method, path, e.code, body)) from None
        except urllib.error.URLError as e:
            raise ComfyUnreachable(f"Cannot reach ComfyUI at {self.base}. Is ComfyUI running? ({e.reason})", busy=isinstance(e.reason, (TimeoutError, socket.timeout))) from None
        except TimeoutError:
            raise ComfyUnreachable(f"Timed out talking to ComfyUI ({method} {path})", busy=True) from None

    def get_json(self, path, timeout=30):
        return json.loads(self._request("GET", path, timeout=timeout))

    # ---- status ----------------------------------------------------------
    def health(self):
        """Return {online, version, gpu, missing:[...]} without raising."""
        try:
            stats = self.get_json("/system_stats", timeout=5)
        except ComfyError as e:
            return {"online": False, "error": str(e), "missing": []}
        missing = []
        for folder, key in (("diffusion_models", "unet_t2i"), ("diffusion_models", "unet_edit"),
                            ("text_encoders", "clip"), ("vae", "vae")):
            try:
                have = self.get_json(f"/models/{folder}", timeout=10)
            except ComfyError:
                have = []
            if self.models[key] not in have:
                missing.append(f"{folder}/{self.models[key]}")
        devices = stats.get("devices") or [{}]
        return {
            "online": True,
            "version": stats.get("system", {}).get("comfyui_version"),
            "gpu": devices[0].get("name"),
            "missing": missing,
        }

    # ---- images ----------------------------------------------------------
    def upload_image(self, png_bytes, filename, image_type="temp", subfolder=None):
        """Upload into ComfyUI (default: its temp dir). Returns the LoadImage value."""
        subfolder = subfolder or f"npc_generator/{self.client_id}"
        boundary = "----npcgen" + uuid.uuid4().hex
        parts = []

        def field(name, value):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())

        field("type", image_type)
        field("subfolder", subfolder)
        field("overwrite", "true")
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\n'
                      f'Content-Type: image/png\r\n\r\n').encode())
        parts.append(png_bytes)
        parts.append(f"\r\n--{boundary}--\r\n".encode())
        raw = self._request("POST", "/upload/image", data=b"".join(parts),
                            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=60)
        info = json.loads(raw)
        name = info["name"]
        if info.get("subfolder"):
            name = f'{info["subfolder"]}/{name}'
        return f"{name} [{image_type}]"

    def fetch_image(self, filename, subfolder="", image_type="temp"):
        return self.fetch_file(filename, subfolder, image_type)

    def fetch_file(self, filename, subfolder="", image_type="temp", byte_range=None):
        """Fetch an output file from ComfyUI (PNG, GLB, or another saved asset). `byte_range` = (first, last) asks for just
        that part of a big file; a server that ignores the request sends the whole file instead."""
        q = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": image_type})
        headers = {"Range": f"bytes={byte_range[0]}-{byte_range[1]}"} if byte_range else None
        return self._request("GET", "/view?" + q, headers=headers, timeout=60)

    def list_files(self, folder):
        """The names of the files in one of ComfyUI's own folders ("output", "input" or "temp"), newest first, in the form its
        nodes take (`name.glb [output]`). Needs a ComfyUI recent enough to have the listing; [] when it has not."""
        try:
            names = self.get_json(f"/internal/files/{folder}", timeout=15)
        except (ComfyError, ValueError):
            return []
        return [n for n in names if isinstance(n, str)] if isinstance(names, list) else []

    # ---- running graphs --------------------------------------------------
    def prepare_picture(self, graph, cancel=None):
        from .gpu import GATE
        GATE.claim_picture(self, graph, cancel)

    def submit(self, graph):
        self.prepare_picture(graph, getattr(self._claiming, "cancel", None))
        body = json.dumps({"prompt": graph, "client_id": self.client_id}).encode()
        raw = self._request("POST", "/prompt", data=body, headers={"Content-Type": "application/json"})
        return json.loads(raw)["prompt_id"]

    def interrupt(self):
        try:
            self._request("POST", "/interrupt", data=b"{}", headers={"Content-Type": "application/json"}, timeout=10)
        except ComfyError:
            pass

    def free_memory(self):
        """Ask ComfyUI to unload every model and free memory (it does so as soon as it is idle)."""
        body = json.dumps({"unload_models": True, "free_memory": True}).encode()
        self._request("POST", "/free", data=body, headers={"Content-Type": "application/json"}, timeout=10)

    def _is_running(self, prompt_id):
        try:
            q = self.get_json("/queue", timeout=10)
        except ComfyError:
            return False
        return any(item[1] == prompt_id for item in q.get("queue_running", []))

    def _dequeue(self, prompt_id):
        try:
            body = json.dumps({"delete": [prompt_id]}).encode()
            self._request("POST", "/queue", data=body, headers={"Content-Type": "application/json"}, timeout=10)
        except ComfyError:
            pass

    def wait(self, prompt_id, cancel=None, timeout=900, poll=1.0):
        """Block until the prompt finishes; returns its history entry. A poll that gets no answer is tried again (BUSY_SILENCE_SECONDS, DOWN_SILENCE_SECONDS), and the job is told
        what is being waited for meanwhile; Cancel works throughout."""
        deadline = time.time() + timeout
        silent = None                                             # since when ComfyUI has not answered the polls, if it has not
        while time.time() < deadline:
            if cancel is not None and cancel.is_set():
                if self._is_running(prompt_id):
                    self.interrupt()
                else:
                    self._dequeue(prompt_id)
                raise Cancelled()
            beat(cancel)
            try:
                hist = self.get_json(f"/history/{prompt_id}", timeout=15)
            except ComfyUnreachable as exc:
                silent = silent or time.time()
                if time.time() - silent > (BUSY_SILENCE_SECONDS if exc.busy else DOWN_SILENCE_SECONDS):
                    raise
                beat(cancel, "ComfyUI is busy and not answering: still waiting for its graph" if exc.busy else "ComfyUI is not answering: waiting for it")
                time.sleep(poll)
                continue
            silent = None
            entry = hist.get(prompt_id)
            if entry and entry.get("status", {}).get("completed") is not None:
                status = entry["status"]
                if status.get("status_str") != "success":
                    raise ComfyError(_format_exec_error(status))
                return entry
            time.sleep(poll)
        raise ComfyError(f"ComfyUI job {prompt_id} did not finish within {timeout}s")

    def run_entry(self, graph, cancel=None, timeout=900, unload_models=False):
        """Submit a graph and return its ComfyUI history entry after it finishes.

        `unload_models` frees VRAM before and after a memory-heavy graph, while keeping the endpoint lock for the whole run.
        """
        with self.operation_lock.hold(cancel):
            self._claiming.cancel = cancel
            if unload_models:
                self.prepare_picture(graph, cancel)
                try:
                    self.free_memory()
                except ComfyError:
                    pass
            try:
                return self.wait(self.submit(graph), cancel=cancel, timeout=timeout)
            finally:
                self._claiming.cancel = None
                if unload_models:
                    from . import gpu
                    try:
                        self.free_memory()
                    except ComfyError:
                        pass
                    else:
                        gpu.release_comfy(self)

    def run(self, graph, cancel=None, timeout=900):
        """Submit a graph, wait, and return the first output image as PNG bytes."""
        with self.operation_lock.hold(cancel):
            self._claiming.cancel = cancel
            try:
                entry = self.wait(self.submit(graph), cancel=cancel, timeout=timeout)
            finally:
                self._claiming.cancel = None
            for node_out in entry.get("outputs", {}).values():
                for img in node_out.get("images", []):
                    return self.fetch_image(img["filename"], img.get("subfolder", ""), img.get("type", "temp"))
            raise ComfyError("ComfyUI finished but produced no image")


def _format_http_error(method, path, code, body):
    try:
        data = json.loads(body)
    except ValueError:
        return f"{method} {path} -> HTTP {code}: {body[:500]}"
    msg = (data.get("error") or {}).get("message") or str(data.get("error"))
    details = []
    for node_id, err in (data.get("node_errors") or {}).items():
        for e in err.get("errors", []):
            details.append(f'node {node_id} ({err.get("class_type")}): {e.get("message")} {e.get("details", "")}'.strip())
    return f"ComfyUI rejected the workflow: {msg}" + ("".join("\n  - " + d for d in details))


def _format_exec_error(status):
    for kind, payload in status.get("messages", []):
        if kind == "execution_error":
            return (f'ComfyUI failed in node {payload.get("node_id")} ({payload.get("node_type")}): '
                    f'{payload.get("exception_message", "").strip()}')
    return "ComfyUI reported a failed run"


# ---- graph builders -------------------------------------------------------
def _loaders(models, unet_key):
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": models[unet_key], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": models["clip"], "type": "mage", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}},
    }


def _sample_and_decode(seed, steps, cfg):
    return {
        "5": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["4", 0], "negative": ["4", 1], "latent_image": ["4", 2],
            "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["3", 0]}},
        "7": {"class_type": "PreviewImage", "inputs": {"images": ["6", 0]}},
    }


def t2i_graph(models, prompt, negative, width, height, seed, steps=30, cfg=5.0):
    """Text-to-image, mirroring the user's image_mage_flow_t2i_int8 workflow."""
    g = _loaders(models, "unet_t2i")
    g["4"] = {"class_type": "TextEncodeMageFlowEdit", "inputs": {
        "clip": ["2", 0], "vae": ["3", 0], "prompt": prompt, "negative_prompt": negative,
        "width": int(width), "height": int(height), "batch_size": 1}}
    g.update(_sample_and_decode(seed, steps, cfg))
    return g


def edit_graph(models, image, prompt, seed, steps=30, cfg=5.0, megapixels=1.0, size=None, negative=""):
    """Instruction edit of one or more reference images, mirroring the image_mage_flow_edit_int8 workflow.

    `image` is a ComfyUI image name, or a list of them for multi-reference editing (Mage-Flow-Edit takes up to 16;
    1-3 work best). All references are resized to the output size by the encoder, so pass references that already
    share the output's aspect ratio. size=None keeps the first (rescaled) reference's size; size=(w, h) forces it.
    """
    refs = [image] if isinstance(image, str) else list(image)
    g = _loaders(models, "unet_edit")
    encoder_inputs = {}
    for j, name in enumerate(refs):
        load_id, scale_id = str(20 + 2 * j), str(21 + 2 * j)
        g[load_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
        g[scale_id] = {"class_type": "ImageScaleToTotalPixels", "inputs": {
            "image": [load_id, 0], "upscale_method": "lanczos", "megapixels": float(megapixels), "resolution_steps": 16}}
        encoder_inputs[f"images.image_{j + 1}"] = [scale_id, 0]
    if size is None:
        g["10"] = {"class_type": "GetImageSize", "inputs": {"image": ["21", 0]}}
        width, height = ["10", 0], ["10", 1]
    else:
        width, height = int(size[0]), int(size[1])
    g["4"] = {"class_type": "TextEncodeMageFlowEdit", "inputs": {
        "clip": ["2", 0], "vae": ["3", 0], **encoder_inputs, "prompt": prompt,
        "negative_prompt": negative, "width": width, "height": height, "batch_size": 1}}
    g.update(_sample_and_decode(seed, steps, cfg))
    return g
