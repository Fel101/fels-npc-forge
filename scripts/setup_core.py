"""Make NPC Forge's Python environment, .venv, with the packages requirements.txt pins.

    python scripts/setup_core.py                    setup.bat: with the Python it found or fetched, from PyPI
    python\\python.exe scripts/setup_core.py --wheels <folder>   Setup.exe: from the wheels it carries, with no internet

A .venv that already has every pinned package is left as it is. One that lacks some (an update changed requirements.txt, or
an earlier setup was interrupted) gets them, and a missing one is made from the Python running this. Standard library only.
"""
import argparse
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements.txt"
# Left inside .venv until its packages are in, so the next run finishes a setup that was interrupted.
UNFINISHED = ".npc-forge-setup-unfinished"
# The pip .venv is moved to. Setup.exe carries its wheel (scripts/build_installer.py).
PIP = "pip==26.2.2"
# Where Windows says the OneDrive folders are. Setup.exe's folder page warns with the same rule (installer/FelsNPCForge.iss).
ONEDRIVE_VARIABLES = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
OLDEST = (3, 10)
# Prints the pins of requirements.txt (given as arguments) that this Python's packages do not meet, one per line.
MISSING = (
    "import importlib.metadata as m, sys\n"
    "for pin in sys.argv[1:]:\n"
    "    name, version = pin.split('==')\n"
    "    try:\n"
    "        found = m.version(name)\n"
    "    except m.PackageNotFoundError:\n"
    "        found = None\n"
    "    if found != version:\n"
    "        print(pin)\n"
)


class SetupStopped(Exception):
    """Setup cannot go on; the message says why and what to do."""


def onedrive_folder(root, environ=os.environ):
    """The OneDrive folder that holds `root`, or None. OneDrive would upload every character made here and can hold files
    open while it syncs them."""
    for name in ONEDRIVE_VARIABLES:
        folder = environ.get(name)
        if folder:
            try:
                Path(root).resolve().relative_to(Path(folder).resolve())
            except ValueError:
                continue
            return folder
    return None


def windows_powershell_env(environ=os.environ):
    """The environment to start Windows PowerShell (powershell.exe) with: this one without PSModulePath, so that it uses its
    own module path. One inherited from PowerShell 7 names PowerShell 7's modules, which Windows PowerShell cannot load."""
    return {name: value for name, value in environ.items() if name.upper() != "PSMODULEPATH"}


def pins(path=REQUIREMENTS):
    """The `name==version` lines of requirements.txt, without comments."""
    lines = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            if "==" not in line:
                raise SetupStopped(f"requirements.txt should pin every package with ==; this line does not: {line}")
            lines.append(line)
    return lines


def venv_python(root=ROOT):
    for candidate in (Path(root) / ".venv" / "Scripts" / "python.exe", Path(root) / ".venv" / "bin" / "python"):
        if candidate.is_file():
            return candidate
    return None


def venv_version(python, *, runner=subprocess.run):
    """(major, minor) of the Python in .venv, or None when it does not start (the Python it was made from has gone)."""
    try:
        done = runner([str(python), "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"], capture_output=True,
                      text=True)
    except OSError:
        return None
    if done.returncode:
        return None
    try:
        major, minor = done.stdout.split()
        return int(major), int(minor)
    except ValueError:
        return None


def missing(python, wanted, *, runner=subprocess.run):
    """The pins .venv's packages do not meet."""
    done = runner([str(python), "-c", MISSING, *wanted], capture_output=True, text=True)
    if done.returncode:
        return list(wanted)
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]


def offline(wheels):
    """pip's options for installing from the wheels in a folder alone, or none."""
    return ["--no-index", "--find-links", str(wheels)] if wheels else []


def install(python, arguments, *, runner=subprocess.run, wheels=None):
    command = [str(python), "-m", "pip", "install", "--no-input", "--disable-pip-version-check", *offline(wheels), *arguments]
    if runner(command, cwd=ROOT).returncode:
        where = "the wheels Setup.exe carries" if wheels else "PyPI"
        raise SetupStopped(f"pip stopped before NPC Forge's packages were installed from {where}; its message is above. "
                           + ("Run Setup.exe again." if wheels else "Check the internet connection, then run setup.bat again: "
                              "it continues where it stopped."))


def setup(root=ROOT, *, wheels=None, runner=subprocess.run, make=None, say=print, python_version=None):
    """Make .venv or bring its packages up to requirements.txt. Returns what was done, in a sentence."""
    root = Path(root)
    wanted = pins(root / "requirements.txt")
    environment = root / ".venv"
    unfinished = environment / UNFINISHED
    python = venv_python(root)
    if python is None:
        this = python_version or tuple(sys.version_info[:2])
        if this < OLDEST:
            raise SetupStopped(f"NPC Forge needs Python {OLDEST[0]}.{OLDEST[1]} or newer, and this is {this[0]}.{this[1]}. "
                               "Run setup.bat, which finds or fetches one.")
        if wheels and this != (3, 12):
            raise SetupStopped("The wheels Setup.exe carries are for Python 3.12; run this with the Python Setup installed.")
        say(f"Making .venv with Python {this[0]}.{this[1]} ({sys.executable}).")
        environment.mkdir(exist_ok=True)
        unfinished.write_text("setup.bat has not finished installing NPC Forge's packages here. Run it again to finish.\n",
                              encoding="utf-8")
        (make or venv.EnvBuilder(with_pip=True).create)(environment)
        python = venv_python(root)
        if python is None:
            raise SetupStopped("Python made .venv without a python.exe in it. Delete the .venv folder and run setup again.")
        install(python, [PIP], runner=runner, wheels=wheels)
        install(python, ["-r", str(root / "requirements.txt")], runner=runner, wheels=wheels)
        unfinished.unlink(missing_ok=True)
        return "Made .venv with NPC Forge's packages."
    version = venv_version(python, runner=runner)
    if version is None:
        raise SetupStopped(f"The Python in {environment} does not start; the Python it was made from may have been removed. "
                           "Delete the .venv folder and run setup again: it makes a new one.")
    lacking = missing(python, wanted, runner=runner)
    if not lacking and not unfinished.exists():
        return "The .venv folder already has every package requirements.txt pins; nothing changed."
    # The wheels fit Python 3.12 only. A .venv made earlier from another Python gets its packages from PyPI instead.
    use = wheels if version == (3, 12) else None
    if wheels and use is None:
        say(f"The .venv folder was made with Python {version[0]}.{version[1]}, and the wheels Setup.exe carries are for 3.12, "
            "so its packages come from PyPI.")
    say(f"Installing into .venv: {', '.join(lacking) or 'the packages of an interrupted setup'}.")
    install(python, ["-r", str(root / "requirements.txt")], runner=runner, wheels=use)
    unfinished.unlink(missing_ok=True)
    return f"Brought .venv up to requirements.txt: {', '.join(lacking) or 'finished an interrupted setup'}."


def main(argv=None):
    parser = argparse.ArgumentParser(description="Make NPC Forge's .venv, or bring its packages up to requirements.txt.")
    parser.add_argument("--wheels", type=Path, help="install from the wheels in this folder, without the internet")
    args = parser.parse_args(argv)
    folder = onedrive_folder(ROOT)
    if folder and not args.wheels:
        print(f"This folder is inside OneDrive ({folder}). OneDrive would upload every character you make here and can hold "
              "files open while it syncs them. A folder OneDrive does not sync, such as C:\\npc-forge, works better.")
    try:
        print(setup(ROOT, wheels=args.wheels))
    except SetupStopped as exc:
        print(exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
