"""Native work must leave the parent responsive, and stop on cancel or timeout."""
import concurrent.futures
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
import threading
import time
import urllib.request

import numpy as np
import pytest

from npcgen import atlasprocess
from npcgen.comfy import Cancelled


@pytest.fixture
def native_block(tmp_path, monkeypatch):
    script = tmp_path / "native_block.py"
    ready = tmp_path / "ready"
    script.write_text(
        "import ctypes, os, sys\nfrom pathlib import Path\n"
        "Path(sys.argv[1]).write_text('ready')\n"
        "if sys.argv[2] == 'fail':\n"
        "    print('native layout rejection', flush=True)\n    raise SystemExit(7)\n"
        "if os.name == 'nt':\n"
        "    ctypes.PyDLL('kernel32.dll').Sleep(15000)\n"
        "else:\n    ctypes.PyDLL(None).usleep(15000000)\n"
    )
    processes = []
    real = atlasprocess.subprocess.Popen

    def launch(command, **kwargs):
        if "--worker" not in command:                        # (taskkill, when the worker is stopped on Windows)
            return real(command, **kwargs)
        process = real([sys.executable, "-B", str(script), str(ready), mode[0]], **kwargs)
        processes.append(process)
        return process

    mode = ["block"]
    monkeypatch.setattr(atlasprocess.subprocess, "Popen", launch)
    yield ready, processes, mode
    for process in processes:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def layout(**kwargs):
    return atlasprocess.generate_layout(np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32),
                                        np.array([[0, 1, 2]], np.uint32), np.tile([0, 0, 1], (3, 1)),
                                        resolution=64, padding=2, **kwargs)


def wait_ready(path):
    deadline = time.monotonic() + 10
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists(), "The native worker did not start"


def test_parent_serves_http_while_native_child_holds_gil_and_cancel_stops_child(native_block):
    ready, processes, _mode = native_block
    cancel = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"responsive")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(layout, cancel=cancel, timeout=12)
            wait_ready(ready)
            try:
                for _ in range(5):
                    with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=2) as response:
                        assert response.read() == b"responsive"
                assert processes[0].poll() is None
            finally:
                cancel.set()
            with pytest.raises(Cancelled):
                future.result(timeout=3)
            assert processes[0].poll() is not None
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)


def test_deadline_stops_a_native_worker(native_block):
    _ready, processes, _mode = native_block
    with pytest.raises(atlasprocess.AtlasProcessError, match="exceeded"):
        layout(timeout=0.5)
    assert len(processes) == 1 and processes[0].poll() is not None


def test_native_failure_is_reported_and_no_child_is_left_running(native_block):
    _ready, processes, mode = native_block
    mode[0] = "fail"
    with pytest.raises(atlasprocess.AtlasProcessError, match="native layout rejection"):
        layout(timeout=5)
    assert len(processes) == 1 and processes[0].poll() == 7
