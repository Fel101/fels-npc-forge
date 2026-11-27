"""The release kit: Setup.exe's script and what scripts/build_installer.py stages for it, .venv's setup (scripts/setup_core.py),
Setup over an older copy (update.py --after-setup), the uninstaller's helper and the icon. Inno Setup and the internet are not
used here; building and installing Setup.exe is checked by the release workflow (scripts/check_setup_update.ps1)."""
import hashlib
import importlib.util
import io
import json
import re
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from npcgen import __version__, updates

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "installer" / "FelsNPCForge.iss").read_text(encoding="utf-8")


def load(name):
    spec = importlib.util.spec_from_file_location(f"npcforge_test_{name}", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build, setup_core, upd, uninstall, make_icon, start = (load(name) for name in (
    "build_installer", "setup_core", "update", "uninstall_forge", "make_icon", "start"))


def git_works():
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True).returncode == 0
    except OSError:
        return False


def sha(data):
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- the icon and the pinned Python

def test_the_icon_is_what_icon_svg_draws_at_every_size():
    ico = Image.open(ROOT / "web" / "icon.ico")
    assert set(ico.info["sizes"]) == {(size, size) for size in make_icon.SIZES}
    for size in make_icon.SIZES:
        stored = ico.ico.getimage((size, size)).convert("RGBA")
        drawn = make_icon.draw(size)
        worst = max(high for _low, high in ImageChops.difference(stored, drawn).getextrema())
        assert worst <= 2, f"web/icon.ico at {size} px is not what web/icon.svg draws; run python scripts/make_icon.py"


def test_the_python_setup_carries_is_pinned_by_hash():
    runtime = json.loads((ROOT / "data" / "python_runtime.json").read_text(encoding="utf-8"))
    assert runtime["schema"] == 1 and runtime["version"].startswith("3.12.") and runtime["folder"] == "tools"
    assert runtime["url"].startswith("https://api.nuget.org/") and runtime["url"].endswith(f"python.{runtime['version']}.nupkg")
    assert re.fullmatch(r"[0-9a-f]{64}", runtime["sha256"]) and runtime["bytes"] > 10**7
    fetcher = (ROOT / "scripts" / "get_python.ps1").read_text(encoding="utf-8")
    assert "..\\data\\python_runtime.json" in fetcher and "ComputeHash" in fetcher


# ---------------------------------------------------------------- .venv: scripts/setup_core.py

class FakePython:
    """Answers setup_core's questions to .venv's Python and records pip's commands."""

    def __init__(self, version="3 12", lacking=(), starts=True):
        self.version, self.lacking, self.starts, self.pip = version, list(lacking), starts, []

    def __call__(self, command, **options):
        if command[1:3] == ["-m", "pip"]:
            self.pip.append(command[4:])                         # after pip install
            return types.SimpleNamespace(returncode=0)
        if not self.starts:
            return types.SimpleNamespace(returncode=1, stdout="")
        if command[2] == setup_core.MISSING:
            return types.SimpleNamespace(returncode=0, stdout="".join(f"{pin}\n" for pin in self.lacking))
        return types.SimpleNamespace(returncode=0, stdout=self.version + "\n")


def make_venv(root):
    python = root / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"")
    return python


def forge_folder(tmp_path):
    root = tmp_path / "forge"
    root.mkdir()
    (root / "requirements.txt").write_text((ROOT / "requirements.txt").read_text(encoding="utf-8"), encoding="utf-8")
    return root


def test_a_new_venv_gets_setups_pip_and_every_pin_from_the_wheels_alone(tmp_path):
    root, fake = forge_folder(tmp_path), FakePython()
    said = setup_core.setup(root, wheels=tmp_path / "wheels", runner=fake, make=lambda env: make_venv(root), say=lambda text: None,
                            python_version=(3, 12))
    offline = ["--no-input", "--disable-pip-version-check", "--no-index", "--find-links", str(tmp_path / "wheels")]
    assert fake.pip == [offline + [setup_core.PIP], offline + ["-r", str(root / "requirements.txt")]]
    assert said == "Made .venv with NPC Forge's packages." and not (root / ".venv" / setup_core.UNFINISHED).exists()


