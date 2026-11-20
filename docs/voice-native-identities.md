# Preset identities and Chatterbox Original

## CustomVoice without a cloning reference

In **Voice Designer**, select **Qwen 1.7B CustomVoice**, then a speaker under
**Stock voice**. Choose a language and **Save settings**. This assigns a native
model preset immediately; making or selecting an audition take is optional.
Each audition uses that same speaker with a new seed. Speech renders directly
through CustomVoice, including dialogue and scene lines.

The pinned cache currently supplies Serena, Vivian, Uncle Fu, Ryan, Aiden,
Ono Anna, Sohee, Eric and Dylan. Its loaded model confirms these speakers and
Auto plus ten languages. The preset identity records the checkpoint revision,
speaker and a version ID. An audition WAV is display media; it is never passed
as a cloning reference to CustomVoice or required for rendering.

**Save voice to library** creates an immutable identity version even without
audio. Approve it and explicitly assign it to one or more characters. Character
history can restore older preset versions. **Scenes → voice version → Pin preset**
captures the identity, pronunciation rules, performances and speaking settings.
Later speaker changes leave the scene pin unchanged. Recording/upload workflows
can deliberately replace a preset with a separate cloning identity.

Voice packs carry `preset_identity`, `identity_version` and model receipts in
`lines.json`. A reference-free pack has no required `voice.wav`. If a chosen audition
is included, `file_role: audition` distinguishes it from conditioning media.
Importers must render new speech through the recorded preset model; they must not
turn its audition into a cloning reference automatically.

**Pronunciation & performances** offers acting direction, temperature, top-k,
top-p, repetition penalty and maximum speech tokens. Numeric overrides are
validated; integer controls require whole numbers. The model still determines
how closely it follows a direction. Native speed, sound tags and arbitrary voice
design/cloning are not exposed for this checkpoint.

## Original cloning

**Chatterbox Original** is separate from **Chatterbox Turbo**. Select Original
under **Speaks lines with** for an existing assigned recording, or upload/copy a
reference and select Original. It supports English cloning at 24 kHz. Saved
performances offer temperature, exaggeration, voice guidance (CFG), top-p,
minimum probability and repetition penalty. Freeform acting direction, native
speed and Turbo sound tags are withheld. When switching models, revise incompatible
performances with **Remove unsupported controls** before generating new speech.

Original loads the exact cached checkpoint through `from_local`; CustomVoice
reuses the Lab's pinned faster-Qwen worker. Both participate in Forge's existing
ComfyUI gate and the shared Lab residency lease. Discovery checks local interpreters,
source markers and required cached assets. No environments or model packages are
installed, upgraded or downloaded by these workflows.

## Verified local runtimes

| Adapter | Reused environment | Checkpoint and revision |
|---|---|---|
| CustomVoice | Voice Lab `.venv-fqwen`, faster-qwen3-tts 0.4.0 | `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`, `0c0e3051f131929182e2c023b9537f8b1c68adfe` |
| Original | Voice Lab `.venv`, chatterbox-tts 0.1.7 | `ResembleAI/chatterbox`, `5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18` |

Existing AMD runtime pins are preserved. These checks establish the configured
local GPU path; other hardware support is not claimed. Actual generation receipts
record revisions, adapter hashes and effective controls. The subsequent
[runtime-binding milestone](voice-runtime-bindings.md) now freezes queued model,
source and safe runtime settings and verifies them before model load. The
October 12 evidence below remains a historical snapshot of this identity milestone.

## October 12 evidence

- 94 regression checks passed across voice, native identities, reusable library,
  authoring, comparison, GPU ownership, APIs, scenes and web modules/controller.
  One legacy Chrome smoke was excluded in favor of the browser checks below.
  Coverage includes cloning/preset batch transitions, optional auditions never
  conditioning CustomVoice, recording replacement and missing cached assets.
- `local/customvoice-real-20261012-160341-f95fb0/report.json`: two characters assigned
  the same approved Ryan identity, with no reference WAV created or consumed;
  24 kHz clips of 2.8 and 4.0 seconds, cold/warm generation 39.16/2.28 seconds.
  The loaded model confirms nine speakers and eleven language choices. Direction
  and nondefault sampling appear in its receipts. Two pinned scene lines, a
  130-frame/25 fps MP4, voice ZIPs and a scene ZIP were checked. The scene remains
  fresh after one character changes to Aiden.
- `local/voice-studio-real-20261012-160625-5bca7f/report.json`: Original reused one
  approved cloning reference across two isolated characters; 24 kHz clips of
  5.16/5.10 seconds, cold/warm generation 82.50/8.55 seconds. Nondefault exaggeration
  0.65, CFG 0.45 and sampling were confirmed. Two pinned scene lines, a
  140-frame/25 fps MP4 and voice/scene ZIPs were checked. Production Clara's record
  and reference bytes remained unchanged.
- Browser QA in a separate library covered preset save/approval/two-character
  assignment, scene pins, incompatible-control removal and persisted CustomVoice
  and Original performances. Desktop and 390-pixel mobile screenshots are retained
  in `local/voice-workspace-qa-20261012/` as `customvoice-library-*`,
  `customvoice-scene-pin-desktop.png` and `original-controls-*`.

Human listening remains pending. The scene portraits are placeholder cards and the
mouth track is still an amplitude fallback; these checks do not establish word,
phoneme or perceptual identity accuracy. [Reversible effects](voice-effects.md) are
now implemented; validated alignment, visemes,
synchronized character previews and Godot/Unreal delivery remain in the active goal.
