"""Coordinate local ComfyUI model residency with Fel's Studio."""

import http.client
import ipaddress
import json
import threading
import time
from contextlib import closing
from urllib.parse import urlsplit

from . import fels_suite
from .comfy import Cancelled, beat, tell

ASK_EVERY = 3               # seconds between asking a busy Voice Lab or Altera again
OUTSIDE_SECONDS = 10        # how long LM Studio's `lms unload --all` may take
_TOLD = set()               # what this session has already said on a job line (see _tell_once)
_TOLD_LOCK = threading.Lock()


def local_origin(url):
    try:
        origin = urlsplit(url)
        host = origin.hostname
        if host == "localhost" or ipaddress.ip_address(host).is_loopback:
            if origin.scheme in ("http", "https") and origin.username is None:
                return origin
    except (TypeError, ValueError):
        pass
    return None


def unload_holder(holder):
    """Find the current local server for a holder. Holder files never supply an address."""
    address = fels_suite.read_address(holder["app"])
    if not address or address["pid"] != holder["pid"]:
        return None, {}
    origin = local_origin(address["url"])
    if origin is None or origin.path not in ("", "/") or origin.query or origin.fragment:
        return None, {}
    connection_type = http.client.HTTPSConnection if origin.scheme == "https" else http.client.HTTPConnection
    try:
        with closing(connection_type(origin.hostname, origin.port, timeout=3)) as connection:
            connection.request("POST", "/api/unload", b"{}", {"Content-Type": "application/json"})
            with connection.getresponse() as response:
                payload = response.read(16384)
                answer = json.loads(payload) if payload else {}
                return response.status, answer if isinstance(answer, dict) else {}
    except (OSError, ValueError, http.client.HTTPException):
        return None, {}


def can_ask_voice_lab():
    return any(row["app"] == "voice-lab" for row in fels_suite.gpu_holders())


def ask_voice_lab():
    holders = [row for row in fels_suite.gpu_holders() if row["app"] == "voice-lab"]
    if not holders:
        return 200, {"message": "Voice Lab has released the GPU."}
    for holder in holders:
        status, answer = unload_holder(holder)
        if status is not None:
            break
    if status == 409 and answer.get("code") == "busy":
        return 409, {"message": f"Voice Lab is {answer.get('doing') or 'busy'}. Try again when it finishes."}
    if status != 200:
        return 503, {"message": "Open Voice Lab and unload its model there."}
    return 200, {"message": "Voice Lab has released the GPU."}


def acquire_pictures(lock, alone, wait, cancel):
    """Take the lock and leave it held. Cancel or a failed acquisition leaves no request behind.

    With `alone` (One model at a time on), contract 8's steps: say so in alone.json, ask Voice Lab and Altera to unload
    (again every ASK_EVERY seconds while one says it is busy, showing what it is doing), unload LM Studio's and Ollama's
    models, then take the lock alone. A busy app keeps the wait going for as long as it says so; with nothing to wait for,
    the wait ends after `wait` seconds. Cancel works throughout."""
    deadline = time.monotonic() + wait
    next_contact = 0
    unloaded = set()
    waiting_on = None
    outside_checked = False
    requested = False

    def cancelled():
        return cancel is not None and cancel.is_set()

    def over():
        return cancelled() or time.monotonic() >= deadline

    def contact():
        nonlocal next_contact, waiting_on, outside_checked, requested, deadline
        if cancelled():
            return True
        if not alone or time.monotonic() < next_contact:
            return over()                       # (a due question is asked even past the deadline: a busy answer extends it)
        next_contact = time.monotonic() + ASK_EVERY
        waiting_on = None
        pending = lock.alone_wanted()
        if pending is not None and not requested:
            beat(cancel, fels_suite.holder_sentence(pending))
            return over()
        if not requested:
            requested = lock.want_alone("pictures")
            if not requested:
                return over()
        freed = []
        for holder in lock.holders():
            if cancelled():
                return True
            key = holder["app"], holder["pid"]
            if key in unloaded or holder["app"] not in ("voice-lab", "altera"):
                continue
            status, answer = unload_holder(holder)
            name = fels_suite.short_name(holder["app"])
            if status == 200:
                unloaded.add(key)
                freed.append(name)
            elif status == 409 and answer.get("code") == "busy":
                doing = answer.get("doing")
                waiting_on = f"Waiting for {name}: {doing}" if doing else f"Waiting for {name} to finish"
                deadline = max(deadline, time.monotonic() + wait)       # it is working: wait while it says so
        if not outside_checked and not cancelled():
            outside_checked = True
            for entry in fels_suite.unload_outside(timeout=OUTSIDE_SECONDS):
                if entry["done"]:
                    freed.append(entry["program"])
                elif entry["problem"]:
                    _tell_once(cancel, "outside:" + entry["program"], entry["problem"])
        if freed:
            said = unloaded_sentence(freed)
            if not _tell_once(cancel, "unloaded", said):
                beat(cancel, said)
        beat(cancel, waiting_on or "Making room on the GPU")
        return over()

    if cancelled():
        raise Cancelled()
    try:
        pending = lock.alone_wanted()
        beat(cancel, "Making room on the GPU" if alone and pending is None else
             fels_suite.holder_sentence(pending) if pending else "Waiting for the GPU")
        contact()
        if not cancelled() and lock.acquire("pictures", alone=alone, timeout=None, cancel=contact):
            if cancelled():
                raise Cancelled()
            beat(cancel)
            return
        if cancelled():
            raise Cancelled()
        holder = lock.alone_wanted() or next(iter(lock.holders()), None)
        message = (waiting_on + ".") if waiting_on else (fels_suite.holder_sentence(holder) if holder else "The GPU is in use.")
        from .gpu import GpuBusy
        raise GpuBusy(f"{message} Wait for the running work to finish and retry the picture.", code="gpu_busy")
    except BaseException:
        lock.release()
        raise


def unloaded_sentence(names):
    """What the job line says after making room: "Unloaded LM Studio's model to make room (...)"."""
    names = list(dict.fromkeys(names))
    owners = names[0] + "'s" if len(names) == 1 else ", ".join(name + "'s" for name in names[:-1]) + " and " + names[-1] + "'s"
    models = "model" if len(names) == 1 else "models"
    return f"Unloaded {owners} {models} to make room (One model at a time; the switch is on the Models card)."


def _tell_once(cancel, key, text):
    """Leave `text` on the job's step the first time this session needs it. True when it was said now."""
    with _TOLD_LOCK:
        if key in _TOLD:
            return False
        _TOLD.add(key)
    tell(cancel, text)
    return True