def test_a_venv_that_has_every_pin_is_left_alone(tmp_path):
    root, fake = forge_folder(tmp_path), FakePython()
    make_venv(root)
    assert "nothing changed" in setup_core.setup(root, wheels=tmp_path, runner=fake, say=lambda text: None)
    assert fake.pip == []


def test_a_venv_from_another_python_gets_what_it_lacks_from_pypi_not_the_312_wheels(tmp_path):
    root, fake, said = forge_folder(tmp_path), FakePython(version="3 10", lacking=["numpy==2.2.6"]), []
    make_venv(root)
    assert "numpy==2.2.6" in setup_core.setup(root, wheels=tmp_path / "wheels", runner=fake, say=said.append)
    assert fake.pip == [["--no-input", "--disable-pip-version-check", "-r", str(root / "requirements.txt")]]
    assert any("from PyPI" in line for line in said)


def test_setup_says_what_to_do_when_it_cannot_go_on(tmp_path):
    root = forge_folder(tmp_path)
    with pytest.raises(setup_core.SetupStopped, match="3.10 or newer"):
        setup_core.setup(root, runner=FakePython(), make=lambda env: make_venv(root), python_version=(3, 9), say=lambda text: None)
    with pytest.raises(setup_core.SetupStopped, match="for Python 3.12"):
        setup_core.setup(root, wheels=tmp_path, runner=FakePython(), python_version=(3, 11), say=lambda text: None)
    make_venv(root)
    with pytest.raises(setup_core.SetupStopped, match="does not start.*Delete the .venv folder"):
        setup_core.setup(root, runner=FakePython(starts=False), say=lambda text: None)


def test_onedrive_is_found_by_windows_own_variables(tmp_path):
    assert setup_core.onedrive_folder(tmp_path / "a" / "b", {"OneDriveConsumer": str(tmp_path / "a")}) == str(tmp_path / "a")
    assert setup_core.onedrive_folder(tmp_path / "b", {"OneDrive": str(tmp_path / "a")}) is None


# ---------------------------------------------------------------- what the build stages

def test_the_wheels_are_every_pin_and_setups_pip_for_the_python_setup_carries(tmp_path):
    wanted = build.pinned()
    assert wanted == setup_core.pins() + [setup_core.PIP] and len(wanted) == len(set(wanted))
    made = []

    def runner(command):
        made.append(command)
        for item in command[command.index("--dest") + 2:]:
            if not item.startswith("xatlas"):
                name, version = item.split("==")
                (tmp_path / f"{name}-{version}-cp312-cp312-win_amd64.whl").write_bytes(b"")
        return types.SimpleNamespace(returncode=0)

    with pytest.raises(build.BuildError, match=r"No wheel was fetched for xatlas==\S+\."):
        build.stage_wheels(tmp_path, runner=runner)
    assert made[0][1:4] == ["-m", "pip", "download"]
    assert all(flag in made[0] for flag in ("win_amd64", "cp312", "--only-binary=:all:", "--no-deps"))
    assert build.wheel_name("Pillow==12.3.0") == "pillow-12.3.0-" and build.wheel_name("zope.interface==7.0") == "zope_interface-7.0-"


def test_files_an_older_release_dropped_are_retired_only_as_it_shipped_them():
    lists = {"this": {"server.py": "1"},
             "releases": {"1.0.0": {"server.py": "0", "npcgen/voice/engines.py": "e", "local/x.json": "p", "odd'name.py": "q"},
                          "1.1.0": {"server.py": "1"}}}
    assert build.retired_rules(lists, "1.1.0") == [
        "Type: files; Name: \"{app}\\npcgen\\voice\\engines.py\"; Check: StageRetiredFile('npcgen\\voice\\engines.py', '1.0.0', 'e')"]


