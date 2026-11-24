"""Voice Lab's suite API 1. The Lab owns the models, Cast voices and voice archives."""

import base64
import http.client
import ipaddress
import json
import queue
import threading
from urllib.parse import quote, urlsplit

from .. import fels_suite
from ..comfy import Cancelled, ComfyError, beat

APP = "voice-lab"
API = 1
WAIT_SECONDS = 1800
MAX_REPLY = 64 << 20
START_LAB = "Start Voice Lab to design and use voices in NPC Forge."


class LabError(ComfyError):
    """A Voice Lab refusal or connection error that can be shown in a Forge job."""

    def __init__(self, message, *, code=None, status=None, doing=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.doing = doing


def _endpoint(url):
    """Split a local HTTP origin. Forge sends recordings only to a service on this computer."""
    try:
        target = urlsplit(url)
        host = target.hostname or ""
        local = host == "localhost" or ipaddress.ip_address(host).is_loopback
        port = target.port
    except ValueError:
        local = False
    if not local or target.scheme not in ("http", "https") or target.path not in ("", "/") \
            or target.username is not None or target.query or target.fragment:
        raise LabError("Use a Voice Lab address on this computer.", code="invalid_lab_url")
    return target.scheme, host, port


def _path(value):
    return quote(value, safe="")


def _wav(data):
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise LabError("Voice Lab returned unreadable audio. Try making the line again in Voice Lab.", code="lab_reply")
    return data


def _voice_for(voices, identity):
    voice = next((row for row in voices if row.get("identity_id") == identity), None)
    if voice is None:
        raise LabError("Voice Lab could not find that Cast voice. Check the voice in Voice Lab's Cast library.", code="voice_missing")
    if not voice.get("ready"):
        raise LabError(voice.get("why_not") or "Open Voice Lab to check why this voice is unavailable.", code="voice_not_ready")
    return voice


class LabClient:
    """One verified local Lab. A request never follows redirects or sends a file path."""

    def __init__(self, url, health=None):
        self.url = url.rstrip("/")
        self.scheme, self.host, self.port = _endpoint(self.url)
        self.health = health

    @classmethod
    def discover(cls, given=None):
        def ask(url):
            try:
                return cls(url)._json("GET", "/api/health", timeout=3)
            except LabError:
                return None

        found = fels_suite.find_app(APP, ask, given=given)
        if not found["url"]:
            notes = found["notes"][:-1]
            raise LabError(" ".join([*notes, START_LAB]), code="lab_offline")
        health = found["health"]
        block = health.get("fels") or {}
        if block.get("app") != APP or block.get("contract") != fels_suite.CONTRACT or block.get("api") != API:
            raise LabError("Update Voice Lab to a release with Suite API 1, then try again.", code="lab_api_version")
        return cls(found["url"], health)

    def _http(self, method, path, data, headers, timeout):
        connection_type = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(self.host, self.port, timeout=timeout)
        try:
            connection.request(method, path, data, headers)
            with connection.getresponse() as response:
                payload = response.read(MAX_REPLY + 1)
                status = response.status
        except (OSError, http.client.HTTPException) as error:
            raise LabError("The connection to Voice Lab failed. Check that it is running and try again.", code="lab_connection") from error
        finally:
            connection.close()
        if len(payload) > MAX_REPLY:
            raise LabError("Voice Lab sent more than 64 MiB. Try a shorter line or smaller voice file.", code="lab_reply")
        return status, payload

    def _request(self, method, path, *, body=None, data=None, headers=None, status=200, timeout=12, cancel=None):
        sent = dict(headers or {})
        if body is not None:
            sent["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")

        if cancel is None:
            actual, payload = self._http(method, path, data, sent, timeout)
        else:
            if cancel.is_set():
                raise Cancelled()
            replies = queue.SimpleQueue()

            def run():
                try:
                    replies.put(self._http(method, path, data, sent, timeout))
                except Exception as error:
                    replies.put(error)

            threading.Thread(target=run, name="Voice Lab request", daemon=True).start()
            while True:
                if cancel.is_set():
                    raise Cancelled()
                beat(cancel, "Waiting for Voice Lab. A request already running there can still finish.")
                try:
                    result = replies.get(timeout=0.5)
                    break
                except queue.Empty:
                    pass
            if cancel.is_set():
                raise Cancelled()
            if isinstance(result, Exception):
                raise result
            actual, payload = result

        if actual != status:
            try:
                detail = json.loads(payload)
            except (ValueError, UnicodeError):
                detail = None
            detail = detail if isinstance(detail, dict) else {}
            error = detail.get("error")
            code = detail.get("code")
            if isinstance(error, dict):
                code = error.get("code") or code
                error = error.get("message")
            if actual == 404 and not error:
                error = "Update Voice Lab to the release with Suite API 1 and try again."
            raise LabError(error or f"Voice Lab could not finish the request (HTTP {actual}). Check Voice Lab and try again.",
                           code=code, doing=detail.get("doing"), status=actual)
        return payload

    def _json(self, method, path, **kwargs):
        payload = self._request(method, path, **kwargs)
        try:
            result = json.loads(payload)
            if isinstance(result, dict):
                return result
        except (ValueError, UnicodeError):
            pass
        raise LabError("Voice Lab returned an unreadable answer. Check that both apps are up to date.", code="lab_reply")

    def voices(self):
        return self._json("GET", "/api/voices")["voices"]

    def speak(self, voice, text, *, direction=None, speed=None, cancel=None):
        if direction:
            path = "/api/speak"
            body = {"voice": voice, "text": text, "direction": direction}
        else:
            path = "/v1/audio/speech"
            body = {"voice": voice, "input": text, "response_format": "wav"}
        if speed is not None:
            body["speed"] = speed
        body["wait_seconds"] = WAIT_SECONDS
        return _wav(self._request("POST", path, body=body, timeout=WAIT_SECONDS + 120, cancel=cancel))

    def design(self, name, description, *, text=None, engine=None, clone_engine=None, language=None, seed=None, cancel=None):
        body = {"name": name, "voice_description": description, "wait_seconds": WAIT_SECONDS}
        for key, value in (("text", text), ("engine", engine), ("clone_engine", clone_engine), ("language", language), ("seed", seed)):
            if value is not None:
                body[key] = value
        result = self._json("POST", "/api/characters/from-design", body=body, status=201,
                            timeout=WAIT_SECONDS + 120, cancel=cancel)
        return result["voice"]

    def clone(self, name, engine, recording, *, transcript, origin, allowed_use, consent=False, cancel=None):
        if consent is not True:
            raise LabError("Confirm you have permission to use this recording before sending it to Voice Lab.", code="consent_required")
        if not all(isinstance(value, str) and value.strip() for value in (transcript, origin, allowed_use)):
            raise LabError("Enter the exact words, who made the recording, and how you may use it.", code="reference_details")
        character = self._json("POST", "/api/characters", body={"name": name}, status=201, cancel=cancel)["character"]
        identity = self._json("POST", f"/api/characters/{_path(character['id'])}/identities",
                              body={"engine_id": engine, "label": "Recorded"}, status=201, cancel=cancel)["identity"]["id"]
        path = f"/api/identities/{_path(identity)}"
        fields = {"filename": "recording.wav", "transcript": transcript, "origin": origin, "allowed_use": allowed_use}
        metadata = base64.urlsafe_b64encode(json.dumps(fields, ensure_ascii=False).encode("utf-8")).decode("ascii").rstrip("=")
        reference = self._json("POST", f"{path}/references", data=_wav(recording), status=201, timeout=120, cancel=cancel,
                               headers={"Content-Type": "application/octet-stream", "X-Reference-Metadata": metadata})["reference"]
        self._json("POST", f"{path}/update", body={"status": "approved", "reference_version_id": reference["id"]}, cancel=cancel)
        return _voice_for(self.voices(), identity)

    def voice_file(self, identity, audition, *, permission=False, cancel=None):
        take = {"identity_id": identity, "text": audition, "line_id": "forge-card-audition", "wait_seconds": WAIT_SECONDS}
        _wav(self._request("POST", "/api/performance", body=take, timeout=WAIT_SECONDS + 120, cancel=cancel))
        body = {"kind": "persona", "identity_id": identity, "permission": permission is True}
        archive = self._json("POST", "/api/portable/export", body=body, status=201, timeout=120, cancel=cancel)["archive"]
        return self._request("GET", f"/api/portable/{_path(archive['id'])}.zip", timeout=120, cancel=cancel)

    def unload(self):
        return self._json("POST", "/api/unload", body={}, timeout=15)
