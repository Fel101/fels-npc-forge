"""The Forge client is checked against ed1a4e1's frozen Voice Lab OpenAPI and an HTTP-only stand-in."""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from npcgen import fels_suite
from npcgen.comfy import Cancelled
from npcgen.voice.lab_client import LabClient, LabError

OPENAPI = json.loads(Path(__file__).with_name("voice_lab_openapi.json").read_text(encoding="utf-8"))["paths"]
WAV = b"RIFF\x04\x00\x00\x00WAVE"
HEALTH = {"lab_id": "test-library", "fels": fels_suite.health_block("voice-lab", "1.0.0", 1)}
CAST = {"name": "Nell Gray", "kind": "cast", "model": "chatterbox-turbo", "identity_id": "b" * 32, "ready": True}
CHARACTER = "a" * 32
REFERENCE = "c" * 32
ARCHIVE = "d" * 32


@pytest.fixture
def lab(monkeypatch, tmp_path):
    calls = []
    handlers = {}
    monkeypatch.setenv("FELS_SUITE_DIR", str(tmp_path / "suite"))
    monkeypatch.delenv("VOICE_LAB_URL", raising=False)

    class Handler(BaseHTTPRequestHandler):
        def serve(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            calls.append((self.command, self.path, self.headers, data))
            status, answer = handlers.get((self.command, self.path), (404, {"error": "unknown route"}))
            payload = answer if isinstance(answer, bytes) else json.dumps(answer).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = serve

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as http:
        worker = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()
        url = f"http://127.0.0.1:{http.server_port}"
        try:
            yield url, handlers, calls
        finally:
            http.shutdown()
            worker.join()


def test_discovery_uses_the_address_file_and_checks_the_suite_api(lab):
    url, handlers, calls = lab
    handlers[("GET", "/api/health")] = 200, HEALTH
    fels_suite.write_address("voice-lab", url, "1.0.0", 1, HEALTH["lab_id"])
    assert LabClient.discover().url == url
    assert len(calls) == 1

    handlers[("GET", "/api/health")] = 200, {"fels": {**HEALTH["fels"], "api": 2}}
    with pytest.raises(LabError, match="Update Voice Lab"):
        LabClient.discover()


def test_the_client_uses_only_documented_calls(lab):
    url, handlers, calls = lab
    routes = [
        ("GET", "/api/health", "/api/health", 200, HEALTH),
        ("GET", "/api/voices", "/api/voices", 200, {"voices": [CAST]}),
        ("POST", "/v1/audio/speech", "/v1/audio/speech", 200, WAV),
        ("POST", "/api/speak", "/api/speak", 200, WAV),
        ("POST", "/api/characters/from-design", "/api/characters/from-design", 201, {"voice": CAST}),
        ("POST", "/api/characters", "/api/characters", 201, {"character": {"id": CHARACTER}}),
        ("POST", f"/api/characters/{CHARACTER}/identities", "/api/characters/{character_id}/identities", 201,
         {"identity": {"id": CAST["identity_id"]}}),
        ("POST", f"/api/identities/{CAST['identity_id']}/references", "/api/identities/{identity_id}/references", 201,
         {"reference": {"id": REFERENCE}}),
        ("POST", f"/api/identities/{CAST['identity_id']}/update", "/api/identities/{identity_id}/update", 200, {}),
        ("GET", "/api/voices", "/api/voices", 200, {"voices": [CAST]}),
        ("POST", "/api/performance", "/api/performance", 200, WAV),
        ("POST", "/api/portable/export", "/api/portable/export", 201, {"archive": {"id": ARCHIVE}}),
        ("GET", f"/api/portable/{ARCHIVE}.zip", "/api/portable/{delivery_id}.zip", 200, b"PK\x03\x04"),
        ("POST", "/api/unload", "/api/unload", 200, {"ok": True}),
    ]
    for method, path, _, status, answer in routes:
        handlers[(method, path)] = status, answer

    client = LabClient.discover(url)
    assert client.voices() == [CAST]
    assert client.speak("Nell Gray", "Good morning.") == WAV
    assert client.speak("Nell Gray", "Good evening.", direction="Quietly.", speed=0.9) == WAV
    assert client.design("Nell Gray", "Softly spoken", text="A sample.", language="English", seed=8) == CAST
    assert client.clone("Nell Gray", CAST["model"], WAV, transcript="A sample.", origin="Synthesized by the test.",
                        allowed_use="This test", consent=True) == CAST
    assert client.voice_file(CAST["identity_id"], "An audition.", permission=True) == b"PK\x03\x04"
    assert client.unload() == {"ok": True}

    assert [(method, path) for method, path, _, _ in calls] == [(method, path) for method, path, *_ in routes]
    for (_, _, headers, data), (method, _, template, status, _) in zip(calls, routes):
        operation = OPENAPI[template][method.lower()]
        assert operation["x-fels-suite-api"] == 1 and str(status) in operation["responses"]
        if data:
            schema = operation["requestBody"]["content"][headers["Content-Type"]]["schema"]
            if headers["Content-Type"] == "application/json":
                keys = json.loads(data).keys()
                assert set(schema.get("required", ())) <= keys <= schema["properties"].keys()
            else:
                header = next(param for param in operation["parameters"] if param["name"] == "X-Reference-Metadata")
                metadata = json.loads(base64.urlsafe_b64decode(headers["X-Reference-Metadata"] + "=="))
                fields = header["schema"]["contentSchema"]
                assert set(fields["required"]) <= metadata.keys() <= fields["properties"].keys()
                assert metadata == {"filename": "recording.wav", "transcript": "A sample.",
                                    "origin": "Synthesized by the test.", "allowed_use": "This test"}
                assert data == WAV
    assert json.loads(calls[3][3])["direction"] == "Quietly."
    assert json.loads(calls[11][3])["permission"] is True


def test_refusal_consent_and_cancellation(lab):
    url, handlers, calls = lab
    client = LabClient(url)
    with pytest.raises(LabError, match="permission"):
        client.clone("Nell Gray", CAST["model"], WAV, transcript="A sample.", origin="Test", allowed_use="Test")
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(Cancelled):
        client.speak("Nell Gray", "Good morning", cancel=cancelled)
    assert not calls

    handlers[("POST", "/api/unload")] = 409, {"code": "busy", "doing": "speaking a scene", "error": "Busy"}
    with pytest.raises(LabError) as refusal:
        client.unload()
    assert (refusal.value.status, refusal.value.code, refusal.value.doing) == (409, "busy", "speaking a scene")


@pytest.mark.parametrize("url", ["https://example.com", "http://127.0.0.1@other.test", "file:///D:/voice-lab", "http://localhost/api"])
def test_recording_destinations_are_local_http_origins(url):
    with pytest.raises(LabError, match="on this computer"):
        LabClient(url)
