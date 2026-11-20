"""Run native xatlas outside Forge's interpreter so its UI stays responsive."""
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import numpy as np

TIMEOUT_SECONDS = 1800


class AtlasProcessError(RuntimeError):
    pass


def generate_layout(positions, indices, normals, *, resolution, padding, cancel=None, timeout=TIMEOUT_SECONDS):
    """The existing default charts/pack layout in a cancellable child process."""
    from .comfy import Cancelled, beat

    if timeout <= 0:
        raise ValueError("Texture layout timeout must be positive")
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    # (Windows can hold a killed worker's log open for a moment: a leftover temp folder must not turn a stop into a crash)
    with tempfile.TemporaryDirectory(prefix="npcforge-atlas-", ignore_cleanup_errors=True) as directory:
        work = Path(directory)
        source, target = work / "input.npz", work / "output.npz"
        np.savez(source, positions=np.ascontiguousarray(positions, np.float32),
                 indices=np.ascontiguousarray(indices, np.uint32), normals=np.ascontiguousarray(normals, np.float32),
                 resolution=np.int64(resolution), padding=np.int64(padding))
        command = [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", str(source), str(target)]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = None
        with (work / "worker.log").open("wb") as log:
            try:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    beat(cancel)                            # (still running: the watchdog's clock)
                    if time.monotonic() >= deadline:
                        raise AtlasProcessError(f"Texture layout exceeded {timeout:g} seconds; its worker was stopped")
                    try:
                        process.wait(timeout=0.1)
                    except subprocess.TimeoutExpired:
                        pass
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                if process.returncode or not target.is_file():
                    log.flush()
                    detail = (work / "worker.log").read_text(encoding="utf-8", errors="replace")[-1200:].strip()
                    raise AtlasProcessError("Texture layout failed" + (f": {detail}" if detail else f" (exit {process.returncode})"))
            finally:
                if process is not None and process.poll() is None:
                    _stop(process)
        with np.load(target, allow_pickle=False) as saved:
            return {name: saved[name].copy() for name in saved.files}


def _stop(process):
    """Stop the worker and anything it started. A virtual environment's python.exe on Windows is a launcher that runs the
    real interpreter as its child, so killing the launcher alone would leave xatlas running (and its log file open)."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def _worker(source, target):
    import xatlas

    with np.load(source, allow_pickle=False) as saved:
        atlas = xatlas.Atlas()
        atlas.add_mesh(saved["positions"], saved["indices"], saved["normals"])
        pack = xatlas.PackOptions()
        pack.resolution = int(saved["resolution"])
        pack.padding = int(saved["padding"])
        pack.bilinear, pack.rotate_charts = True, True
        atlas.generate(xatlas.ChartOptions(), pack)
        vmap, triangles, uv = atlas.get_mesh(0)
        np.savez(target, vmap=vmap, triangles=triangles, uv=uv,
                 width=np.int64(atlas.width), height=np.int64(atlas.height),
                 atlas_count=np.int64(atlas.atlas_count), chart_count=np.int64(atlas.chart_count))


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--worker":
        raise SystemExit("Usage: atlasprocess.py --worker input.npz output.npz")
    _worker(Path(sys.argv[2]), Path(sys.argv[3]))
