# Fish Audio Drama 3 in NPC Forge

Drama 3 is the ninth Voice Designer engine. It reuses Voice Lab's pinned Q4_K model and portable speech.cpp 0.8.10 Windows Vulkan runtime; no Forge-specific weight copy or ROCm package installation is required. Built with Fish Audio.

## Use

Choose **Fish Audio Drama 3** in Voice Designer. Describe a voice and make audition takes, or upload/copy a reference with its exact transcript. Pick and review a take before assigning it. The saved voice then works through Forge's normal dialogue, pinned scenes, saved performances, pronunciation, effects, CPU lip-sync analysis and export paths.

The model follows the language of the text. Forge offers all 83 installed language labels plus Auto; it does not translate the script. Voice and mood instructions are bracket cues, with experimental adherence. They may be ignored or spoken. Review the words, identity and delivery before approving a performance. Alignment or a successful WAV response is not human quality approval.

Saved performances expose temperature, top-k, top-p and maximum seconds per chunk. The normal candidate/line seed is retained, including zero. Native `[laughs]`, `[sighs]`, `[gasps]` and `[coughs]` are supported; Forge's neutral event markup is translated to Fish syntax. There is no CFG, speed, inference-step or native watermark control for this adapter.

## Installation and discovery

`voice.lab_path` (or the existing Qwen environment path) identifies Voice Lab. The defaults use its `.venv/Scripts/python.exe` only as the protocol/NumPy wrapper. Reference-free design computes on `Vulkan1`, the discrete R9700. Cloning computes on CPU and needs at least 26 GB free RAM; it can take several minutes. GPU cloning is disabled after a device-loss/Windows `0x116` reboot during acceptance on this workstation. The exact cause remains unconfirmed. No Torch/CUDA packages, graphics drivers or Windows timeout settings were changed.

From the Voice Lab directory:

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/install_drama3.py
```

The shared receipt is `out/drama3/installation.json`. Forge requires the completed model, runtime and receipt before enabling the picker. Missing assets disable design/cloning rather than triggering downloads. Explicit overrides are available under `voice.engines.drama3.python` and `.env` (`VOICE_LAB`, `DRAMA3_INSTALL_RECEIPT`). A backend restart is needed after changing Python code; refreshing the page rechecks installation readiness.

The model is `sakasegawa/drama-3-GGUF` revision `9a01354d04ab6eb521bc9cdfc56ab6824c474af2`, derived from Fish Audio Drama 3 source revision `e0a9218aa48888270f06ef1ed9ea67622c0e17d2`. The Q4_K file's SHA-256 is `2a57b14c9b208a2ff833ab7305b920ca8f154ad807004a52d96a86a90a55523e`. Complete pins and installer behavior are documented in Voice Lab `docs/DRAMA3.md`.

Queued runtime bindings capture the actual GGUF, license/NOTICE, receipt, native executable/DLLs and shared adapter source. Source and derivative identities, effective controls, reference information and runtime hashes survive into saved clips/exports. Config changes after enqueue do not silently switch the worker's model. Existing Forge/ComfyUI GPU admission and Lab residency ownership are retained. A Windows job kills the owned native process when its Python worker unloads, is cancelled, times out or exits.

## Scope and acceptance

The native runtime supports incremental PCM and up to 16 speaker tags. Forge's current character workflow generates one pinned voice per line and delivers complete WAVs. The new Drama studio adds multi-reference cast slots, longer single-request scripts and incremental audition playback. It exports a mixed take; the existing line-by-line game export paths are unchanged. Previous-take editing and training are not exposed.

Regression coverage: `tests/test_voice_drama3.py`, existing voice runtime/binding/authoring tests, and the Lab native-child ownership regression. The existing retained-library acceptance helper now accepts Drama 3 design testing:

```powershell
python scripts/check_voice_runtime_bindings_real.py --engines drama3 --designs
```

It uses an isolated library, retained reference audio and normal queue/binding/scene/export paths, preserving production characters. Results are recorded under ignored `local/voice-runtime-real-*/report.json`. The final run passed: `local/voice-runtime-real-20261018-171439-83f5d6/report.json`. Two dialogue clones, two pinned scene lines, voice/scene ZIPs, MP4, a reference-free design take, frozen queued bindings and unchanged source hashes were verified. Clones used CPU; design used Vulkan1. Native workers exited. Automated voice regressions: 107 tests passed. No human listening/identity/word-quality approval is asserted. The Lab report `docs/reviews/DRAMA3_INSTALL_2026-10-18.md` records the reboot workaround and full evidence.

## Terms

The model/derivative use the [Fish Audio Community License 1.0](https://huggingface.co/fishaudio/drama-3/blob/main/LICENSE). Revenue and hosted-service conditions apply; this is not an Apache/MIT model. The picker displays a dedicated Fish Audio notice and export receipts retain conditions and **Built with Fish Audio** attribution. No optional user-maintained licensing catalogue was rewritten.

## Character-production expansion

Open **Drama studio** in Voice Designer after restarting the backend. This uses the shared Voice Lab `drama3_production.py` contracts and a native Forge runner with normal GPU/residency admission and frozen runtime bindings. All studio inference uses CPU. Long jobs are retained under the library `.voice-drama3/`; cancel/reconnect/immutable version selection and WAV/receipt/ZIP exports are supported. See Voice Lab `docs/DRAMA3_EXPANSION.md` for the API, boundaries and previous-take editing research. Browser playback uses incoming audio rather than waiting for the completed WAV.
