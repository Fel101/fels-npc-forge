"""Windows launcher: reuse a responsive server or explain a stalled one before starting.

    start.bat                    open NPC Forge, starting it when it is not running
    start.bat --setup-comfyui    the same, at Set up ComfyUI (Setup.exe's last page offers it)
"""
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
from npcgen import config, fels_suite  # noqa: E402
from npcgen.forge_endpoint import ask_identity, local_url  # noqa: E402


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


SETUP_COMFYUI = "--setup-comfyui"


def _open_existing(url, health, setup_comfyui=False):
    if not isinstance(health.get("fels"), dict):
        print(f"An older NPC Forge (1.0) is running at {url}. Opening it. Close it and run start.bat to use the new copy.")
        setup_comfyui = False                                  # 1.0 has no Set up ComfyUI
    else:
        print(f"NPC Forge is already running. Opening {url}")
    webbrowser.open(url + ("#setup-comfyui" if setup_comfyui else ""))
    return 0


def _port_in_use(port, who):
    print(f"Port {port} is in use by {who}. Close that app or set NPCGEN_PORT to a free port and run start.bat again.")
    return 1


def _owns_listener(owner, port):
    """Use this installation's running marker before offering to restart a Python listener."""
    try:
        recorded = json.loads((ROOT / "local" / "running.json").read_text("utf-8"))
    except (OSError, ValueError):
        return False
    return (recorded == {"pid": owner["pid"], "port": port}
            and (owner.get("name") or "").lower() in ("python.exe", "pythonw.exe")
            and "server.py" in (owner.get("command") or "").lower())


def main(argv=None):
    setup_comfyui = SETUP_COMFYUI in (sys.argv[1:] if argv is None else argv)
    print("Checking for a running NPC Forge. Press Ctrl+C to cancel.", flush=True)
    cfg = config.load(migrate_port=True)
    port = int(cfg["port"])
    url = local_url(cfg["host"], port)
    token = cfg["auth_token"]
    health = ask_identity(url, token)
    app = fels_suite.identify(health)
    if app == config.APP_ID:
        return _open_existing(url, health, setup_comfyui)
    if health is not None:
        return _port_in_use(port, fels_suite.short_name(app) if app else "another program")

    owner = _listener(port)
    if owner:
        # One retry avoids interrupting a server that was briefly busy starting.
        time.sleep(1)
        health = ask_identity(url, token)
        app = fels_suite.identify(health)
        if app == config.APP_ID:
            return _open_existing(url, health, setup_comfyui)
        if health is not None:
            return _port_in_use(port, fels_suite.short_name(app) if app else "another program")
        pid = int(owner["pid"])
        if not _owns_listener(owner, port):
            return _port_in_use(port, f"an unidentified program (PID {pid})")
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

    if port == config.DEFAULT_PORT and not owner:
        old_url = local_url(cfg["host"], config.LEGACY_PORT)
        old_health = ask_identity(old_url, token)
        old_app = fels_suite.identify(old_health)
        if old_app == config.APP_ID and not isinstance(old_health.get("fels"), dict):
            return _open_existing(old_url, old_health)
        if old_app == "voice-lab":
            print(f"Voice Lab is running on port {config.LEGACY_PORT}. NPC Forge will use port {port}.")
    print("Starting NPC Forge...")
    return subprocess.call([sys.executable, str(ROOT / "server.py"), "--open", *([SETUP_COMFYUI] if setup_comfyui else [])], cwd=ROOT)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (EOFError, KeyboardInterrupt):
        print("\nStart cancelled.")
        sys.exit(1)
