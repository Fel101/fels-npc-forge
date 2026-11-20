# Voice Lab runtime bridge

The first engine bridge adds **VoxCPM2 (Voice Lab)** to Forge's native model and
speaking-engine selectors. It reuses the existing `.venv-m6-voxcpm` interpreter,
read-only `.venv-fqwen` ROCm wheel overlay, pinned M6 source, and cached model. No
environment, package, model cache, reference or Voice Lab database is upgraded.

Forge discovers the Lab from `voice.lab_path` when configured, otherwise from the
existing Qwen interpreter's project root. Discovery requires the Lab catalog,
interpreter, source, ROCm overlay and pinned checkpoint to exist. An explicit
`voice.engines.voxcpm2` configuration takes precedence; its environment must include
`VOICE_LAB`. Missing assets are reported as unavailable; this path does not install
or download them. Model loading stays offline.

## VoxCPM2 behavior

- Description design uses the pinned worker's parenthesized description convention.
- Cloning reads Forge's immutable, checksummed NPC WAV; it does not apply the Lab
  Cast's separate reference-root list. The transcript is retained as provenance but
  is not passed as continuation conditioning in ordinary clone mode.
- Native output remains 48 kHz before Forge's normal trim/loudness processing.
- Saved performances offer **guidance strength (CFG)** (`cfg_value`, 0.1–10) and
  whole-number **inference steps** (`inference_timesteps`, 1–100). Defaults are 2
  and 10. A real nondefault 2.5/8 profile was forwarded and confirmed by the worker.
- New freeform performance direction is withheld. Existing mood previews are
  explicitly experimental parenthesized clone prefixes, with no adherence score.
- Forge currently offers English/Chinese metadata; the adapter does not force a
  language token. Language follows the submitted words. Broader language discovery,
  continuation controls and per-design native parameter controls remain to be added
  during the complete engine/capability pass. Design candidates currently use worker
  defaults; these controls apply to saved performances on speech.
- Model receipts on takes, tests, dialogue and scene speech include checkpoint,
  checkpoint revision, source revision, runtime fingerprint, Forge-adapter hash,
  effective native controls and reference checksum. Delivery/source manifests carry
  those receipts. Full queue-time model/revision bindings across every adapter remain
  in the larger goal; the receipts describe the actual generation performed.

Pinned source: `f772e498a45fbb5fb8e13fbf9b9c48be9fe33e69`.
Checkpoint: `openbmb/VoxCPM2`, revision `32279effe8c19989596f05d353d1447f51d9e915`.
The reused worker requires its verified R9700 ROCm path; CPU or other GPU support
is not claimed. Code/weights are recorded as Apache-2.0 in the local Lab inventory;
that does not establish rights to a particular reference speaker or output.

## Model ownership

When Forge detects this Lab, real Forge voice workers acquire the same OS byte
lock used by the Lab broker at `out/runtime-locks/model-residency.lock`. Forge keeps
its lease while its worker remains resident, including idle time, and releases it
after the process exits. Failed starts and dead-worker errors release it immediately;
the monitor also reaps crashed workers when idle unloading is disabled. A worker that
fails to stop retains it. The file's existence is never treated as ownership. Fake tone workers
do not use the GPU and do not acquire this residency lease.

The existing Forge/ComfyUI operation lock, GPU gate and memory checks remain in
place. Forge does not forcibly stop a Lab service's worker: a competing lease
returns a readable message to unload that service's model or wait for its idle
unload. Lab brokers configured with a different residency path must use this shared
path to participate in the same lease; GPU memory checks remain an additional guard.

Changing speaking adapters may leave incompatible saved performance fields. The
editor lists them and offers **Remove unsupported controls**, an explicit edit,
before accepting new effective settings.

## October 12 verification

- 87 Python checks passed, including GPU gate, lease lifecycle, discovery, voice
  library, authoring, API, scenes and JS syntax/controller checks. One legacy Chrome
  smoke was excluded in favor of in-app browser QA. An earlier parallel smoke exposed
  inappropriate residency acquisition by the no-GPU tone fixture; that path was fixed
  and the full suite rerun successfully.
- The actual Lab byte-lock implementation rejected a second Python process while
  held and admitted it after release, using an isolated temporary lock file.
- Native browser QA selected VoxCPM2, saved speaking settings, explicitly removed
  incompatible temperature/direction fields, saved CFG 2.5/steps 8 and reloaded those
  settings. Desktop 1440×1000 and mobile 390×844 checks had no horizontal overflow.
  Screenshots: `local/voice-workspace-qa-20261012/voxcpm-controls-{desktop,mobile}.png`.
- Real generation reused one approved voice version across two isolated characters,
  produced two 48 kHz clips (3.98/3.12 s), then two scene lines and an MP4/scene ZIP.
  Cold/warm generation was 57.72/4.55 s in that run. The model receipt confirms CFG
  2.5/steps 8, the requested seeds, pinned checkpoint/source, adapter hash, and matching
  original-reference hash. Production source JSON/reference bytes stayed unchanged.
- Evidence: `local/voice-studio-real-20261012-144048-ae6f89/report.json`, actor voice ZIPs
  and `scene-package.zip`. The MP4 is under
  `library/.npcforge-scenes/scene-711923e6e6/render/5614f005e74d.mp4` there.

Human listening remains pending. The mouth track is an amplitude fallback; this is
generation, reuse, control/provenance and export evidence, not validated word or
phoneme alignment.

Qwen CustomVoice reference-free identities and Chatterbox Original are now integrated;
the [native identity workflow](voice-native-identities.md) records their controls,
pins and real generation/export evidence. [Reversible effects](voice-effects.md)
are implemented. Richer checkpoint bindings and
aligned lip sync remain in the active full goal. Production Forge on 8765 is kept
available; close its existing server window before launching `start.bat` to load a
new backend. The launcher reuses an already-responsive server.
