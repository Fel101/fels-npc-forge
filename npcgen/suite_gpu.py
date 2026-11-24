"""Coordinate local ComfyUI model residency with Fel's Studio."""

import http.client
import ipaddress
import json
import time
from contextlib import closing
from urllib.parse import urlsplit

from . import fels_suite
from .comfy import Cancelled, beat


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
    """Take the lock and leave it held. Cancel or a failed acquisition leaves no request behind."""
    deadline = time.monotonic() + wait
    next_contact = 0
    unloaded = set()
    waiting_on = None
    outside_checked = False
    requested = False

    def cancelled():
        return cancel is not None and cancel.is_set()

    def contact(first=False):
        nonlocal next_contact, waiting_on, outside_checked, requested
        if cancelled() or (not first and time.monotonic() >= deadline):
            return cancelled()
        if not alone or time.monotonic() < next_contact:
            return False
        next_contact = time.monotonic() + 1
        waiting_on = None
        if lock.alone_wanted() is not None:
            return False
        if not requested:
            requested = lock.want_alone("pictures")
            if not requested:
                return False
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
            elif status == 409 and answer.get("code") == "busy":
                doing = answer.get("doing")
                waiting_on = f"{name} is {doing}." if doing else f"{name} is busy."
                beat(cancel, f"Making room on the GPU. {waiting_on}")
        if not outside_checked and not cancelled():
            outside_checked = True
            fels_suite.unload_outside(timeout=max(0.1, min(3, deadline - time.monotonic())))
        return cancelled()

    if cancelled():
        raise Cancelled()
    try:
        pending = lock.alone_wanted()
        beat(cancel, "Making room on the GPU" if alone and pending is None else
             fels_suite.holder_sentence(pending) if pending else "Waiting for the GPU")
        contact(first=True)
        if not cancelled() and lock.acquire("pictures", alone=alone, timeout=max(0, deadline - time.monotonic()), cancel=contact):
            if cancelled():
                raise Cancelled()
            beat(cancel)
            return
        if cancelled():
            raise Cancelled()
        holder = lock.alone_wanted() or next(iter(lock.holders()), None)
        message = waiting_on or (fels_suite.holder_sentence(holder) if holder else "The GPU is in use.")
        from .gpu import GpuBusy
        raise GpuBusy(f"{message} Wait for the running work to finish and retry the picture.", code="gpu_busy")
    except BaseException:
        lock.release()
        raise
