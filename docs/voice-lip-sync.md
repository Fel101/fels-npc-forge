# Final-audio alignment and viseme timing

This first alignment milestone adds checked word timing, English phoneme timing,
configurable visemes and a synchronized timing diagram. Character-specific mouth
rigs, scene animation and Godot/Unreal mappings are still being integrated. Existing
scene videos continue to use the amplitude mouth renderer; timing data in their
packages does not mean those videos have acquired phonetic mouth shapes.

## Workflow

Open **Voice Designer → Lip sync** for a character with existing speech. Enable
word/phoneme lip sync and save its settings. New tests, dialogue and scene speech
then analyze their final delivered WAV, after effects. Existing tests/dialogue can
use **Update saved clip timing** without regenerating or changing audio. References
and candidate takes can be analyzed for preview; they are not reassigned or edited.

**Analyze speech timing** previews the selected clip with the current draft settings.
The audio player and numeric preview-time control drive a viseme diagram from the
audio's actual playback position. Play, pause, seek and end-of-audio reset use the
same clock. The diagram illustrates timing and generic mouth groups; it does not
represent a particular NPC mesh. Changing settings removes the older preview.
Preview audio is an immutable content-addressed copy, so later dialogue rerenders
cannot silently replace the speech being reviewed.

Set a frame rate from 24–120 fps and a transition blend from 0–120 ms. Blend windows
are limited to half each cue so short phonemes can reach their target. The ARPABET
mapping editor chooses among the 15 target groups `sil, PP, FF, TH, DD, kk, CH, SS,
nn, RR, aa, E, ih, oh, ou`. These are portable timing/group identifiers, not a claim
that every Forge character has matching mesh targets or sprites.

Invented English spoken tokens may need explicit ARPABET phonemes. The optional
editor guides alignment only; the pronunciation dictionary still controls synthesis.
Overrides use a private dictionary without modifying the pinned base dictionary.
Both base and effective dictionary hashes are retained. Poor fits still fall back.

## Runtime and trust checks

Install the dedicated CPU environment with:

```powershell
python scripts/install_voice_alignment.py
```

It creates `local/.venv-align` using the pinned `requirements-voice-alignment.txt`.
Existing Voice Lab, Qwen, Chatterbox, Vox and AMD environments are untouched. The
installer does not download model weights, and inference never downloads them.
The default model is the existing user cache at
`.cache/huggingface/hub/models--Systran--faster-whisper-tiny/snapshots/d90ca5fe260221311c53c58e660288d3deb8d356`.
Set `voice.alignment.python` and `voice.alignment.whisper_model` for a relocated
installation. The model still must match the committed asset checksums.

Verified Windows/Python 3.12 pins are faster-whisper 1.2.1, CTranslate2 4.8.2 and
PocketSphinx 5.0.4. PocketSphinx 5.1.1 had no compatible Windows binary on the package
index during setup; the 5.0.4 wheel's alignment methods were verified directly.
Whisper and the bundled English dictionary/acoustic assets are checksum-bound in
`alignment-models.json`; actual versions, assets and worker hashes accompany results.

