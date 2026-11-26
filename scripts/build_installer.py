"""Build a release's files from a commit: Setup.exe, the release ZIP and SHA256SUMS.

    python scripts/build_installer.py                  the commit checked out now
    python scripts/build_installer.py --ref v1.1.0     a release's tag (the release workflow does this)

They go to dist/ (or --output): FelsNPCForge-Setup-<version>.exe, FelsNPCForge-<version>.zip and SHA256SUMS, which names both.

Setup.exe is built by Inno Setup (installer/FelsNPCForge.iss) from what this stages in dist/staging:

- app: the commit's files from `git archive`, which is how GitHub packs the release's source ZIP. The installed folder holds
  exactly the release's files, so update.bat treats it like an unpacked ZIP.
- python: NPC Forge's own Python, fetched and checked by scripts/get_python.ps1 from data/python_runtime.json.
- wheels: the packages requirements.txt pins and the pip setup_core moves to, for 64-bit CPython 3.12 on Windows, from PyPI
  (`pip download`). Setup installs them with no internet, and deletes them.
- releases.json: the SHA-256 of every file of the commit and of each release before it (the tags v<major>.<minor>.<patch>
  up to its version), as `git archive` gives them. Setup hands this list to update.py --after-setup. The tags must be in this
  clone.
- retired.iss: [InstallDelete] rules for the files an older release had and this one does not. Setup checks the installed
  version and each file's hash first.

The release ZIP is the same `git archive`, in one folder named as GitHub names its source ZIP's, so update.bat can read it.

It needs git, Windows PowerShell, Inno Setup 6.7 or later (ISCC.exe: --iscc, the ISCC environment variable, the PATH, or
where Inno Setup installs) and the internet, for Python and the wheels.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from npcgen.updates import REPOSITORY  # noqa: E402


def _script(name):
    """A module of scripts/ by its file, which scripts/ (no __init__.py) cannot otherwise promise."""
    spec = importlib.util.spec_from_file_location(f"npcforge_{name}", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup_core, update = _script("setup_core"), _script("update")

APP = "FelsNPCForge"
SCRIPT = ROOT / "installer" / "FelsNPCForge.iss"
OUTPUT = ROOT / "dist"
STAGING_NAME = "staging"
# What setup leaves in .venv beyond the files Setup.exe copies, so the folder page asks for enough space: measured from an
# installed copy, rounded up.
ENVIRONMENT_BYTES = 150 * 1024 * 1024
ISCC_PLACES = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
)
VERSION_LINE = re.compile(r'^__version__ = "(?P<version>[^"]+)"$', re.MULTILINE)
RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
# The Python the wheels are for: the one Setup.exe carries (data/python_runtime.json).
WHEEL_TARGET = ["--platform", "win_amd64", "--python-version", "3.12", "--implementation", "cp", "--abi", "cp312",
                "--only-binary=:all:"]


class BuildError(Exception):
    """The build stopped. The message says why and what to do."""


def say(text=""):
    print(text, flush=True)


def git(*arguments):
    done = subprocess.run(["git", "-C", str(ROOT), *arguments], capture_output=True)
    if done.returncode:
        raise BuildError(f"git {' '.join(arguments)} failed: {done.stderr.decode('utf-8', 'replace').strip()}")
    return done.stdout


def installer_name(version):
    return f"{APP}-Setup-{version}.exe"


def zip_name(version):
    return f"{APP}-{version}.zip"


def zip_folder(version):
    """The one folder the release ZIP holds, named as GitHub names its source ZIP's: the repository, then the version."""
    return f"{REPOSITORY.split('/')[1]}-{version}"


def version_at(ref):
    """__version__ in npcgen/__init__.py at a commit."""
    found = VERSION_LINE.search(git("show", f"{ref}:npcgen/__init__.py").decode("utf-8"))
    if not found:
        raise BuildError(f"npcgen/__init__.py at {ref} has no __version__ line.")
    return found["version"]


