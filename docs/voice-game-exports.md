# Voice lip sync in Godot and Unreal

Download a **Voice pack** from an assigned character or Voice Designer, or a rendered
scene's package. Fresh dialogue and delivered scene clips now include:

| File | Purpose |
|---|---|
| `game/clips/<line-or-beat>.json` | Exact WAV hash, sample count/rate, duration, status/reason and dense playback weights |
| `game/clips/<line-or-beat>.curves.csv` | Unreal CurveTable; rows are groups, column times are clip-relative seconds |
| `game/targets.json` | Editable mapping from Forge mouth groups to artist-created character targets |
| `game/godot/forge_lip_player.gd` | WAV hash check, audio clock, seek/stop, mesh shape and sprite adapters |
| `game/unreal/import_curves.py` | Explicit editor-only checked CurveTable importer; refuses asset replacement |
| `game/README.md` | Packaged integration instructions and Blueprint playback mapping |

`lines.json` / scene `sources.json` link each clip's `game_playback` artifacts.
Existing `lip-sync/*.json`, amplitude `mouth` values, authored dialogue, original
masters and WAVs remain in their documented locations. Each scene curve starts
with its beat's WAV; subtract the beat's timeline start when using a scene clock.

Godot and Unreal use Constant sample holding to match the browser timing diagram.
Stored rounded timestamps are authoritative; the Godot/browser helpers use a binary
search rather than `floor(time * fps)`, which can pick the previous sample at rounded
boundaries. Every group is cleared or updated each tick and all targets rest at stop
and after the WAV duration. Weight sums are normalized. An exported fallback has
only `sil` and `mouth_open`, drives the jaw, and retains its reason; it does not assert
phoneme or named-viseme timing. Missing amplitude entries rest rather than inventing
speech. Altered/nonfinite amplitude values are rejected.

The 15-group target names are a starting convention. Rename the blend shape/morph
targets in `targets.json` and Godot's Inspector/your Unreal Blueprint to match the
specific mesh. Silence has an empty morph target and a dedicated sprite rest frame.
For sprites, configure the frame index for each group in the helper or Blueprint.
If several groups share a target, sum and clamp to 1. These files provide no automatic
ARKit/MetaHuman retarget and create no facial shapes. A character needs an authored
mouth mesh or sprite atlas. Browser NPC-rig and native scene-viseme animation remain
in progress; the current Forge scene video still uses its amplitude renderer.

Follow the [packaged integration instructions](../npcgen/voice/game_templates/README.md)
for loading, seeking, pause/stop, Godot export include filters, Unreal CSV import and
Blueprint nodes. Preserve the delivered source WAV bytes for hash checks. Unreal's
imported SoundWave is an engine asset; retain the WAV beside JSON for reimport checks.
The importer is deliberately executed by the game project's author, never by Forge
or by opening a ZIP. It checks audio hash, CSV times and every weight against JSON.

## Installed-engine verification on October 12, 2026

`python scripts/check_voice_game_exports_real.py` uses retained real CustomVoice
speech/alignment and creates only ignored QA packs/projects. It changes no production
characters, source WAVs, TTS environments, game projects or AMD pins. The primary
passing report is `local/voice-game-export-real-20261012-213003-f5b480/report.json`.

- Godot **4.7.2 Mono**, headless with Dummy audio, loaded two 2.8/4.0-second aligned
  voice clips, one actual 2.8-second WAV in amplitude-fallback mode, and two pinned
  scene clips (1.26/1.36 seconds). It applied all exported frame values to an actual
  synthetic ArrayMesh with shape keys and a 15-frame AnimatedSprite2D. Audio clocks
  advanced; seek used absolute clip time; pause held it; stop cleared all shape keys
  and restored the rest sprite. A changed WAV was rejected by SHA-256.
- Unreal **5.8.2** imported all five CurveTables and five SoundWaves in an isolated
  headless project. All 8,692 group/sample values and every interval midpoint matched
  Constant interpolation; imported sound duration agreed within one source sample.
  A second import of the same asset was refused. Curve and audio assets are retained
  under the QA Unreal project's Content folder for inspection.
- Source NPC/scene JSON and WAV hashes, and delivered exported WAV hashes, were
  unchanged. No TTS or alignment inference ran in this checker. Source records are
  read; clip-specific settings are restored only in an in-memory export copy.

These checks prove serialization, engine imports and playback adapters. The mesh and
atlas are synthetic API fixtures; they do not prove the appearance of any user's NPC,
perceptual sync, manually accurate phoneme boundaries or MetaHuman compatibility.
Existing Godot 4.6 projects retain their own executable and are not migrated.

Regression checks: 146 passed with one legacy Chrome smoke deselected for in-app browser QA; 18 focused API/game-export/web checks passed after final UI/API additions. The real-engine checker then passed again after correcting Godot paused-audio pose handling.

Actual character frame atlases and [3D mesh/jaw controls](voice-mesh-mouths.md) are
now exported alongside these clips. The separate real-GLB check exercises the jaw
modifier on a copied Forge model in Godot; it does not extend the Unreal verification
to a generated facial AnimBlueprint.
