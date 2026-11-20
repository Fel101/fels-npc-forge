# Queued voice models and local capabilities

New dialogue, audition/test, batch and scene jobs bind their inference operations
when submitted. The private durable job stores the selected Python, safe worker
environment settings, cached model directories and SHA-256 manifests. It does not
store authentication variables. Qwen VoiceDesign and Base are separate operations;
CustomVoice and Kokoro are stock identities; Turbo and Original remain distinct.

Defaults select the verified Voice Lab snapshot revisions. Qwen overrides must be
existing local folders of the supported 1.7B model type. Breeze uses its actual local
bf16 model/tokenizer directory; Kokoro loads explicit config, weights and embedding
files; Turbo uses its local constructor. CustomVoice, Original and VoxCPM retain
their previously verified fixed checkpoint loaders. No cache refs, runtime pins,
installed packages, model weights or vendor sources are edited by this workflow.

Before loading, the worker rehashes the captured dependencies and checks its Python
and environment. Reused workers must match the binding ID; a different model/runtime
binding starts a fresh worker using the normal GPU gate and Voice Lab residency
lease. Changed or missing captured dependencies stop generation with an explicit
error. Submit a new job after deliberately changing a model/runtime; retry preserves
the earlier job's binding. Warm checks use file size/mtime/ctime hash caches; model
load checks bypass that cache. This protects queued inputs and does not assert
bit-identical output across GPU/library changes.

Runtime manifests cover the Python executable, Forge worker/adapter/shared setup,
relevant backend Python sources, package metadata and applicable Voice Lab sources.
Model manifests cover inference weights, tokenizer/config files and speaker
embeddings. They are dependency manifests, not hashes of every installed system
DLL or executable on the workstation. Effective controls and model binding receipts
are stored with generated audio and retained in voice/scene packages. Public receipts
contain checkpoint/revision and model/runtime/adapter digests, not private model paths.

Changing current configuration while a job waits does not retarget it. Previously
persisted jobs lacking a binding bind at execution and report `execution_legacy`;
they cannot retroactively acquire a queue-time guarantee. Existing generated WAVs
retain their original receipts. Effects-only edits can reuse the checked dry master
without an available inference environment or a new model load.

The engine API reports cached operation availability. The designer and scene voice
pickers offer Kokoro embeddings that actually exist in the selected checkpoint;
automatic batch matching uses the same set. Existing recordings remain playable
if an embedding is later missing, but regeneration reports that missing dependency.
No automatic download or substitute speaker is used. The audited local snapshot
has 11 of the 28 advertised English Kokoro embeddings. Experimental Qwen/VoxCPM clone
direction controls are hidden; verified numeric controls remain exposed. The Voice
Lab may provide other models, conversion or streaming interfaces that Forge does
not expose; these are not advertised as native Forge features.

## Verification

`scripts/check_voice_runtime_bindings_real.py` uses the normal queue in an isolated
library and reads a retained real 8.5-second reference. It assigns one approved voice
version to two actors, generates dialogue, holds the actual ComfyUI operation lock
while changing runtime config, then checks the delivered binding receipts. It also
voices a pinned scene, renders MP4 and verifies exported WAV/receipt metadata.
`--designs` additionally checks the three reference-design operations. Results and
representative audio/scene previews stay under ignored `local/voice-runtime-real-*`.

This is generation, queue-binding, identity reuse and export evidence. Listening
quality, accents and phonetic accuracy remain for human review; failed alignment
checks keep the visible amplitude fallback.

Final real evidence:

- `local/voice-runtime-real-20261013-034626-300e69/report.json`: Qwen, Breeze,
  VoxCPM2, CustomVoice and Original; ten dialogue WAVs, ten pinned scene WAVs,
  five scene MP4/ZIPs, ten voice ZIPs and three additional design takes. Current
  runtime configuration was replaced while each first actor waited, and the
  captured model/runtime still generated the line. Retained reference bytes stayed
  unchanged. All five reports passed. Short CustomVoice clips and one Breeze clip
  failed the independent alignment checks and correctly kept amplitude fallback.
- `local/voice-runtime-real-20261013-035830-9ebd56/report.json`: final Kokoro/Turbo
  checks, four dialogue WAVs, four pinned scene WAVs, two MP4/scene ZIPs and four
  voice ZIPs. Kokoro's audition worker was released before the config-change check,
  so the queued binding was verified through a cold worker start as well as warm
  identity reuse. Source reference/record hashes stayed unchanged.
- API/UI: seven installed adapters report operation/checkpoint availability;
  11 cached Kokoro English speakers are offered. Qwen/Vox clone mood controls are
  disabled. Assigned stock/upload voices no longer acquire a false unsaved badge
  from an unused description suggestion; automatic Kokoro speech is described
  accurately as embedding selection, with audition media retained for playback.

Final selected regression: **171 passed**. A server queue timeout during concurrent real GPU QA passed in the focused 42-test server run and the complete repeat after workers finished. Native browser measured 1440/390 CSS pixels with no page overflow; screenshots are retained under the first runtime QA directory.

An independent copy of committed d27ffbb passed **165 checks** without the unrelated
working changes (the six additional working-checkout checks belonged to other work).
Final UI follow-up also gates dialogue and scene mood selectors by the effective
voice engine, including pinned scene snapshots and stock overrides. Browser verified
12 disabled dialogue selectors and two disabled pinned-scene selectors for Turbo;
eight workspace/web checks passed after this follow-up. Other parameter and visual
expression controls remain independent of speech mood support.