@pytest.mark.skipif(not git_works(), reason="needs git and this repository's history")
def test_the_app_and_the_release_zip_are_the_commits_files(tmp_path):
    count = build.stage_app("HEAD", tmp_path / "app")
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-tree", "-r", "--name-only", "HEAD"], capture_output=True, text=True,
                             check=True).stdout.split("\n")
    assert count == len([line for line in tracked if line])
    assert b"\r\n" in (tmp_path / "app" / "start.bat").read_bytes(), "the .bat files keep CRLF, as in GitHub's ZIP"
    assert build.version_at("HEAD") == __version__
    packed = tmp_path / build.zip_name(__version__)
    packed.write_bytes(build.archive("HEAD", build.zip_folder(__version__)))
    files = upd.read_archive(packed)
    assert upd.archive_version(files) == __version__ and "server.py" in files
    with zipfile.ZipFile(packed) as archive:
        assert {name.split("/", 1)[0] for name in archive.namelist()} == {f"fels-npc-forge-{__version__}"}


def test_the_checksums_are_in_sha256sum_form(tmp_path):
    one, two = tmp_path / "FelsNPCForge-Setup-9.9.9.exe", tmp_path / "FelsNPCForge-9.9.9.zip"
    one.write_bytes(b"abc")
    two.write_bytes(b"")
    assert build.checksums([one, two]) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad  FelsNPCForge-Setup-9.9.9.exe\n"
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  FelsNPCForge-9.9.9.zip\n")


def test_a_missing_inno_setup_says_how_to_get_it(monkeypatch):
    monkeypatch.setattr(build, "ISCC_PLACES", ())
    monkeypatch.setattr(build.shutil, "which", lambda name: None)
    monkeypatch.setenv("ISCC", "")
    with pytest.raises(build.BuildError, match="winget install JRSoftware.InnoSetup"):
        build.find_iscc()


# ---------------------------------------------------------------- installer/FelsNPCForge.iss

def test_every_define_the_script_uses_is_passed_by_the_build():
    command = build.iscc_command(Path("ISCC.exe"), version="9.9.9", staging=Path("S"), output=Path("O"))
    passed = {item[2:].split("=", 1)[0] for item in command if item.startswith("/D")}
    used = set(re.findall(r"\{#(\w+)\}", SCRIPT)) - set(re.findall(r"^#define (\w+)", SCRIPT, re.MULTILINE))
    assert used == passed and f"/DRepository={updates.REPOSITORY}" in command and command[-1] == str(build.SCRIPT)


def test_it_installs_for_this_user_into_a_folder_of_its_own():
    assert "PrivilegesRequired=lowest\n" in SCRIPT and "DefaultDirName={localappdata}\\FelsNPCForge\n" in SCRIPT
    app_id = re.search(r"^AppId=\{\{([0-9A-F-]+)\}$", SCRIPT, re.MULTILINE)[1]
    assert re.fullmatch(r"[0-9A-F]{8}-[0-9A-F]{4}-4[0-9A-F]{3}-[89AB][0-9A-F]{3}-[0-9A-F]{12}", app_id)
    assert app_id != "6F1D2A8B-5C3E-4B7A-9D21-3E8F0C4A7B15", "Voice Lab's AppId would make each Setup update the other app"
    assert [name for name in re.findall(r"Names\[\d\] := '(\w+)'", SCRIPT)] == list(setup_core.ONEDRIVE_VARIABLES)
    assert "'.git'" in SCRIPT, "a Git checkout is refused: git and update.bat update it"
    assert "SetupIconFile={#Staging}\\app\\web\\icon.ico" in SCRIPT and (ROOT / "web" / "icon.ico").is_file()


def test_it_reads_the_installed_version_and_asks_the_installed_updater_whether_forge_runs():
    prefix = '__version__ = "'
    assert f"(Copy(Line, 1, {len(prefix)}) = '{prefix}')" in SCRIPT and f"Copy(Line, {len(prefix) + 1}, Length(Line) - {len(prefix) + 1})" in SCRIPT
    assert (ROOT / "npcgen" / "__init__.py").read_text(encoding="utf-8").count(f'{prefix}{__version__}"') == 1
    assert "m.app_processes(sys.argv[2])" in SCRIPT and callable(upd.app_processes)


