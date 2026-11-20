"""Read-only runtime discovery and lease lifecycle; no model install or GPU use."""
import copy
import json
import sys

import pytest

from npcgen.voice import authoring, engines, runtime


def test_voxcpm_discovery_requires_existing_environment_source_and_snapshot(tmp_path):
    lab = tmp_path / "lab"
    for rel in ("catalog", ".venv-m6-voxcpm/Scripts", ".venv-fqwen/Lib/site-packages/torch", "out/m6/source/src/voxcpm",
                "hf-cache/models--openbmb--VoxCPM2/snapshots/32279effe8c19989596f05d353d1447f51d9e915"):
        (lab / rel).mkdir(parents=True)
    (lab / ".venv-m6-voxcpm/Scripts/python.exe").write_text("test-only executable placeholder")
    (lab / "voxcpm_worker.py").write_text("# test-only source marker")
    (lab / "catalog/engines.json").write_text(json.dumps({"engines": [{"id": "voxcpm2", "runtime": {"python": ".venv-m6-voxcpm/Scripts/python.exe"}}]}))
    cfg = {"voice": {"lab_path": str(lab)}}
    assert not engines.installed(cfg, "voxcpm2")
    (lab / "hf-cache/models--openbmb--VoxCPM2/snapshots/32279effe8c19989596f05d353d1447f51d9e915/config.json").write_text("{}")
    before = copy.deepcopy(cfg)
    assert engines.installed(cfg, "voxcpm2")
    assert engines.engine_config(cfg, "voxcpm2")["env"]["HF_HUB_OFFLINE"] == "1" and cfg == before
    inferred = {"voice": {"engines": {"qwen": {"python": str(lab / ".venv-fqwen/Scripts/python.exe")}}}}
    assert engines.lab_root(inferred) == lab.resolve()
    assert not engines.installed({}, "voxcpm2")
    assert authoring.validate_performance({"parameters": {"cfg_value": 2.5, "inference_timesteps": 8}}, "voxcpm2")["parameters"]["cfg_value"] == 2.5
    with pytest.raises(ValueError, match="whole number"):
        authoring.performance({"parameters": {"inference_timesteps": 8.5}})
    with pytest.raises(ValueError, match="does not"):
        authoring.validate_performance({"parameters": {"temperature": .7}}, "voxcpm2")


def test_customvoice_and_original_discovery_require_complete_pinned_local_assets(tmp_path):
    lab = tmp_path / "lab"
    (lab / "catalog").mkdir(parents=True)
    entries = [{"id": model, "runtime": {"python": f"{folder}/Scripts/python.exe"}} for model, folder in
               (("qwen3-tts-1.7b-custom-voice", ".venv-fqwen"), ("orig", ".venv"))]
    (lab / "catalog/engines.json").write_text(json.dumps({"engines": entries}))
    for entry in entries:
        executable = lab / entry["runtime"]["python"]
        executable.parent.mkdir(parents=True)
        executable.write_text("test-only interpreter placeholder")
    cfg = {"voice": {"lab_path": str(lab)}}
    custom = lab / f"hf-cache/hub/models--Qwen--Qwen3-TTS-12Hz-1.7B-CustomVoice/snapshots/{engines.CUSTOM_REVISION}"
    custom.mkdir(parents=True)
    (custom / "config.json").write_text(json.dumps({"talker_config": {"spk_id": {"ryan": 0, "ono_anna": 1}}}))
    (lab / "qwen_worker.py").write_text("# test-only source marker")
    assert [v["id"] for v in engines.stock_voices("qwen_custom", cfg)] == ["Ryan", "Ono_Anna"]
    assert not engines.installed(cfg, "qwen_custom")
    for name in ("model.safetensors", "generation_config.json", "tokenizer_config.json", "vocab.json", "merges.txt",
                 "speech_tokenizer/config.json", "speech_tokenizer/model.safetensors"):
        path = custom / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test-only cached asset marker")
    assert engines.installed(cfg, "qwen_custom")
    original = lab / f"hf-cache/hub/models--ResembleAI--chatterbox/snapshots/{engines.ORIGINAL_REVISION}"
    original.mkdir(parents=True)
    for name in ("ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors", "tokenizer.json", "conds.pt"):
        (original / name).write_text("test-only cached asset marker")
    assert not engines.installed(cfg, "chatterbox_original")
    (lab / "chatterbox_support.py").write_text("# test-only support marker")
    assert engines.installed(cfg, "chatterbox_original")


