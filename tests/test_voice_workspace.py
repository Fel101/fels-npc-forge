"""Voice workspace state regressions without a browser, GPU, or user library."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_voice_workspace_controller():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for voice workspace checks")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, "--test", str(root / "scripts" / "check_voice_workspace.mjs")],
        cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