def test_after_copying_it_finishes_an_update_and_sets_up_venv_from_its_wheels():
    found = re.search(r"'\" --after-setup --from ' \+ InstalledVersion \+ ' --setup-files \"' \+\s+ExpandConstant\('\{tmp\}\\releases\.json'\) "
                      r"\+ '\" --wheels \"' \+ ExpandConstant\('\{tmp\}\\wheels'\) \+ '\" --summary \"' \+\s+ExpandConstant\('\{tmp\}\\update-summary\.txt'\)",
                      SCRIPT)
    assert found, "Setup passes --after-setup its file lists, wheels and summary"
    args = upd.build_parser().parse_args(["--after-setup", "--from", "1.0.0", "--setup-files", "r.json", "--wheels", "w", "--summary", "s"])
    assert args.after_setup and args.from_version == "1.0.0" and args.wheels == Path("w")
    assert "scripts\\setup_core.py') + '\" --wheels \"'" in SCRIPT and "function GetCustomSetupExitCode" in SCRIPT


def test_its_last_page_opens_the_normal_character_workflow():
    assert 'Filename: "{app}\\start.bat"; WorkingDir:' in SCRIPT
    assert "--setup-comfyui" not in SCRIPT
    assert "start.py %*" in (ROOT / "start.bat").read_text(encoding="utf-8")
    assert '"#setup-comfyui"' in (ROOT / "server.py").read_text(encoding="utf-8")
    assert "'#setup-comfyui'" in (ROOT / "web" / "app.js").read_text(encoding="utf-8")


def test_a_running_forge_is_opened_at_set_up_comfyui_unless_it_is_1_0(monkeypatch):
    opened = []
    monkeypatch.setattr(start.webbrowser, "open", opened.append)
    start._open_existing("http://127.0.0.1:8766", {"fels": {"app": "npc-forge"}}, True)
    start._open_existing("http://127.0.0.1:8765", {"ok": True}, True)
    assert opened == ["http://127.0.0.1:8766#setup-comfyui", "http://127.0.0.1:8765"]


# ---------------------------------------------------------------- Setup over an older copy: update.py --after-setup

def test_setup_over_an_older_copy_keeps_what_it_dropped_and_what_you_changed_and_finishes(tmp_path):
    root = tmp_path / "forge"
    files = {"server.py": b"new", "requirements.txt": b"numpy==2"}
    old = {"server.py": b"old", "requirements.txt": b"numpy==1", "gone.py": b"gone", "edited.py": b"as shipped",
           "removed_by_setup.py": b"x", "npcgen/voice/engines.py": b"voice"}
    for path, data in {**files, "gone.py": b"gone", "edited.py": b"mine", "npcgen/voice/engines.py": b"voice"}.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    cache = root / "npcgen" / "voice" / "__pycache__" / "engines.cpython-312.pyc"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"")
    (root / "voice-envs" / "kokoro").mkdir(parents=True)
    (root / "voice-envs" / "kokoro" / "model.bin").write_bytes(b"x" * 1000)
    python = make_venv(root)
    setup = tmp_path / "setup-tmp"
    (setup / "retired").mkdir(parents=True)
    (setup / "retired" / "removed_by_setup.py").write_bytes(b"x")        # [InstallDelete] removed it, StageRetiredFile kept this copy
    lists = {"schema": 1, "this": {path: sha(data) for path, data in files.items()},
             "releases": {"0.9.0": {path: sha(data) for path, data in old.items()}}}
    (setup / "releases.json").write_text(json.dumps(lists), encoding="utf-8")
    args = upd.build_parser().parse_args(["--after-setup", "--from", "0.9.0", "--setup-files", str(setup / "releases.json"),
                                          "--wheels", str(setup / "wheels"), "--summary", str(setup / "summary.txt")])
    ran = []
    status = upd.after_setup(root, args=args, runner=lambda command, cwd=None: (ran.append(command), types.SimpleNamespace(returncode=0))[1],
                             say=lambda text="": None)
    assert status == 0 and python.is_file()
    record = upd.read_record(root)
    assert record["method"] == "setup" and record["state"] == "applied" and record["from"] == "0.9.0" and record["to"] == __version__
    assert record["removed"] == ["gone.py", "npcgen/voice/engines.py", "removed_by_setup.py"] and record["kept"] == ["edited.py"]
    backup = root / record["backup"] / "files"
    assert (backup / "gone.py").read_bytes() == b"gone" and (backup / "removed_by_setup.py").read_bytes() == b"x"
    assert (root / "edited.py").read_bytes() == b"mine" and not (root / "gone.py").exists()
    assert not (root / "npcgen" / "voice").exists(), "a folder left with Python's caches alone is removed"
    assert ran == [[sys.executable, str(root / "scripts" / "setup_core.py"), "--wheels", str(setup / "wheels")]]
    summary = (setup / "summary.txt").read_text(encoding="ascii")
    assert summary.startswith(f"Updated from version 0.9.0 to {__version__}.") and "edited.py" in summary and "voice-envs" in summary
    assert (root / "voice-envs").is_dir(), "the voice folders are named, never deleted"
    with pytest.raises(upd.UpdateStopped, match="Setup.exe made the last update"):
        upd.undo(root, args=upd.build_parser().parse_args(["--undo", "--yes"]), processes=lambda r: [])


