"""Windows launcher: reuse a responsive server or explain a stalled one before starting."""
import http.client
import json
import os
import pathlib
import signal
import subprocess
import sys
import time
import webbrowser

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from npcgen.config import load  # noqa: E402


def _reachable(host, port):
    """Probe the local page, which does not depend on ComfyUI or the job worker."""
    conn = http.client.HTTPConnection(host, port, timeout=3)
    try:
        conn.request("GET", "/")
        return conn.getresponse().status in (200, 401)
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def _listener(port):
    """The process on this port, if Windows can identify one."""
    script = (
        f"$c = Get-NetTCPConnection -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue | "
        "Select-Object -First 1; "
        "if ($c) { $p = Get-CimInstance Win32_Process -Filter "
        "\"ProcessId = $($c.OwningProcess)\"; "
        "[pscustomobject]@{pid=$c.OwningProcess; name=$p.Name; command=$p.CommandLine} | ConvertTo-Json -Compress }"
    )
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=8, check=False)
        return json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def main():
    cfg = load()
    port = int(cfg["port"])
    host = cfg["host"] if cfg["host"] not in ("0.0.0.0", "::") else "127.0.0.1"
    url = f"http://{host}:{port}"
    if _reachable(host, port):
        print(f"NPC Forge is already running. Opening {url}")
        webbrowser.open(url)
        return 0

    owner = _listener(port)
    if owner:
        # One retry avoids interrupting a server that was briefly busy starting.
        time.sleep(1)
        if _reachable(host, port):
            print(f"NPC Forge is already running. Opening {url}")
            webbrowser.open(url)
            return 0
        pid = int(owner["pid"])
        command = owner.get("command") or ""
        if (owner.get("name") or "").lower() not in ("python.exe", "pythonw.exe") or "server.py" not in command.lower():
            print(f"Port {port} is held by process {pid}, which is not an identifiable NPC Forge server.")
            print("Close that process or choose another NPCGEN_PORT, then start NPC Forge again.")
            return 1
        print(f"An existing NPC Forge process (PID {pid}) is holding port {port} but is not responding.")
        print("Restarting it will interrupt any work it still has in progress.")
        if input("Restart the unresponsive process? [y/N] ").strip().lower() not in ("y", "yes"):
            return 1
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            print(f"Could not stop process {pid}: {exc}")
            return 1
        time.sleep(1)

    print("Starting NPC Forge...")
    return subprocess.call([sys.executable, str(ROOT / "server.py"), "--open"], cwd=ROOT)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (EOFError, KeyboardInterrupt):
        print("\nStart cancelled.")
        sys.exit(1)