class Lease:
    def __init__(self):
        self.held, self.foreign = False, False
    def acquire(self):
        if self.foreign:
            return False
        self.held = True
        return True
    def release(self):
        self.held = False


class Worker:
    fail_start = False
    created = 0
    def __init__(self, engine_id, *args):
        type(self).created += 1
        self.engine_id, self.running, self.hung = engine_id, True, False
        self.loaded = set()
    def alive(self):
        return self.running
    def wait_ready(self, cancel):
        if self.fail_start:
            raise RuntimeError("worker startup failed")
    def kill(self):
        self.running = False
    def close(self):
        if not self.hung:
            self.running = False


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(engines, "installed", lambda *args: True)
    monkeypatch.setattr(engines, "lab_root", lambda cfg: None)
    monkeypatch.setattr(runtime, "Worker", Worker)
    monkeypatch.setattr(runtime.picture_engines, "add_before_big", lambda fn: None)
    monkeypatch.setattr(runtime.VoiceRuntime, "_prepare_memory", lambda *args: None)
    Worker.fail_start, Worker.created = False, 0
    cfg = {"voice": {"idle_minutes": 0, "engines": {"qwen": {"python": sys.executable}}}}
    instance = runtime.VoiceRuntime(cfg, tmp_path)
    instance.lab_ownership = Lease()
    yield instance
    if instance.worker:
        instance.worker.hung = False
    instance.close()


def test_foreign_residency_blocks_start_and_idle_release_frees_own_lease(bridge):
    bridge.lab_ownership.foreign = True
    with pytest.raises(runtime.VoiceError, match="Voice Lab owns"):
        bridge._worker_for("qwen", None)
    assert Worker.created == 0
    bridge.lab_ownership.foreign = False
    worker = bridge._worker_for("qwen", None)
    assert bridge.lab_ownership.held
    bridge.release()
    assert not worker.alive() and not bridge.lab_ownership.held


def test_failed_start_releases_lease_but_hung_worker_retains_it(bridge):
    Worker.fail_start = True
    with pytest.raises(RuntimeError, match="startup failed"):
        bridge._worker_for("qwen", None)
    assert not bridge.lab_ownership.held
    Worker.fail_start = False
    worker = bridge._worker_for("qwen", None)
    worker.hung = True
    bridge.release()
    assert bridge.worker is worker and bridge.lab_ownership.held


def test_constructor_failure_does_not_leave_a_residency_lease(bridge, monkeypatch):
    def fail(*args):
        raise OSError("interpreter could not start")
    monkeypatch.setattr(runtime, "Worker", fail)
    with pytest.raises(OSError, match="interpreter"):
        bridge._worker_for("qwen", None)
    assert bridge.worker is None and not bridge.lab_ownership.held


def test_idle_disabled_still_releases_lease_after_worker_crash(bridge):
    worker = bridge._worker_for("qwen", None)
    worker.running = False
    bridge._reap_once()
    assert bridge.worker is None and not bridge.lab_ownership.held


def test_dead_worker_error_releases_lease_immediately(bridge, monkeypatch, tmp_path):
    def call(self, request, *args):
        if request["cmd"] == "load":
            return {}
        self.running = False
        raise runtime.VoiceError("worker exited during speech")
    monkeypatch.setattr(Worker, "call", call, raising=False)
    with pytest.raises(runtime.VoiceError, match="exited"):
        bridge._run_locked("qwen", {"cmd": "clone", "text": "Hello"}, tmp_path / "speech.wav", "clone", None)
    assert bridge.worker is None and not bridge.lab_ownership.held
