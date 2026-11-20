# DramaBox in NPC Forge

DramaBox is the eighth native Voice Designer engine. It can design an English
voice from a description or clone a reference, then use that voice for dialogue
and pinned scenes. Existing reusable voices, pronunciation rules, effects,
lip-sync analysis and game export packages work through Forge's normal queue.

## Use

Choose **DramaBox** in Voice Designer. Design takes or supply a 3–40 second
reference; about ten seconds is recommended. The first 30 seconds are used.
Review the actual take before assigning it. Accent control is unverified.

Open **Pronunciation & performances** to save acting direction and supported
guidance. Dialogue lines and scene beats can select those saved performances.

| Control | Default | Supported range |
| --- | --- | --- |
| Text guidance (CFG) | 2.5 | 1–5 |
| Acting guidance (STG) | 1.5 | 0–3 |
| Duration allowance | 1.1 | 0.8–1.5 |

There is no temperature or inference-step slider. Direction is kept outside
the quoted spoken text. Long prompts use upstream chunked generation. Audio
carries the native Perth watermark. Compilation and RE-USE denoising are disabled.

Expressive speech can fail word matching. Forge retains a visible amplitude
mouth-track fallback in that case; it does not report successful phoneme alignment.
The evaluation passed one of four DramaBox alignment cases. The final native
short-line QA retained fallback for both dialogue and scene speech.

## Local installation

The verified installation is retained in `local/dramabox-eval-20261013/`.
It contains `upstream/`, the private `env/Scripts/python.exe`, speech weights and
the NF4 Gemma text encoder. Keep this folder and its Voice Lab package overlays
when backing up the installation. Git contains the adapter and manifests,
not the large models or interpreter.

The engine defaults point to that installation. Discovery checks both models
and the source and does not install or download anything. Explicit overrides
are supported under `voice.engines.dramabox.python` and `.env` in `config.json`:
`DRAMABOX_SOURCE`, `DRAMABOX_MODEL_DIR`, `DRAMABOX_GEMMA_DIR` and
`DRAMABOX_SITE_OVERLAYS`. Missing assets disable generation with an installation
message. Restart the Forge backend after updating its Python code; launching
`start.bat` while the old server is responding can reconnect to that process.

| Component | Verified revision |
| --- | --- |
| resemble-ai/DramaBox source | a70a5818e103c1c9fef22409c1e0c707ebf4f8a7 |
| ResembleAI/Dramabox | 404f967f653fa1170dc15a9d1ddd3fdb9a0a842d |
| unsloth/gemma-3-12b-it-bnb-4bit | 826e729dbaeea4ecb143738eed2bcf3539ebf7bf |

Windows/Radeon AI PRO R9700 was verified using BF16 speech and real AMD NF4
kernels. Short warm evaluation clips took about ten seconds; peak Torch reserved
memory was 18.52 GiB. Forge gates admission at 23,000 MB free VRAM and 16,000 MB
free RAM, coordinating with ComfyUI and Voice Lab residency.

Queued jobs capture source, runtime, reference and both model identities before
execution. Receipts and export manifests retain the auxiliary Gemma identity,
effective guidance, performance prompt, watermark and license conditions.

## License conditions

The picker and receipts display conditional commercial use under the
[LTX-2 Community License](https://github.com/resemble-ai/DramaBox/blob/a70a5818e103c1c9fef22409c1e0c707ebf4f8a7/LICENSE)
and [Gemma terms](https://ai.google.dev/gemma/terms). Entities with annual revenue
of at least $10 million need a paid LTX license. Keep applicable notices and
observe use restrictions. RE-USE is not installed or enabled.

`data/dramabox-licences.json` ships the two license records. If the optional
license catalogue is present, run `python scripts/register_dramabox_licences.py`
to append those records; conflicting IDs are refused and repeated runs are
idempotent. The native conditional-use notice also works without that catalogue.

## Verification

The integration passed 209 relevant working-tree regression tests. A separate
copy of the staged source verifies that the integration works without unrelated
uncommitted Body Designer or licensing changes.

The real check uses two copied actors and an isolated library:

```powershell
python scripts/check_voice_runtime_bindings_real.py --engines dramabox --designs
```

Final evidence is retained in
`local/voice-runtime-real-20261013-142202-550d66/report.json`: two real dialogue
clips, two pinned-scene clips, a reference-free design take, CPU-rendered MP4,
two voice packs and a scene package. Nondefault CFG 2, STG 1 and duration 1
with saved quiet direction appear in receipts. The first queued job still
generated after the current interpreter and both model paths were deliberately
changed to unavailable locations, proving use of the captured binding. Original
character/reference hashes stayed unchanged. Browser QA checked the eight-engine
picker, conditional notice, native controls, pinned scenes and fallback labels.
