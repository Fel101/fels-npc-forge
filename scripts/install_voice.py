"""Install a voice engine for NPC Forge's voice designer: its own Python environment, PyTorch for your GPU, the engine's
packages, its model files, and the "voice" entry in config.json that tells NPC Forge where it is.

    python scripts/install_voice.py qwen                 # Qwen3-TTS (recommended first: voices from a description, fast)
    python scripts/install_voice.py kokoro chatterbox    # several at once
    python scripts/install_voice.py all --torch cpu      # everything, CPU only (slow, but no GPU needed)
    python scripts/install_voice.py qwen --use-existing C:/path/to/venv/Scripts/python.exe   # a Python you set up yourself

Environments go to voice-envs/<engine>, models to voice-models/ (both git-ignored). Each engine is separate because they pin
different library versions (the stock Qwen package wants transformers 4.57, faster-qwen3-tts 5.15+, Chatterbox 5.2).

--torch picks the PyTorch build: auto (CUDA if nvidia-smi works, else CPU), cuda, rocm (AMD, see below) or cpu.
On Windows with an AMD card, rocm uses AMD's own multi-arch wheels (repo.amd.com); name your card's architecture with
--gpu-arch (gfx1201 = Radeon RX 9070 / AI PRO R9700, gfx1100 = RX 7900). A PC with two AMD GPUs (a CPU with graphics too)
sets --hip-device to the big one's index.

What was tested: the ROCm/Windows path on a Radeon AI PRO R9700 (the numbers in npcgen/voice/engines.py) and the CPU path.
The CUDA path follows PyTorch's published instructions but was not run on an NVIDIA card here.
"""
import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ENGINES = ["qwen", "breeze", "kokoro", "chatterbox"]
BREEZE_FILES = ["loader.py", "native.py", "runtime.py", "int8.py"]
TORCH_VERSION = {"rocm": ("2.12.0+rocm7.14.0", "2.11.0+rocm7.14.0")}
AMD_INDEX = "https://repo.amd.com/rocm/whl-multi-arch/"
SPACY_EN = "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"

PACKAGES = {
    "qwen": [["faster-qwen3-tts==0.4.0", "transformers==5.15.1", "soundfile", "huggingface_hub"]],
    "breeze": [["transformers>=5.3", "accelerate", "numpy", "safetensors", "soundfile", "huggingface_hub", "tqdm"]],
    "kokoro": [["kokoro", "soundfile", "misaki[en]", SPACY_EN, "huggingface_hub"]],
    # Chatterbox pins torch 2.6.0, so it goes in without its dependencies and they are listed here (gradio left out)
    "chatterbox": [["--no-deps", "chatterbox-tts==0.1.7"],
                   ["s3tokenizer", "transformers==5.2.0", "diffusers==0.29.0", "resemble-perth>=1.0.0", "conformer==0.3.2",
                    "safetensors==0.5.3", "librosa==0.11.0", "pyloudnorm", "omegaconf", "numpy<2", "soundfile", "huggingface_hub",
                    "spacy-pkuseg", "pykakasi==2.3.0"]],
}


