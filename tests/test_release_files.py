"""A release's notes (scripts/release_notes.py), its files as checked before the draft is published
(scripts/check_release_files.py), and the workflows that build and check them."""
import hashlib
import importlib.util
import re
import types
from pathlib import Path

import pytest

from npcgen import __version__, updates

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def load(name):
    spec = importlib.util.spec_from_file_location(f"npcforge_test_{name}", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


notes, files, build = load("release_notes"), load("check_release_files"), load("build_installer")

CHANGELOG = """# Changelog

## 1.1.0 (2026-12-01)

What changed for you:

- **The port:** NPC Forge now uses 8766, so it runs beside
  [Voice Lab](https://github.com/Fel101/fels-voice-lab) and keeps your [status](docs/status.md#checks).
- Pictures: ![styles](docs/images/gallery/styles.jpg)

See [the 1.0.0 notes](#100-2026-11-20).

## 1.0.0 (2026-11-20)

The first public release.
"""


def test_the_notes_are_the_versions_section_unwrapped_with_links_at_its_tag(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    text = notes.notes("1.1.0", changelog=changelog, repository="Fel101/fels-npc-forge")
    blob = "https://github.com/Fel101/fels-npc-forge/blob/v1.1.0"
    assert text.splitlines() == [
        "What changed for you:", "",
        "- **The port:** NPC Forge now uses 8766, so it runs beside [Voice Lab](https://github.com/Fel101/fels-voice-lab) and keeps "
        f"your [status]({blob}/docs/status.md#checks).",
        "- Pictures: ![styles](https://github.com/Fel101/fels-npc-forge/raw/v1.1.0/docs/images/gallery/styles.jpg)", "",
        f"See [the 1.0.0 notes]({blob}/CHANGELOG.md#100-2026-11-20)."]


def test_the_notes_refuse_what_update_bat_would_refuse(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG + "\n## 1.2.0\n\nNo date.\n\n## 1.3.0 (soon)\n\nx\n", encoding="utf-8")
    for version, tag, problem in (("1.1.0", "v1.1.1", "is not v1.1.0"), ("1.1", None, "not a version"),
                                  ("9.9.9", None, "no section for 9.9.9"), ("1.2.0", None, "has no date"),
                                  ("1.3.0", None, "'soon' for its date")):
        with pytest.raises(notes.NotesError, match=problem):
            notes.notes(version, changelog=changelog, tag=tag)
    assert notes.main(["1.1.0", "--tag", "v1.1.0", "--changelog", str(changelog), "--output", str(tmp_path / "n.md")]) == 0
    assert notes.main(["1.1.0", "--tag", "v2.0.0", "--changelog", str(changelog), "--output", str(tmp_path / "bad.md")]) == 1
    assert not (tmp_path / "bad.md").exists()


def test_this_versions_changelog_heading_is_one_the_notes_can_read():
    found = [notes.HEADING.fullmatch(line) for line in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
             if line.startswith("## ")]
    assert found and all(found) and all(match["date"] for match in found)


def release_folder(tmp_path, tag="v1.1.0"):
    folder = tmp_path / "draft"
    folder.mkdir()
    names = files.release_names(tag)
    sums = ""
    for name, data in zip(names, (b"setup", b"zip")):
        (folder / name).write_bytes(data)
        sums += f"{hashlib.sha256(data).hexdigest()}  {name}\n"
    (folder / files.SUMS).write_text(sums, encoding="utf-8")
    return folder


def test_a_release_holds_setup_the_zip_and_their_checksums_and_nothing_else(tmp_path):
    folder = release_folder(tmp_path)
    assert [path.name for path in files.check_files(folder, "v1.1.0")] == ["FelsNPCForge-Setup-1.1.0.exe", "FelsNPCForge-1.1.0.zip",
                                                                         "SHA256SUMS"]
    assert files.release_names("v1.1.0") == [build.installer_name("1.1.0"), build.zip_name("1.1.0")]
    (folder / "FelsNPCForge-1.1.0.zip").write_bytes(b"changed")
    with pytest.raises(files.ReleaseFilesError, match="zip changed after SHA256SUMS was written"):
        files.check_files(folder, "v1.1.0")
    (folder / "extra.txt").write_text("x")
    with pytest.raises(files.ReleaseFilesError, match="not expected extra.txt"):
        files.check_files(folder, "v1.1.0")
    with pytest.raises(files.ReleaseFilesError, match="not a release tag"):
        files.check_files(folder, "1.1.0")


def test_each_file_must_be_attested_by_this_repositorys_release_workflow(tmp_path):
    ran = []
    paths = files.check_files(release_folder(tmp_path), "v1.1.0")
    files.check_attestations(paths, "Fel101/fels-npc-forge", run=lambda command: (ran.append(command), types.SimpleNamespace(returncode=0))[1],
                             which=lambda name: "gh")
    assert len(ran) == 3 and ran[0][-2:] == ["--signer-workflow", "Fel101/fels-npc-forge/.github/workflows/release.yml"]
    with pytest.raises(files.ReleaseFilesError, match="no attestation"):
        files.check_attestations(paths, "x/y", run=lambda command: types.SimpleNamespace(returncode=1), which=lambda name: "gh")
    with pytest.raises(FileNotFoundError, match="gh"):
        files.check_attestations(paths, "x/y", which=lambda name: None)


def test_the_release_workflow_builds_attests_drafts_checks_then_publishes():
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    jobs = re.findall(r"^  (\w+):\n", text.split("\njobs:\n", 1)[1], re.MULTILINE)
    assert jobs == ["verify", "draft", "check", "publish"]
    assert "uses: ./.github/workflows/tests.yml" in text and "workflow_call:" in (WORKFLOWS / "tests.yml").read_text(encoding="utf-8")
    for needed in ("scripts/release_notes.py --tag", "scripts/build_installer.py --ref HEAD", "actions/attest@",
                   "FelsNPCForge-Setup-*.exe", "FelsNPCForge-*.zip", "SHA256SUMS", "--draft --verify-tag",
                   "scripts/check_release_files.py", "scripts/check_setup_update.ps1", "--draft=false"):
        assert needed in text, needed
    for script in ("release_notes.py", "build_installer.py", "check_release_files.py", "check_setup_update.ps1"):
        assert (ROOT / "scripts" / script).is_file()


def test_the_setup_check_reads_the_version_and_repository_where_they_live():
    check = (ROOT / "scripts" / "check_setup_update.ps1").read_text(encoding="utf-8")
    assert "npcgen\\__init__.py -Pattern '^__version__ = \"(.+)\"$'" in check
    assert (ROOT / "npcgen" / "__init__.py").read_text(encoding="utf-8").count(f'__version__ = "{__version__}"') == 1
    assert "npcgen\\updates.py -Pattern '^REPOSITORY = \"(.+)\"$'" in check
    assert f'REPOSITORY = "{updates.REPOSITORY}"' in (ROOT / "npcgen" / "updates.py").read_text(encoding="utf-8")
    assert "$env:FELS_SUITE_DIR = " in check and "$env:NPCGEN_COMFY_URL = " in check, "its servers never touch this PC's own"
