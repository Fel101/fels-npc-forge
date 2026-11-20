# DramaBox local evaluation — October 13, 2026

Native integration subsequently passed on October 13; see
[DramaBox in Forge](voice-dramabox.md) for controls, installation and verification.
The measurements below describe the earlier isolated evaluation.

DramaBox successfully loaded and generated real speech on this workstation's
Windows/Radeon AI PRO R9700 runtime. This is an isolated audition, with four
performance cases compared against Qwen, Breeze and Chatterbox Turbo. That evaluation
did not register a production adapter or change character voice assignments.
Human listening determines whether its delivery and identity consistency suit the NPC.

## Evidence and runtime

All local evidence and downloaded assets are retained under
`local/dramabox-eval-20261013/`:

- `preflight.json`: real AMD NF4 quantization/dequantization/matrix execution and
  upstream imports, not a tone-worker test.
- `models.json`: exact snapshot revisions, file sizes and checked SHA-256 values.
- `runtime.json`: package versions and their read-only source environments.
- `trial.json`, `trial.log`: four real DramaBox performances, prompts, timings,
  waveform checks, watermark application and unchanged source hashes.
- `comparison.json`: twelve real performances from the existing three adapters,
  using Forge's normal voice runtime, memory gate and residency coordination.
- `verification.json`: sixteen raw audio checksums, mono timing bindings,
  normalized listening copies and protected source checks passed.
- `review.html`, `review-desktop.png`, `review-mobile.png`: listening comparison.
  Browser playback checked; 1280/390 CSS pixel layouts have no horizontal overflow.

The dedicated `env/` interpreter is Python 3.12.10. Its package overlay reads the
existing `.venv-fqwen` and `.venv` environments without changing their packages.
Only bitsandbytes 0.50.2, PyAV 19.0.1 and SentencePiece 0.2.2 were installed into
the new environment, with dependency resolution disabled to protect AMD pins.
The trial uses Torch 2.12.0+rocm7.14.0, HIP 7.14.60850 and Transformers 5.15.1.
bitsandbytes loaded its actual `libbitsandbytes_rocm714.dll` successfully.

Pinned source: [resemble-ai/DramaBox](https://github.com/resemble-ai/DramaBox/tree/a70a5818e103c1c9fef22409c1e0c707ebf4f8a7).
The upstream checkout remains clean. Model revisions:

- `ResembleAI/Dramabox`: `404f967f653fa1170dc15a9d1ddd3fdb9a0a842d`.
- `unsloth/gemma-3-12b-it-bnb-4bit`: `826e729dbaeea4ecb143738eed2bcf3539ebf7bf`.

Only these pinned model assets were downloaded into the trial directory. Their
selected files total approximately 16.36 GB. An interrupted initial Xet transfer
left partial files in the trial cache; normal HTTP completed the checked downloads.
Those partial files are expendable trial artifacts, not active models.

## Generation and timing results

Each engine used an exact copied Clara Kowalski approved 8.5-second reference
(SHA-256 `23c6e62537dcc6b349b26c18978c7b55b22b4fdabdb723386dbc51df292859d8`).
DramaBox conditions on its actual 8.5-second length rather than padding/repeating
it. This is shorter than the upstream suggested ten-second reference and limits
the conclusion about cloning quality. The source NPC record/reference and protected
runtime files stayed unchanged; the reference never left this machine.

DramaBox uses BF16, the prequantized NF4 Gemma encoder, seed 42, CFG 2.5 and STG 1.5.
Compilation and RE-USE denoising are disabled. The upstream warm `generate` method
is used; Perth watermarking is applied explicitly. SoundFile writes the native
float WAV instead of installing TorchCodec for `torchaudio.save`. A separate
PCM16 mono delivered copy feeds Forge alignment, retaining exact audio hashes.
GPU use follows the shared ComfyUI operation lock, Forge memory gate and Lab
residency lease. Model loading and generation run offline with explicit paths.

| Case | Native audio | Generation after startup | Forge timing result |
|---|---:|---:|---|
| Neutral | 7.69 s | 23.27 s, including first generation warmup | Aligned words/English phones |
| Whisper | 7.69 s | 9.88 s | Fallback: independent boundaries disagree |
| Angry | 7.37 s | 9.77 s | Fallback: forced timing could not fit speech |
| Amused/chuckle | 8.97 s | 9.86 s | Fallback: ASR transcript differs |

Startup took 24.08 seconds. Peak Torch allocated memory was 17.46 GiB and reserved
memory 18.52 GiB; these counters exclude GPU driver/other-process overhead.
The amused case's ASR omitted "Oh" and repeated "that"; this is an ASR observation,
not a human-confirmed transcription. Green alignment status remains a checked
model estimate, not proven phonetic accuracy or a voice quality rating.

The existing engine comparisons preserve the same authored text. Breeze receives
supported direction; Qwen receives none; Turbo uses its chuckle tag for the amused
case and no unsupported freeform direction. Thus each engine exercises its actual
capabilities, rather than falsely promising identical acting controls. Four of the
twelve baseline clips passed full phone timing. Failed checks stay visible.

Players use independent FFmpeg listening copies matched to -18 LUFS, with a -1 dB
true-peak target. Downloads retain raw WAVs. First baseline times include model
startup; they are not directly comparable to warm DramaBox generation. Four short
cases do not establish long-form speaker stability, seed determinism or preferred
acting. No machine speaker-similarity score or subjective winner is asserted.

## Open or rerun the audition

From the repository, run the retained `env/Scripts/python.exe` with
`local/dramabox-eval-20261013/review.py --serve`. It prints a loopback URL; the server
serves only review audio/timing/report files and supports audio byte ranges.
`review.html` can also be opened directly; a loopback server gives normal playback.
The current listening page is `http://127.0.0.1:61670/` while its helper runs.

Retained scripts: `download.py`, `preflight.py`, `trial.py`, `comparison.py`,
`verify.py`, `review.py`. Read their exact pins and settings before deliberately
rerunning. `download.py` retrieves only public pinned files and verifies weights.
Generation scripts reuse existing references; do not run an upstream CUDA
requirements resolver in Forge or any working Voice Lab environment.

## Integration decision and licenses

Local compatibility is established for this tested configuration. The next
integration decision depends on listening to the cloned identity, acting and exact
words. A native adapter should keep authored dialogue separate from quoted stage
prompts, expose only measured controls, capture source/model/runtime bindings, and
retain failed-alignment fallback. This trial does not claim those production adapter
features exist for DramaBox yet. Forge's seven installed engines remain available.

DramaBox uses the [LTX-2 Community License](https://github.com/resemble-ai/DramaBox/blob/a70a5818e103c1c9fef22409c1e0c707ebf4f8a7/LICENSE);
the Gemma encoder has its separate Gemma license. Preserve both notices and terms
in any integration; do not label the combined pipeline Apache/MIT without conditions.
RE-USE is separately non-commercial and was not downloaded or enabled. This local
evaluation neither distributes weights nor uploads NPC references to cloud notebooks.