def archive(ref, prefix=None):
    return git("archive", "--format=zip", *([f"--prefix={prefix}/"] if prefix else []), ref)


def stage_app(ref, target):
    """The commit's files, as `git archive` gives them (and GitHub's source ZIP holds)."""
    with zipfile.ZipFile(io.BytesIO(archive(ref))) as packed:
        for item in packed.infolist():
            parts = Path(item.filename).parts
            if item.is_dir() or not parts or ".." in parts or Path(item.filename).is_absolute():
                continue
            destination = target.joinpath(*parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(packed.read(item))
    return sum(1 for path in target.rglob("*") if path.is_file())


def file_hashes(ref):
    """{path: SHA-256} of a commit's files as `git archive` gives them, which is what its release ZIP and Setup.exe hold."""
    with zipfile.ZipFile(io.BytesIO(archive(ref))) as packed:
        return {item.filename: hashlib.sha256(packed.read(item)).hexdigest() for item in packed.infolist() if not item.is_dir()}


def version_key(version):
    found = RELEASE_TAG.match(f"v{version}")
    return tuple(int(part) for part in found.groups()) if found else None


def release_tags(version):
    """The release tags in this clone, v<major>.<minor>.<patch>, up to `version` and oldest first."""
    limit = version_key(version)
    tags = [line.strip() for line in git("tag", "--list", "v*").decode("utf-8").splitlines()]
    tags = [tag for tag in tags if RELEASE_TAG.match(tag) and (limit is None or version_key(tag[1:]) <= limit)]
    return sorted(tags, key=lambda tag: version_key(tag[1:]))


def retired_rules(lists, version):
    """[InstallDelete] lines for the files each older release had and this one does not. Inno checks, file by file, that the
    installed version is that release and the file is as it shipped (StageRetiredFile), so a file someone changed stays."""
    entries = []
    for old_version, hashes in lists["releases"].items():
        if old_version == version:
            continue
        for path in sorted(set(hashes) - set(lists["this"])):
            if update.protected(path):
                continue
            # Only plain names can go in Inno's rules; --after-setup handles the rest.
            if not re.fullmatch(r"[\w .+/-]+", path, flags=re.ASCII):
                continue
            name = path.replace("/", "\\")
            entries.append(f"Type: files; Name: \"{{app}}\\{name}\"; Check: StageRetiredFile('{name}', '{old_version}', '{hashes[path]}')")
    return entries


def stage_release_files(ref, version, target):
    """releases.json for update.py --after-setup, and retired.iss beside it."""
    tags = release_tags(version)
    if not tags:
        raise BuildError("No release tags (v1.0.0 and the like) are in this clone, so Setup could not tell which files an update "
                         "removes. Fetch them (git fetch --tags; in a workflow, actions/checkout with fetch-depth: 0) and build again.")
    lists = {"schema": update.SETUP_FILES_SCHEMA, "this": file_hashes(ref),
             "releases": {tag[1:]: file_hashes(f"refs/tags/{tag}") for tag in tags}}
    target.write_text(json.dumps(lists, sort_keys=True), encoding="utf-8")
    target.with_name("retired.iss").write_text("\n".join(retired_rules(lists, version)) + "\n", encoding="ascii")
    return tags


def stage_python(target):
    done = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(ROOT / "scripts" / "get_python.ps1"), "-Destination", str(target)],
                          env=setup_core.windows_powershell_env())
    if done.returncode or not (target / "python.exe").is_file():
        raise BuildError("scripts\\get_python.ps1 did not fetch NPC Forge's own Python; its message is above.")


def pinned():
    """The pins of requirements.txt, then the pip setup_core moves to."""
    wanted = setup_core.pins()
    return wanted + ([setup_core.PIP] if setup_core.PIP not in wanted else [])


def wheel_name(requirement):
    """The start of a wheel's file name for `name==version`: the name with - and . as _, then the version."""
    name, version = requirement.split("==")
    return f"{re.sub(r'[-_.]+', '_', name).lower()}-{version}-"


