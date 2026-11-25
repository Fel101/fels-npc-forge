# Final-audio alignment and viseme timing

Forge times each final WAV: checked word timing, English phoneme timing, configurable
visemes and a synchronized timing diagram. Scene videos made before a clip was timed keep
the amplitude mouth renderer; timing data in their packages does not give those videos
phonetic mouth shapes.

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

## Voice Lab comparison, November 24, 2026

Ten English lines were rendered with each of three voices in an isolated Voice
Lab at `de1d7f6`. Kokoro spoke as `af_heart` (American English) and `bm_george`
(British English). Chatterbox Turbo spoke with a synthetic Qwen VoiceDesign
reference. Each `POST /api/performance` kept a take. Its fetched WAV matched the
kept audio and the original-take hash in the Lab's scene export. Forge's
lip-sync preview ran at 30 fps on those same WAVs with blending off.

The Lab's take API reports whether timings are present. The scene export
provides the actual mouth cues. The Lab's word aligner used its own pinned
environment. Kokoro's exported cues use the model's 25 ms phoneme timings.
Chatterbox Turbo cues distribute each aligned word's time across its phonemes.

The comparison grouped Forge's ARPABET phones into the Lab's eleven mouth
shapes and merged consecutive cues of the same shape. It paired non-rest onsets
of the same shape in order within 250 ms, maximizing matches and then minimizing
their total distance. Unpaired cues and Forge amplitude fallbacks were excluded
from the error statistics. One frame at 30 fps is 33.3 ms. The waveform plots
showed speech bounds based on a −50 dBFS threshold alongside each mouth track.
Absolute phone accuracy would require manually annotated boundaries.

| Voice | Forge phonetic tracks | Lab mouth tracks | Matched onsets within one frame | Median onset gap |
| --- | ---: | ---: | ---: | ---: |
| Kokoro `af_heart` | 6/10 | 10/10 | 2/110 | 80 ms |
| Kokoro `bm_george` | 7/10 | 10/10 | 1/131 | 85 ms |
| Chatterbox Turbo | 7/10 | 10/10 | 28/130 | 70 ms |
| **Total** | **20/30** | **30/30** | **31/371** | **79 ms** |

Across the 20 comparable clips, 16 Forge onsets and 35 Lab onsets went unpaired.
The 90th-percentile gap among matched cues was 130 ms. Forge fell back to
amplitude for nine transcript mismatches and one rejected word-boundary fit.

On the `af_heart` waveform for “Five bright lanterns flickered in the fog,”
speech crossed the threshold at 0.320 s and ended at 2.340 s. Forge's first and
last mouth cues bounded 0.250–2.380 s. The Lab's cues bounded 0.375–2.625 s and
continued into trailing silence. The same late ending was visible for
`bm_george` on “Wait here. I will be right back.” The Lab's Chatterbox Turbo
starts were within 10 ms of the waveform threshold on all ten lines, although
102 of its 130 matched interior or opening cues differed by over one frame.

Forge keeps the CPU aligner to time the delivered WAV and apply the transcript
checks and per-character phone mappings. The Lab's scene exporter supplied all
30 cue files. Its package verifier flagged the SRT/VTT line mapping, so the
comparison checked each timing file and source WAV against the manifest hashes.

Game-engine mappings and the checks in Godot and Unreal are in [voice-game-exports.md](voice-game-exports.md).
