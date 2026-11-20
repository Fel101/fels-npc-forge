# Reversible voice effects

Open **Voice Designer → Voice effects**. Choose **Creature**, **Robot** or **Dry /
original**, then adjust pitch, formants, filtering, distortion, modulation or the
second voice layer. Preview an existing reference, take, speech test or dialogue
clip with side-by-side original/processed players. Preview files never replace
the assigned reference or generated dialogue. An edit clears the previous preview.

Save the studio settings to use these defaults in new speech tests, dialogue and
scenes. A saved performance can **Override voice effects for this performance**;
choose a dry override when one line should sound normal. Dialogue and scene
performance selectors apply those overrides along with their native acting controls.
Scene pins preserve the effect settings independently of later character changes.

Changing an effect marks affected delivery audio outdated. Regenerate the affected
line using its ordinary **Redo/Voice** control. If only effects changed and a verified
dry master is available, Forge reprocesses it on the CPU without a model call.
Changing words, pronunciation, native settings or identity requires new synthesis.
Reapplying effects always starts from the dry master, never the previous processed
output. Disabling effects restores that master byte-for-byte. Redo with unchanged
settings still requests a new synthesis; it is not silently treated as an effect edit.

## Effective local controls

The installed FFmpeg 8.1.1 full build supplies the required CPU filters. Availability
is checked through the engine catalog; missing filters produce a readable error.
No audio runtime, model environment, package or AMD pin is installed or changed.

| Control | Behavior and bounds |
|---|---|
| Pitch | -12 to +12 semitones, Rubber Band pitch shift with tempo 1. |
| Formants | Preserve the original formants or shift them with pitch. Independent formant scaling is unavailable. |
| High-pass / low-pass | 0 disables a filter; up to 2,000/20,000 Hz. Cutoffs are limited to 45% of the delivered sample rate and the receipt records both requested and applied values. |
| Distortion | 0–1 drive through oversampled tanh soft clipping. Final normalization limits peaks. |
| Robot modulation | 0 disables; up to 200 Hz sinusoidal ring modulation. |
| Second layer | Mix a separate copy of the same dry voice at level 0–1, pitch -12 to +12 semitones, delay 0–100 ms. This does not synthesize another speaker. |

Effects accept mono delivery WAVs at 8–192 kHz, up to 120 seconds when enabled.
The dry bypass retains longer speech. Output remains at the original rate and exact
sample count. There is no effect-induced retrimming or time scaling. Backend frame
counts are recorded; if necessary, output is padded/cropped to the source length.
The final amplitude mouth track is computed from the processed audio.

## Originals, receipts and exports

New speech tests, dialogue and scene lines preserve their dry master under the
speaker's `voice/originals/<sha256>.wav`. This is the existing trim/loudness-normalized
delivery before effects, rather than the raw untrimmed model tensor. Masters are
content-addressed and checksum-checked before reuse. Existing references and candidate
takes remain conditioning/identity media, unaffected by these delivery effects.
Previewing an older clip without a dry master explicitly labels its source as an
existing clip; it does not pretend to recover an unknown original.

Metadata separates `synthesis_signature` from `render_signature`, and retains original
file/hash/rate/sample count plus `postprocessing` requested/applied settings, FFmpeg
version, filter graph, source/output hashes and exact frame counts. Existing model
receipts and seeds are retained during an effect-only edit. Export verifies delivery
and dry-master checksums.

Voice ZIPs include delivered `lines/*.wav` and `originals/<sha256>.wav`; `lines.json`
maps both and records the processing receipt. Scene ZIPs now include delivered
`audio/<beat>.wav` and dry originals alongside the MP4, with paths/receipts in
`sources.json`. The existing render-outdated flag still distinguishes current source
audio from an older video. Originals and recipes permit reprocessing, not mathematical
inversion of a distorted waveform. Explicit deletion of a character/voice retains
the existing deletion behavior; export/back up needed originals beforehand.

New speech metadata explicitly labels `lip_sync.status: amplitude_fallback` and binds
that mouth track to the delivered audio hash. Dialogue shows **Amplitude mouth track**.
[Final-audio alignment and viseme timing](voice-lip-sync.md) now provides checked
English timing data with fallback. Character-specific animation remains in the full
goal; the amplitude track itself does not establish phonetic timing.

## October 12 verification

- 100 automated checks passed, with the legacy Chrome smoke excluded in favor of
  in-app browser QA. Effects checks measure actual pitch/modulation/layer changes,
  exact sample counts, bounded controls, applied cutoff receipts, dry restoration,
  checksum failures, effect-only reuse, new synthesis for changed pronunciation,
  scene pins and actual HTTP preview/save behavior.
- Browser selected Creature, edited pitch, previewed paired audio, saved/reloaded
  defaults, saved a dry performance override and previewed Robot. Desktop and
  390-pixel mobile dialogs have no horizontal overflow. Screenshots:
  `local/voice-workspace-qa-20261012/voice-effects-{desktop,mobile}.png`.
- Real prior Chatterbox speech was copied to a fresh QA library and processed;
  no new inference ran. Robot and Creature clips stayed at 24 kHz and 123,840 /
  122,400 samples (5.16 / 5.10 seconds). Dry restoration was byte-identical and
  repeated processing reproduced the same output. Source QA records stayed unchanged.
- Two pinned scene lines were processed and rendered to a 1280×720, 140-frame,
  25 fps MP4 with 5.6 seconds of AAC audio. Pins stayed fresh after a character
  effect edit. Voice/scene ZIP delivery and original hashes were checked.
  Final evidence: `local/voice-effects-real-20261012-164751-cd27a1/report.json`.

Human listening remains pending. Tone tests verify DSP/routing, and real speech
artifacts support human review; neither proves perceptual quality or aligned lip sync.
