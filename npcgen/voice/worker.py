"""A voice engine worker. NPC Forge starts one with the engine's own Python (see runtime.py):

    <engine python> -u npcgen/voice/worker.py --engine qwen

and talks to it over stdin/stdout, one JSON object per line each way:

    -> {"id": 1, "cmd": "design", "text": "...", "instruct": "...", "language": "English", "seed": 7, "out": "C:/.../x.wav"}
    <- {"id": 1, "ok": true, "seconds": 2.1, "audio_seconds": 4.3, "sample_rate": 24000}
    <- {"id": 1, "ok": false, "error": "..."}

Commands: ping, load, design, clone, stock, voices, unload. There is no network port: when NPC Forge closes the pipe (or is
killed) stdin ends and the worker exits, so it can never be left holding the GPU on its own.

Everything a model library prints goes to stderr (NPC Forge writes it to a log file): the protocol owns stdout. Only this file
and adapters/ run in the engine's environment; they need numpy and the engine's own packages, nothing from NPC Forge.
"""
import argparse
import importlib
import json
import os
import pathlib
import sys
import time
import traceback
import wave

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _protocol_stdout():
    """A private handle on the real stdout for replies; fd 1 is pointed at stderr so library prints cannot corrupt them."""
    fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return os.fdopen(fd, "w", encoding="utf-8", buffering=1, newline="\n")


def write_wav(path, samples, rate):
    import numpy as np
    x = np.clip(np.asarray(samples, dtype=np.float32).reshape(-1), -1.0, 1.0)
    pcm = (x * 32767.0).round().astype("<i2")
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm.tobytes())
    os.replace(tmp, path)
    return len(x) / float(rate)


def handle(engine, req):
    from npcgen.voice import binding
    cmd = req.get("cmd")
    if cmd == "ping":
        return {"engine": engine.name, "device": getattr(engine, "device", "")}
    if cmd == "load":
        frozen=req.get('_runtime_binding')
        if frozen:
            binding.verify(frozen,engine.name,force=True)
            if pathlib.Path(sys.executable).resolve()!=pathlib.Path(frozen['python']).resolve():
                raise ValueError('Worker Python differs from the queued runtime')
            if any(os.environ.get(key)!=value for key,value in frozen['env'].items()):
                raise ValueError('Worker environment differs from the queued runtime')
            engine._forge_binding=frozen
        if engine.name=='drama3' and req.get('cpu_only'):
            engine.runtime._select_device(cloning=True)
        engine.load(req.get("parts") or [])
        return {}
    if cmd == "unload":
        engine.unload()
        return {}
    if cmd == "voices":
        return {"voices": engine.voices()}
    if cmd not in ("design", "clone", "stock"):
        raise ValueError(f"unknown command {cmd!r}")
    frozen=getattr(engine,'_forge_binding',None)
    if frozen:binding.verify(frozen,engine.name,cmd)
    t0 = time.perf_counter()
    production=engine.name=='drama3' and req.get('_studio') is True
    if production:
        preview=pathlib.Path(req['out']).with_suffix('.pcm');preview.parent.mkdir(parents=True,exist_ok=True)
        with preview.open('wb') as stream:
            def emit(pcm,rate):
                if rate!=44100:raise ValueError('Drama preview rate changed')
                stream.write(pcm);stream.flush()
            samples,rate=engine.production(req['text'],req.get('_studio_references',[]),req.get('_studio_description',''),
                req.get('instruct',''),req.get('params') or {},emit)
    elif cmd == "design":
        samples, rate = engine.design(req["text"], req["instruct"], req.get("language") or "English", int(req.get("seed") or 0),
                                      req.get("params") or {})
    elif cmd == "clone":
        samples, rate = engine.clone(req["text"], req["ref"], req.get("ref_text") or "", req.get("language") or "English",
                                     int(req.get("seed") or 0), req.get("instruct") or None, req.get("params") or {})
    else:
        samples, rate = engine.stock(req["text"], req["voice"], float(req.get("speed") or 1.0), req.get("language") or "English",
                                     req.get("params") or {})
    seconds = time.perf_counter() - t0
    if production:
        with wave.open(req['out'],'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(rate)
            with preview.open('rb') as stream:
                while chunk:=stream.read(65536):wav.writeframesraw(chunk)
        audio_seconds=preview.stat().st_size/2/rate
    else:
        audio_seconds = write_wav(req["out"], samples, rate)
    if audio_seconds <= 0:
        raise RuntimeError("the model produced no audio")
    identity=getattr(engine,'model_identity',None)
    if engine.name in ('qwen','breeze','kokoro','chatterbox'):
        controls={}
        params=req.get('params') or {}
        if engine.name in ('qwen','breeze'):controls['temperature']=float(params.get('temperature',.9))
        if engine.name=='breeze':controls['cfg']=float(params.get('cfg',4.)) if cmd=='design' or req.get('instruct') else 1.
        if engine.name=='kokoro':controls['speed']=max(.5,min(2.,float(req.get('speed') or 1.)))
        identity={**(identity or {}),'effective_controls':controls,'seed_requested':params.get('seed',req.get('seed'))}
        if engine.name=='chatterbox':identity['native_watermark']='Perth'
    if frozen:identity={**(identity or {}),**binding.receipt(frozen,cmd)}
    return {"seconds": round(seconds, 2), "audio_seconds": round(audio_seconds, 2), "sample_rate": int(rate),
            "model_identity": identity}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    args = ap.parse_args()
    out = _protocol_stdout()
    sys.stdin.reconfigure(encoding="utf-8")
    try:
        adapter = importlib.import_module(f"npcgen.voice.adapters.{args.engine}")
        engine = adapter.Engine()
    except Exception as e:
        traceback.print_exc()
        out.write(json.dumps({"id": 0, "ok": False, "fatal": True, "error": f"{type(e).__name__}: {e}"}) + "\n")
        return 1
    out.write(json.dumps({"id": 0, "ok": True, "ready": True, "engine": args.engine,
                         'pid':os.getpid(),'parent_pid':os.getppid()}) + "\n")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        rid = None
        try:
            req = json.loads(line)
            rid = req.get("id")
            reply = {"id": rid, "ok": True, **handle(engine, req)}
        except Exception as e:
            traceback.print_exc()
            reply = {"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}"}
        out.write(json.dumps(reply) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