def run(cmd, **kw):
    print("  $ " + " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def venv_python(env_dir):
    return env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def pick_torch(choice):
    if choice != "auto":
        return choice
    return "cuda" if shutil.which("nvidia-smi") else "cpu"


def pip(py, args, uv):
    if uv:
        run([uv, "pip", "install", "--python", py, *args])
    else:
        run([py, "-m", "pip", "install", *args])


def install_torch(py, torch, arch, uv):
    constraints = None
    if torch == "cuda":
        pip(py, ["torch", "torchaudio", "--index-url", "https://download.pytorch.org/whl/cu128"], uv)
    elif torch == "cpu":
        pip(py, ["torch", "torchaudio", "--index-url", "https://download.pytorch.org/whl/cpu"], uv)
    elif torch == "rocm" and os.name == "nt":
        tv, tav = TORCH_VERSION["rocm"]
        # AMD's index answers 403 for packages it does not host (fatal for uv): torch from AMD, the rest from PyPI
        pip(py, ["--default-index", "https://pypi.org/simple", "--index", AMD_INDEX, f"torch[device-{arch}]=={tv}",
                 f"rocm[libraries,device-{arch}]==7.14.0", f"torchaudio=={tav}"], uv)
        constraints = [f"torch=={tv}", f"torchaudio=={tav}"]
    elif torch == "rocm":
        pip(py, ["torch", "torchaudio", "--index-url", "https://download.pytorch.org/whl/rocm6.4"], uv)
    else:
        raise SystemExit(f"Unknown --torch {torch}")
    return constraints


def install_breeze_code(env_dir, source):
    """The Breeze TTS 2 inference code, copied as a plain package `breeze` (no ComfyUI needed).

    It comes from the Apache-2.0 ComfyUI node Saganaki22/ComfyUI-Breeze-TTS-2 (loader.py, native.py, runtime.py, int8.py,
    vendor/). That repository was taken down in September 2026 and the ComfyUI registry flags its last version, so this
    script does not download it: point --breeze-code at a copy you have and trust (the folder holding loader.py, or its
    parent holding breeze/loader.py). Read it first; the one used to measure NPC Forge's numbers contained no network or
    process calls beyond the Hugging Face weight download."""
    if not source:
        raise SystemExit("Breeze TTS 2 needs its inference code: pass --breeze-code <folder with loader.py> (see the README, "
                         "Voices). Its original repository is no longer online, so it is not downloaded.")
    src = pathlib.Path(source).resolve()
    if not (src / "loader.py").is_file() and (src / "breeze" / "loader.py").is_file():
        src = src / "breeze"
    missing = [f for f in BREEZE_FILES if not (src / f).is_file()] + ([] if (src / "vendor").is_dir() else ["vendor/"])
    if missing:
        raise SystemExit(f"{src} is not the Breeze inference code (missing {missing})")
    code = env_dir / "code"
    target = code / "breeze"
    target.mkdir(parents=True, exist_ok=True)
    for name in BREEZE_FILES:
        shutil.copy2(src / name, target / name)
    shutil.copytree(src / "vendor", target / "vendor", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    (target / "__init__.py").write_text("", encoding="utf-8")
    (target / "vendor" / "__init__.py").touch()
    return code


def engine_env(engine, env_dir, models, hip_device, breeze_code):
    env = {"HF_HOME": str(models / "hf"), "HF_HUB_DISABLE_TELEMETRY": "1"}
    if hip_device is not None:
        env["HIP_VISIBLE_DEVICES"] = str(hip_device)
    if engine == "breeze":
        env["BREEZE_CODE"] = str(breeze_code)
        env["BREEZE_MODELS"] = str(models / "breeze")
    return env


def download_models(engine, py, env, models):
    """Start the engine's worker online once and load its models: the adapters download exactly what they use."""
    from npcgen.voice.runtime import Worker
    print(f"  downloading the {engine} models (several GB for qwen and breeze; this can take a while)", flush=True)
    worker = Worker(engine, str(py), {**env, "HF_HUB_OFFLINE": "0"}, models / f"install-{engine}.log")
    try:
        worker.wait_ready()
        parts = {"qwen": ["design", "clone"], "breeze": ["design"], "kokoro": ["stock", "all_voices"], "chatterbox": ["clone"]}[engine]
        worker.call({"cmd": "load", "parts": parts}, 7200)
    finally:
        worker.close()


def write_config(engine, py, env, path=None):
    path = pathlib.Path(path) if path else ROOT / "config.json"
    cfg = json.loads(path.read_text("utf-8")) if path.exists() else {}
    voice = cfg.setdefault("voice", {})
    voice.setdefault("idle_minutes", 3)
    voice.setdefault("engines", {})[engine] = {"python": str(py), "env": {**env, "HF_HUB_OFFLINE": "1"}}
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"  {path.name}: voice engine {engine} -> {py}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("engines", nargs="+", help="qwen, breeze, kokoro, chatterbox or all")
    ap.add_argument("--torch", default="auto", choices=["auto", "cuda", "rocm", "cpu"])
    ap.add_argument("--gpu-arch", default="gfx1201", help="AMD architecture for --torch rocm on Windows (default gfx1201)")
    ap.add_argument("--hip-device", type=int, default=None, help="AMD GPU index to use when the PC has two")
    ap.add_argument("--python", default=sys.executable, help="the Python 3.12 that creates the environments")
    ap.add_argument("--dir", default=str(ROOT / "voice-envs"), help="where the environments go")
    ap.add_argument("--models", default=str(ROOT / "voice-models"), help="where the model files go")
    ap.add_argument("--no-models", action="store_true", help="skip the model download (they download on first use instead)")
    ap.add_argument("--use-existing", metavar="PYTHON", help="write config.json for a Python you already set up, install nothing")
    ap.add_argument("--breeze-code", metavar="DIR", help="the Breeze TTS 2 inference code (see install_breeze_code)")
    ap.add_argument("--config", default=str(ROOT / "config.json"), help="the config file to write (default: NPC Forge's config.json)")
    a = ap.parse_args()
    engines = ENGINES if a.engines == ["all"] else a.engines
    unknown = [e for e in engines if e not in ENGINES]
    if unknown:
        raise SystemExit(f"Unknown engine {unknown}; choose from {ENGINES} or all")
    models = pathlib.Path(a.models).resolve()
    if a.use_existing:
        for engine in engines:
            code = pathlib.Path(a.breeze_code or pathlib.Path(a.dir) / engine / "code").resolve()
            if (code / "loader.py").is_file():            # the adapter wants the folder that holds breeze/
                code = code.parent
            env = engine_env(engine, None, models, a.hip_device, code)
            write_config(engine, pathlib.Path(a.use_existing), env, a.config)
        return 0
    torch = pick_torch(a.torch)
    uv = shutil.which("uv")
    print(f"PyTorch build: {torch}{' (' + a.gpu_arch + ')' if torch == 'rocm' else ''}; installer: {'uv' if uv else 'pip'}")
    for engine in engines:
        print(f"\n== {engine} ==", flush=True)
        env_dir = pathlib.Path(a.dir).resolve() / engine
        py = venv_python(env_dir)
        if not py.is_file():
            run([uv, "venv", "--python", a.python, env_dir] if uv else [a.python, "-m", "venv", env_dir])
        constraints = install_torch(py, torch, a.gpu_arch, uv)
        cfile = None
        if constraints:
            cfile = env_dir / "constraints.txt"
            cfile.write_text("\n".join(constraints) + "\n", encoding="utf-8")
        for group in PACKAGES[engine]:
            pip(py, (["-c", cfile] if cfile else []) + group, uv)
        breeze_code = install_breeze_code(env_dir, a.breeze_code) if engine == "breeze" else None
        env = engine_env(engine, env_dir, models, a.hip_device, breeze_code)
        if not a.no_models:
            download_models(engine, py, env, models)
        write_config(engine, py, env, a.config)
    print("\nDone. Restart NPC Forge; the voice designer lists the installed models.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
