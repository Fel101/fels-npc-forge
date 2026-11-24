# NPC Voice Studio goal

> This page records earlier Forge voice experiments. The current workflow is in [Voices](voices.md).

Current guide update, October 20, 2026: the original October 13 completion details
below describe that milestone's services and adapters at that time. DramaBox and
[Fish Drama 3](voices.md) were added afterward; see the [current designer
capability map](voice-designer-tab.md#integration-scope). Historical PIDs and
measurements are not live service status. Missing weights/runtimes still require
separate setup; all Drama cloning and production-studio jobs remain CPU-only.

The full scope remains nine feature areas, including aligned lip sync. Work builds
on the native Voice Designer commit `01b4b3b`. Local milestone commits include only
this goal's work; unrelated 3D/Body Designer work stays untouched.

**Completed October 13, 2026.** The final manual restart is verified on port 8765
(PID 16720). All seven installed adapters report runtime-binding schema 1; Kokoro
offers its 11 cached English voices. Library, authoring, effects, alignment, game
exports and both character rig capability schemas are loaded. The native Voice
Designer and existing Clara Kowalski Lip sync dialog opened successfully without
changing assignments or lip-sync settings. Production still lists 845 characters.

Retained validation includes 171 selected regression checks, 165 checks from an
independent committed-source copy, real generation and pinned scenes across seven
adapters, and actual Godot/Unreal imports. Physical microphone permission/hardware
and human listening/phonetic quality remain manual review limits. Models with only
a jaw cannot supply independent facial shapes; Unreal jaw integration remains the
documented artist Blueprint recipe. These limits are explicit in the feature docs.

The final local evidence is `local/voice-runtime-audit-20261013/completion-audit.json`
and `production-lip-sync.png` in that same directory. Sections below record earlier
milestones and their historical remaining work; the final production gate is resolved.

| Requirement | Current evidence and remaining work |
|---|---|
| Reusable named voice library, search, tags, favorites, multiple assignments | Implemented in `voicelibrary.py`, library APIs and `voice-library-view.js`; unit/API/browser checks cover saving, approval and two-character assignment. |
| Immutable versions, approval, restoration, dialogue/scene pins | Immutable files, DB version triggers, approval events, NPC history, pinned assignments, frozen dialogue jobs, scene-specific reference/settings/authoring pins and export IDs/checksums implemented. Full checkpoint/runtime revision bindings are implemented and verified across seven adapters. |
| Reference editor, recording, waveform, trimming, transcript, provenance/quality | Waveform, region playback, derived versions, recording upload, microphone UI, transcripts, origin/consent, length/RMS/clipping checks implemented. Synthetic-file upload and trimming checked in browser. Physical microphone recording still needs human device/permission QA. |
| Performance presets and model-supported per-line direction | Saved named presets/defaults, dialogue/scene selection, effective adapter controls and frozen queued inputs implemented. Supported controls exercised with real speech; unsupported direction is disabled for the effective engine. Human perceptual quality remains a manual review. |
| Pronunciation dictionary and audio invalidation | Case-insensitive whole-word/phrase rules, synthesis-text previews, authored-text preservation, targeted freshness signatures, frozen job input and export provenance implemented and checked. Older clips without full snapshots retain documented compatibility limits. |
| Same-script candidate comparisons, ratings, favorites, take selection | Retained same-script/language sessions, human stars/listening notes/favorites and explicit assignment implemented; candidates protected from pruning/deletion. Unit/API/browser checks pass. |
| VoxCPM2, Qwen CustomVoice, Chatterbox Original; capability discovery and GPU coordination | All three integrated with pinned local checkpoints and model receipts. Vox native CFG/steps, reference-free CustomVoice identity versions/direction/sampling and Original numeric cloning controls checked with real dialogue/scenes/exports. Shared Lab/Forge residency lease and Forge/ComfyUI gate retained. Cached operation/stock discovery and checksum-bound queue-time checkpoint/runtime inputs are implemented and checked for all seven adapters; see [Voices](voices.md). |
| Reversible creature/robotic effects and preserved originals | CPU Creature/Robot presets, pitch with preserved/pitch-linked formants, filters, distortion, modulation and layering implemented. Paired previews, performance overrides, frozen scenes, dry masters, effect-only reuse and original/delivery exports verified with real prior speech. Independent formant scaling is unavailable in the installed backend. See voice-effects.md. |
| Word/phoneme alignment, visemes, synchronized previews and Godot/Unreal mappings | Final-audio hash-bound, independently checked word/English phone timing, explicit fallback, configurable 15-group visemes, immutable audio preview and synchronized timing diagram, voice/scene JSON and game playback artifacts implemented. Godot4.7.2 mesh/sprite adapters and Unreal5.8.2 CurveTable/SoundWave imports verified on real retained speech and pinned scene clips; editable target maps documented in voice-game-exports.md. Character-specific portrait/sprite frame maps, immutable versions/restoration, audio-clock browser previews and native CPU scene frame animation implemented and verified on copied real NPC artwork (voice-character-mouths.md). Existing 3D morph/jaw mappings, immutable model snapshots, native stage/MP4 capture and a real-model Godot modifier are implemented (voice-mesh-mouths.md); model intervals are estimates, not manually proven phonetic accuracy. See voice-lip-sync.md. |

## First library/reference milestone

Open **Voice Designer → Voice library** or **Save voice to library** on an assigned
character. Save a named voice or append a version to an existing one; add tags,
notes, and favorites. Audition a version, approve it, then explicitly assign it to
one or more checked characters. New versions never update those characters by
themselves. Choose an approved earlier version as the library default, or restore
an earlier character reference from **Reference history**.

The reference editor displays a waveform, clipping/RMS/sample-rate diagnostics,
trim bounds, region playback, and an exact transcript. Save a trim or transcript
correction as a new draft; its parent WAV and provenance remain intact. Normalizing
a derived version is opt-in. Uploaded/recorded audio is decoded by the browser into
16 kHz mono PCM WAV before sending; those submitted WAV bytes are retained unchanged
as the new library recording version. Source files on disk are untouched. Microphone
permission is requested only by pressing **Record microphone**; stop/close releases
the tracks, and recording stops at 40 seconds.

Each library is local to the configured NPC library:

- `.voice-library/voices.sqlite3`: names, tags, favorites, immutable version snapshots,
  approval events, preferred-version IDs.
- `.voice-library/versions/<version>.wav`: immutable reference media, with SHA-256.
- `<npc>/voice/references/<reference>.wav`: independent pinned character references.
- `<npc>/voice/reference.wav`: compatibility copy for existing importers.

Back up the whole configured library with Forge stopped, including hidden files.
Copy the DB and version media together. This milestone does not migrate the separate
Voice Lab Cast database or change model environments/cache/AMD pins. It creates no
production voice-library entries automatically. Legacy mutable references are pinned
when a new dialogue/test request is submitted; queued jobs then use that frozen
reference and settings, even if the character's active voice changes.

Reference quality measurements are diagnostics rather than ASR, speaker similarity,
or a perceptual quality rating. Cloning assignments enforce the local reference-length
policy; warnings remain visible for drafts that need trimming or a longer sample.

## Verification retained on October 12, 2026

- 59 Python checks passed across voice, reusable-library, API, scenes, workspace,
  syntax/import checks; the legacy headless-Chrome smoke was excluded in favor of
  in-app browser verification. Nine Node workspace regressions are invoked there.
- Eight library integrity tests cover source independence, approval, multiple-NPC
  assignment, immutable DB records, trims, media checksums, restoration, frozen queued
  dialogue, and invalid/short references. API tests add actual request/export behavior.
- Browser: saved/favorited/approved a QA voice, assigned to two NPCs, created and
  approved a trimmed version, restored one character's older reference, and imported
  a synthetic recording as a draft. Desktop and 390-pixel mobile layouts checked;
  mobile screenshot: `local/voice-workspace-qa-20261012/voice-library-mobile.png`.
  New drafts preserve the existing library default. No production voice assignment was made.
- Real installed Qwen Base cloning produced two 24 kHz speech clips from one pinned
  reference version assigned to two isolated QA NPCs. Durations: 2.66 and 3.56 seconds;
  elapsed generation: 80.95 seconds including first load, then 2.80 seconds. Source NPC
  JSON and source reference bytes remained unchanged. These are generation/reuse checks;
  human identity/word-quality listening is pending.
- Real evidence: `local/voice-library-real-20261012-132540-da2158/report.json` and its
  two `library/*/voice/lines/greeting_1.wav` clips. UI fixtures remain under
  `local/voice-workspace-qa-20261012/`. All these files are ignored by Git.

## Authoring/comparison milestone

The workflow and effective-control table are in [voice-authoring.md](voice-authoring.md).
67 automated checks passed, followed by focused checks after freshness/UI refinements.
Browser verified dictionary preview/save, performance save, comparison/rating/favorite/
assignment and scene pin/performance generation in the isolated QA server.

Real Qwen verification with final synthesis signatures is retained in
`local/voice-studio-real-20261012-141301-ecef01/report.json`: two 24 kHz dialogue clips,
two pinned scene lines, actor voice ZIPs and a 5.6-second MP4/scene ZIP. Production source
JSON/reference were unchanged. Exports preserve `Aether` captions and `Ee ther` synthesis,
temperature 0.7 and reference IDs. The current scene timeline is fresh after an NPC rule
change. Human pronunciation/speaker listening remains pending; the mouth track is still
an amplitude fallback, with alignment/visemes remaining in scope.

Next milestone: additional Voice Lab engine/checkpoint integration, GPU coordination,
reversible effects, followed by alignment, visemes, previews and game-engine exports.

## First additional-engine bridge

[Voices](voices.md) documents VoxCPM2 discovery, control bounds,
the reused environments/source/checkpoint, shared residency lease, and current limits.
87 checks passed including GPU regression coverage; actual cross-process lock contention
and release were checked. Native UI persisted the nondefault CFG/step settings with
desktop/mobile screenshots. Real 48 kHz voice-reuse and pinned scene evidence is in
`local/voice-studio-real-20261012-144048-ae6f89/report.json` and its WAV/MP4/ZIP artifacts.
Actual receipts carry checkpoint/source revisions and effective CFG 2.5/steps 8.
The subsequent [native identity milestone](voices.md) adds CustomVoice
and Original, with real generation, pinned scenes and export checks. Complete model
pins, effects and aligned lip sync remain required.

## Live service status

The user closed the earlier server and relaunched `start.bat` on October 12. The
read-only probe on 8765 now finds PID15556 with `library_schema: 1`, `studio_schema: 1`,
`alignment_schema: 1`, all seven adapters and an available CPU alignment runtime.
The native Voice Designer and Lip sync dialog opened successfully. All 845 existing
characters remain listed; no production voice assignments were changed by QA.
The earlier manual-restart requirement is resolved. Feature mutations continue in
isolated libraries. Game playback exports added after this launch are verified in
isolated projects; a fresh backend loads their `game_export_schema: 1` capability.
Launching `start.bat` while a server is responsive reopens it instead of restarting it.

## Game playback export milestone

[voice-game-exports.md](voice-game-exports.md) documents exact-time JSON, Unreal
Constant CurveTable CSV, editable target maps, a Godot playback helper and an explicit
Unreal checked importer. Retained real speech and pinned scene WAVs import in installed
Godot4.7.2/Unreal5.8.2; both engines match weights at sample times, and Unreal also
matches interval midpoints. Godot applies shape keys and sprite frames, advances an
audio clock, seeks, holds on pause and rests on stop. SHA corruption and existing-asset
replacement are refused. Real evidence: `local/voice-game-export-real-20261012-213003-f5b480/report.json`.
The test mesh/atlas are synthetic fixtures. Real NPC browser rigs, native scene visemes,
remaining model/checkpoint bindings and human microphone/listening QA remain in scope.


## Character mouth-frame milestone

Mapped immutable portrait/sprite frames now animate the NPC itself in browser previews and CPU card scenes. Living talk/rest artwork provides a two-frame bridge; imported aligned images supply distinct mouth poses. Artwork edits leave voice identity and WAV signatures unchanged, and visual versions can be restored. Voice/scene ZIPs include checked PNGs and artist mappings; Godot loads the actual atlas and combines shared-image weights. Real evidence is local/voice-character-rig-real-20261012-224957-969d8c/report.json. Browser upload/map/restore/playback and measured1440/468 desktop/mobile layouts checked. Expression-asset fingerprint inputs are sorted, avoiding false video staleness across server restarts. Full goal remains active for model/checkpoint binding and capability audit.

## Existing 3D mouth-target milestone

[3D mouth controls](voice-mesh-mouths.md) discover actual GLB morph/joint names,
save checksum-bound immutable model/mapping snapshots, and drive the browser close-up,
native stage and deterministic MP4 capture through the same mapping path. Scene/voice
packages contain the exact model and artist config. Godot's SkeletonModifier3D helper
executes those jaw/morph controls after body animation; Unreal has an explicit documented
bone/morph recipe alongside the previously verified audio/CurveTable importer.

Real evidence: `local/voice-mesh-real-20261012-231710-14476b/report.json`.
The copied Briar model has 1,503 jaw-weighted vertices and no named facial morphs.
Godot4.7.2 verifies 169 real-WAV sample poses, audio-clock advance, seek, pause and
stop/rest. Browser 1.02s seek shows `open / OW / oh` on the actual close-up model.
The exported two-actor 5.2s scene has 130 frames and decoded audio correlation0.9993
with the planned beat mix at zero offset. Original NPC/model/WAV bytes remain unchanged.
Jaw-only opening is explicitly distinguished from independent phonetic lip shapes.
No production assignment, runtime pin change or software install was needed.

The all-adapter queue-time binding and capability audit is implemented; see [Voices](voices.md). The full goal remains **active** for the final live-service completion check. Physical microphone
and human speaker/phonetic quality reviews are not claimed. The latest game/character/mesh
backend additions postdate production PID15556; an eventual fresh backend loads those
capability flags. Port8765 remains available throughout isolated QA.


## Queue-bound runtime and capability milestone

All seven installed adapters now bind explicit cached models, Python/source/package
metadata and safe worker settings at submission. Both runtime and worker validate
dependencies; exports retain the binding receipt. Qwen VoiceDesign and Base have
separate snapshots. Kokoro/Turbo use explicit local loaders. Eleven cached Kokoro
English embeddings replace the previous 28 unverified choices; automatic matching
and scenes use the same discovery. Experimental clone direction is hidden.

Real final evidence: local/voice-runtime-real-20261013-034626-300e69/report.json
(Qwen/Breeze/Vox/Custom/Original) and local/voice-runtime-real-20261013-035830-9ebd56/report.json
(Kokoro/Turbo, including a cold worker after queued config mutation). Fourteen
dialogue WAVs and fourteen pinned scene WAVs, seven MP4/scene packages, fourteen
voice packages and three design takes are retained. Source reference/record hashes
stayed unchanged. Alignment failures keep explicit fallback. No human-quality claim.

Final selected regression:171passed after real model workers finished; an earlier
server queue timeout during concurrent GPU QA passed on focused retry (42 server
checks) and the complete repeat. Desktop1440/mobile390 native picker checked with
no page overflow. Only goal hunks in shared Jobs/server/CLI files are staged; all
other local changes remain. Production8765 was externally relaunched asPID9040 at
00:24 and exposes game/character/mesh schemas; its runtime-binding metadata is
still absent and its stock list is28. A fresh backend is the remaining live-service
step after local commit and full completion audit.