Whisper performs unprompted CPU/int8 recognition with voice activity detection.
PocketSphinx then runs word alignment and a separate phone pass. Their documented
APIs are in [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and
[PocketSphinx alignment](https://pocketsphinx.readthedocs.io/en/stable/pocketsphinx.html#pocketsphinx.Decoder.set_alignment).
No transcript prompt is fed into the independent recognizer.

Acceptance requires:

- Matching spoken tokens, allowing explicit English spelling variants, dictionary
  phonetic equivalents and known contractions. There is no fuzzy text editing.
  Contractions share an independent recognition-group boundary; an interior boundary
  is not falsely reported as independently measured by Whisper.
- Mean recognized-word probability at least 0.5, minimum at least 0.15, and forced
  word/group boundaries within 350 ms of the independent recognition boundaries.
- Finite, ordered, positive intervals inside the exact delivered audio duration.
  Phones must cover each word and use supported symbols. Viseme cues cover the
  whole clip, frames follow the configured rate and finite weights sum to one.
- Exact final-WAV hash, sample count/rate, transcript hash and settings signature.
  Changed audio/text/configuration cannot ship older alignment as current data.

These are operational validation gates; passing them does not establish manually
annotated phoneme accuracy. Human timing/listening review remains necessary. The
350 ms guard is a rejection limit, not an accuracy guarantee. Current real examples
have maximum independent-boundary differences of 180 and 170 ms.

## Fallback and limits

`aligned` means word and phone checks passed and a viseme track was generated.
`word_aligned` retains recognized word timing when phone timing is unsupported or
the English vocabulary lacks a token; its mouth movement remains an amplitude
fallback. `amplitude_fallback` states a failed/disabled/missing alignment reason and
does not invent word or phone intervals. Existing amplitude arrays remain available.

English is the verified phoneme path. Other-language word timing is experimental
and may fail the exact token checks; there is no supported non-English phone model
here. Numbers, unusual tokenization, acoustic effects, clipped speech or unclear
pronunciations can require a clearer synthesis spelling or yield fallback. Clips
over 120 seconds and worker timeouts also fall back. A strongly distorted real Robot
clip failed forced alignment; a Creature clip disagreed with recognized words. Both
were bound to the processed WAV hash and retained fallback rather than aligning dry
speech and pretending it represented the effects output.

Lip-sync settings are independent of the speech render signature. Changing a mapping
does not make the WAV outdated or force new synthesis. Its timing can be updated
separately. Scene pins include the settings. Voice/scene ZIPs contain per-clip
`lip-sync/*.json` alongside delivered WAVs; the manifests link the timing files.
Export removes stale/invalid timing in favor of explicit fallback. Native character
rendering now supports [mapped mouth frames](voice-character-mouths.md) and
[existing 3D morph/jaw controls](voice-mesh-mouths.md).
[Game playback files](voice-game-exports.md) provide
verified Godot and Unreal adapters alongside the timing metadata.

## October 12 evidence

- The broad regression suite passed 143 checks, excluding one legacy Chrome smoke
  in favor of in-app browser testing. Coverage includes interval/confidence/NaN
  rejection, contractions, audio/config/transcript binding, preview snapshots,
  final-effects routing, metadata-only updates, source preservation and media byte
  ranges. Byte-range support fixed a real browser seekability failure.
- Real prior CustomVoice speech produced checked 7/8 word intervals, 29/30 English
  phone intervals and 169/241 frame samples at 60 fps for 2.8/4.0 seconds. Two real
  scene clips also passed, including the `harbor is` / `harbour's` contraction.
  Source NPC files and WAV bytes were preserved; no TTS inference ran in this check.
- Wrong transcripts and a tone probe rejected alignment. A known-token phoneme
  override used a separate dictionary and still passed. Actor/scene ZIPs bind their
  timing JSON to delivered audio hashes; a pinned scene retains 60 fps after a
  character switches to 30 fps.
- `scripts/check_voice_alignment_real.py` retains reports, paired voice/scene
  packages and `actor-*-timing-reference.mp4` diagrams for listening/timing review.
  These diagrams are explicitly labelled separately from character-rig animation.
- Browser verified save, metadata update, mapping edit/restore, playback and seeking
  to `1.02 s → open / OW / oh` and `0.20 s → forge / F / FF`. Desktop and 390-pixel
  mobile screenshots are retained in `local/voice-workspace-qa-20261012/` as
  `lip-sync-timing-{desktop,mobile}.png`; mobile had no horizontal overflow.

Current primary evidence is under `local/voice-alignment-real-20261012-180714-874a83/`.
The ledger in `voice-studio-goal.md` preserves the remaining 3D target,
capability and checkpoint-binding work. Game-engine mappings and installed-engine
checks are described in [voice-game-exports.md](voice-game-exports.md). The full goal stays active.