def test_leftovers_are_only_folders_of_caches(tmp_path):
    root = tmp_path / "forge"
    for path in ("a/b/__pycache__/x.cpython-312.pyc", "c/__pycache__/y.cpython-312.pyc", "c/notes.txt", "d/__pycache__/z.txt"):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(b"")
    assert upd.clear_leftovers(root, ["a/b/x.py", "c/y.py", "d/z.py", "library/q.py"]) == ["a"]
    assert not (root / "a").exists() and (root / "c" / "notes.txt").exists() and (root / "d").exists()


def test_the_voice_folders_are_named_only_after_an_update_from_1_0(tmp_path):
    (tmp_path / "voice-models").mkdir()
    record = {"state": "applied", "from": "1.0.0", "to": "1.1.0"}
    notes = upd.leftover_notes(tmp_path, record, measure=lambda folder: 3 * 10**9)
    assert len(notes) == 1 and "voice-models" in notes[0] and "3.0 GB" in notes[0] and "voice-envs" not in notes[0]
    assert "(2 MB)" in upd.leftover_notes(tmp_path, record, measure=lambda folder: 2_400_000)[0]
    assert "(under 1 MB)" in upd.leftover_notes(tmp_path, record, measure=lambda folder: 0)[0]
    assert upd.leftover_notes(tmp_path, {**record, "from": "1.1.0", "to": "1.2.0"}) == []
    assert upd.leftover_notes(tmp_path, {**record, "state": "undone"}) == []


def test_updates_never_touch_setups_python_or_built_release_files():
    assert upd.protected("python/python.exe") and upd.protected("dist/FelsNPCForge-1.1.0.zip") and not upd.protected("scripts/x.py")


# ---------------------------------------------------------------- uninstalling

def uninstall_folder(tmp_path):
    root = tmp_path / "FelsNPCForge"
    for path in ("library/mira/npc.json", "local/extensions/x/__init__.py", "config.json", "my chars/npc.json", "server.py",
                 ".venv/Scripts/python.exe", "npcgen/__pycache__/x.pyc", "animations/quaternius/UAL1.glb", "voice-envs/k/x",
                 "python/python.exe", "unins000.exe", "unins000.dat"):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(b"x")
    (root / "config.json").write_text(json.dumps({"library_dir": "my chars"}), encoding="utf-8")
    return root


def test_uninstalling_keeps_the_library_and_the_configured_one_inside_unless_told_otherwise(tmp_path):
    root = uninstall_folder(tmp_path)
    said = []
    assert uninstall.run(root, keep_library=True, say=said.append) == 0
    assert sorted(path.name for path in root.iterdir()) == ["config.json", "library", "local", "my chars", "python", "unins000.dat",
                                                            "unins000.exe"]
    assert "my chars" in said[0]
    assert uninstall.main(["--everything", "--root", str(root)]) == 0
    assert sorted(path.name for path in root.iterdir()) == ["python", "unins000.dat", "unins000.exe"]


def test_the_uninstaller_runs_the_helper_with_both_choices():
    assert "Choice := '--keep-library'" in SCRIPT and "Choice := '--everything';" in SCRIPT
    assert "scripts\\uninstall_forge.py" in SCRIPT and "DelTree(ExpandConstant('{app}\\python'), True, True, True);" in SCRIPT
    with pytest.raises(SystemExit):
        uninstall.main([])
