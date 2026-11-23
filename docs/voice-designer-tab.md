# Voice Designer tab

Updated October 20, 2026; the original tab milestone shipped October 12. Open NPC Forge on its configured local port (normally 8766).
The native Voice Designer tab sits between Items and Scenes. A character's Voice
card opens it with that character selected.

The subsequent library/reference milestone adds **Voice library**, immutable reference
history and queued render snapshots, approved reusable voices, and a waveform/recording
editor. See [the full voice-studio goal and validation](voice-studio-goal.md).

## Workflow

1. Find a saved NPC using the character search and voice-status filter.
2. Describe a voice, choose a stock voice, upload a reference, or copy another
   character's assigned voice. Model capabilities govern source and mood controls.
3. Generate candidate takes, listen, and choose **Assign to character**.
4. Audition a line, generate individual dialogue lines or the whole dialogue pack,
   and download the character's voice pack. Scenes use the same NPC voice record.

Voice settings are stored through the existing Forge APIs. Unsaved settings,
audition text and upload selections survive character/tab switches within the page;
save settings before reloading. Character and dialogue drafts in Create remain
independent. Queue updates refresh generated clips while preserving voice controls
and input focus. Pending requests cannot be redirected to another character.

## Integration scope

The current designer includes Qwen design/Base, Breeze, Kokoro, Chatterbox Turbo,
Qwen CustomVoice, Chatterbox Original, VoxCPM2, DramaBox and Fish Audio Drama 3,
subject to configured local runtime/checkpoint availability. It reuses the Lab's
installed environments/assets where configured; no separate running Lab service
is needed. Jobs use Forge's existing queue and ownership gate.

Reusable approved library versions, native preset identities, pronunciation and
performances, candidate comparisons, reversible effects, final-audio alignment,
character audition reviews and game delivery workflows are implemented. Current
guides: [studio overview](voice-studio-goal.md), [identities](voice-native-identities.md),
[authoring](voice-authoring.md), [effects](voice-effects.md), [auditions](voice-auditions.md),
[alignment](voice-lip-sync.md), [runtime bindings](voice-runtime-bindings.md),
[DramaBox](voice-dramabox.md) and [Drama 3](voice-drama3.md).

Drama's shared cast/narration studio supports streaming auditions and retained
mixed-audio packages; studio/cloning inference stays on CPU. Previous-take VQ
editing, isolated speaker stems and native word timing remain unavailable in
the pinned backend. Model and source-recording terms remain separate from app code.

The Lab Cast database and Forge voice library remain separate stores. Forge
records/exports are authoritative for Forge characters; Lab-specific conversion,
general blind evaluation and scene packages do not imply shared databases. The
validation below is the historical October 12 tab milestone, not fresh quality
approval of every subsequently added model.

## Validation

- 36 Python checks passed for voice behavior/APIs, workspace regressions, and JS
  syntax/import contracts. The separate legacy headless Chrome smoke was excluded;
  the running application was checked through the in-app browser instead.
- Nine Node controller regressions cover Create draft isolation, per-character
  settings/line retention, pending-save routing, copied voice controls, failed-save
  recovery, candidate submission, and queue updates.
- Browser checks used the running Forge library for navigation, character
  switching, model controls, loading an existing assigned voice, and unsaved edits.
  No production-library voice generation or assignment was performed.
- An isolated local library and the project's fake tone worker exercised candidate
  generation, assignment, directed audition, all 12 dialogue lines, persistence
  after reload, and a voice-pack ZIP with 13 WAV files and dialogue/line metadata.
  Tone-worker results verify plumbing; they do not assess speech quality.
- Desktop layout checked at 1440 × 1000; mobile at 390 × 844 had a document width of
  375 pixels without page overflow. Model controls disabled unsupported moods.

Retained local QA data, ZIP and preview image:
`local/voice-workspace-qa-20261012/` (ignored by Git). The temporary QA server was
stopped after verification; Forge on 8765 was left running.
