"""Check a release's files as they were downloaded from its draft, before the draft is published.

    python scripts/check_release_files.py --dir <folder> --tag v1.1.0
    python scripts/check_release_files.py --dir <folder> --tag v1.1.0 --no-attestation     the files only, without gh

The release workflow (.github/workflows/release.yml) makes each release as a draft, downloads its files from the draft into
a folder of their own, and runs this before it publishes anything. It checks that:

- the folder holds FelsNPCForge-Setup-<version>.exe, FelsNPCForge-<version>.zip and SHA256SUMS, and nothing else;
- SHA256SUMS names those two files, and each SHA-256 is the file's;
- `gh attestation verify` finds each of the three attested by this repository's release workflow.

Exit status: 0 when all of that holds; 1 with the reason when something does not; 2 when gh is needed and missing.
"""
import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from npcgen.updates import REPOSITORY  # noqa: E402

SUMS = "SHA256SUMS"
SIGNER = ".github/workflows/release.yml"
SUM_LINE = re.compile(r"(?P<digest>[0-9a-f]{64}) [ *](?P<name>[^/\\]+)")


class ReleaseFilesError(Exception):
    """A release file is missing, unexpected, changed or not attested; the message says which and what to do."""


def release_names(tag):
    """The two files a release carries besides SHA256SUMS: Setup.exe and the ZIP (scripts/build_installer.py names them)."""
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag or ""):
        raise ReleaseFilesError(f"{tag!r} is not a release tag such as v1.1.0.")
    return [f"FelsNPCForge-Setup-{tag[1:]}.exe", f"FelsNPCForge-{tag[1:]}.zip"]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check_files(folder, tag):
    """The files the folder must hold, after checking their names and SHA256SUMS. Raises ReleaseFilesError."""
    folder = Path(folder)
    names = release_names(tag)
    if not folder.is_dir():
        raise ReleaseFilesError(f"There is no folder {folder}: download the draft's files into it first.")
    found = sorted(path.name for path in folder.iterdir())
    expected = sorted([*names, SUMS])
    if found != expected:
        missing = [name for name in expected if name not in found]
        extra = [name for name in found if name not in expected]
        parts = (["missing " + ", ".join(missing)] if missing else []) + (["not expected " + ", ".join(extra)] if extra else [])
        raise ReleaseFilesError(f"The release for {tag} should have {', '.join(expected)} only: {'; '.join(parts)}.")
    lines = [line for line in (folder / SUMS).read_text(encoding="utf-8").splitlines() if line.strip()]
    entries = [SUM_LINE.fullmatch(line) for line in lines]
    if len(entries) != len(names) or not all(entries) or sorted(entry["name"] for entry in entries) != sorted(names):
        raise ReleaseFilesError(f"{SUMS} should hold two lines, the SHA-256 of {' and '.join(names)}; it holds {len(lines)}.")
    for entry in entries:
        actual = sha256(folder / entry["name"])
        if entry["digest"] != actual:
            raise ReleaseFilesError(f"{entry['name']} changed after {SUMS} was written: {SUMS} says {entry['digest']}, the file is "
                                    f"{actual}. Delete the draft and run the release again.")
    return [folder / name for name in names] + [folder / SUMS]


def attestation_command(gh, path, repository):
    return [gh, "attestation", "verify", str(path), "--repo", repository, "--signer-workflow", f"{repository}/{SIGNER}"]


def check_attestations(paths, repository, *, run=subprocess.run, which=shutil.which):
    gh = which("gh")
    if not gh:
        raise FileNotFoundError("gh, GitHub's command line, is needed to check the attestations; install it or pass --no-attestation.")
    for path in paths:
        if run(attestation_command(gh, path, repository)).returncode:
            raise ReleaseFilesError(f"{path.name} has no attestation from {repository}'s release workflow (its message is above). "
                                    "Do not publish the draft.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check a release's downloaded files before its draft is published.")
    parser.add_argument("--dir", required=True, type=Path, help="the folder the draft's files were downloaded into")
    parser.add_argument("--tag", required=True, help="the release's tag, such as v1.1.0")
    parser.add_argument("--repo", default=REPOSITORY, help="owner/name (default: this project's)")
    parser.add_argument("--no-attestation", action="store_true", help="check the names and SHA256SUMS only")
    args = parser.parse_args(argv)
    try:
        paths = check_files(args.dir, args.tag)
        if not args.no_attestation:
            check_attestations(paths, args.repo)
    except ReleaseFilesError as error:
        print(error, file=sys.stderr)
        return 1
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return 2
    print(f"The release files for {args.tag} are as built" + ("." if args.no_attestation else ", and all three are attested."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
