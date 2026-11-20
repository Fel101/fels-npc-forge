"""Create the dedicated CPU aligner; reuse an existing pinned Whisper cache offline."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    folder = ROOT / 'local/.venv-align'
    executable = folder / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if not executable.is_file():
        subprocess.run([sys.executable, '-m', 'venv', str(folder)], check=True)
    subprocess.run([str(executable), '-m', 'pip', 'install', '--only-binary=:all:', '-r', str(ROOT / 'requirements-voice-alignment.txt')], check=True)
    check = subprocess.run([str(executable), '-c',
        "import importlib.metadata as m,json; import pocketsphinx,faster_whisper; print(json.dumps({p:m.version(p) for p in ['pocketsphinx','faster-whisper','ctranslate2']}))"],
        capture_output=True, text=True, check=True)
    receipt = {'python': str(executable), 'versions': json.loads(check.stdout), 'device': 'cpu', 'inference_downloads': False}
    (folder / 'runtime.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps(receipt, indent=2))
    print('Reuse the pinned Systran/faster-whisper-tiny snapshot from your local cache. This installer does not download model weights.')


if __name__ == '__main__':
    main()