def stage_wheels(target, *, python=sys.executable, runner=subprocess.run):
    wanted = pinned()
    target.mkdir(parents=True, exist_ok=True)
    done = runner([str(python), "-m", "pip", "download", "--no-deps", "--disable-pip-version-check", *WHEEL_TARGET,
                   "--dest", str(target), *wanted])
    if done.returncode:
        raise BuildError("pip could not fetch the wheels; its message is above.")
    names = [path.name.lower() for path in target.glob("*.whl")]
    lacking = [item for item in wanted if not any(name.startswith(wheel_name(item)) for name in names)]
    if lacking:
        raise BuildError(f"No wheel was fetched for {', '.join(lacking)}.")
    return wanted


def find_iscc(given=None):
    for candidate in (given, os.environ.get("ISCC"), shutil.which("iscc"), *ISCC_PLACES):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise BuildError("Inno Setup was not found. Install Inno Setup 6.7 or later (winget install JRSoftware.InnoSetup), or give "
                     "its ISCC.exe with --iscc.")


def iscc_command(iscc, *, version, staging, output):
    return [str(iscc), "/Qp", f"/DAppVersion={version}", f"/DRepository={REPOSITORY}", f"/DStaging={staging}",
            f"/DOutput={output}", f"/DExtraBytes={ENVIRONMENT_BYTES}", str(SCRIPT)]


def checksums(files):
    lines = []
    for path in files:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        lines.append(f"{digest.hexdigest()}  {path.name}\n")
    return "".join(lines)


def build(ref, *, output=OUTPUT, iscc=None, keep_staging=False):
    iscc = find_iscc(iscc)
    version = version_at(ref)
    output = Path(output).resolve()
    staging = output / STAGING_NAME
    say(f"Building NPC Forge {version}'s release files from {ref}, with {iscc}.")
    if staging.exists():
        shutil.rmtree(staging)
    say("The release's files (git archive) ...")
    say(f"  {stage_app(ref, staging / 'app')} files.")
    say("The file lists of the releases up to it, for updates ...")
    say(f"  {', '.join(stage_release_files(ref, version, staging / 'releases.json'))}.")
    say("NPC Forge's own Python ...")
    stage_python(staging / "python")
    say("The wheels for .venv ...")
    say(f"  {len(stage_wheels(staging / 'wheels'))} wheels.")
    installer, packed = output / installer_name(version), output / zip_name(version)
    for old in (installer, packed, output / "SHA256SUMS"):
        old.unlink(missing_ok=True)
    say("Inno Setup ...")
    if subprocess.run(iscc_command(iscc, version=version, staging=staging, output=output)).returncode or not installer.is_file():
        raise BuildError("Inno Setup did not build Setup.exe; its message is above.")
    say("The release ZIP ...")
    packed.write_bytes(archive(ref, zip_folder(version)))
    (output / "SHA256SUMS").write_text(checksums([installer, packed]), encoding="utf-8", newline="\n")
    if not keep_staging:
        shutil.rmtree(staging, ignore_errors=True)
    say(f"Built {installer.name} ({installer.stat().st_size / 1e6:.0f} MB), {packed.name} ({packed.stat().st_size / 1e6:.0f} MB) "
        f"and SHA256SUMS in {output}.")
    return installer, packed


def build_parser():
    parser = argparse.ArgumentParser(description="Build Setup.exe, the release ZIP and SHA256SUMS from a commit.")
    parser.add_argument("--ref", default="HEAD", help="the commit or tag to build from (default: HEAD)")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="where the files go (default: dist)")
    parser.add_argument("--iscc", type=Path, help="Inno Setup's ISCC.exe, when it is not found")
    parser.add_argument("--keep-staging", action="store_true", help="leave the staging folder in place")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        build(args.ref, output=args.output, iscc=args.iscc, keep_staging=args.keep_staging)
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
