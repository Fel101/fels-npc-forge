# Pronunciation, performances, comparisons and scene pins

In **Voice Designer**, choose a saved character and open **Pronunciation & performances**.
Add a written term and a spoken spelling, such as `Aether` → `Ee ther`. Matching ignores
case, respects whole words, chooses longer phrases first and never cascades replacement
rules. Recognized sound tags remain intact. Ordinary parentheses still allow pronunciation
inside them. Preview the synthesis text before saving; then audition the saved rules with
**Try it** or a dialogue line. Authored dialogue, subtitles and exports retain `Aether`.
`synthesis_text` and `pronunciations_applied` record what was sent to the speaking adapter.

Add a named performance and optionally select it as the character default. Dialogue lines
choose **Character default**, **Voice settings** or a specific preset. Scene beats offer
the same choice. Saving a changed rule or preset preserves existing WAVs; affected audio
is marked outdated and excluded from a fresh voice-pack delivery until regenerated.
Unused pronunciation rules and unused model parameters do not invalidate an identical
render. Generated lines capture their authored words, effective settings and dictionary
when queued; editing the dialogue while a job waits does not change its input. Batch jobs
capture those words too. Candidate takes capture their own settings for later selection.

## Effective controls

The presets use the controls actually forwarded by the current Forge adapters:

| Speaking adapter | Saved performance controls |
|---|---|
| Qwen Base cloning | Temperature; freeform acting direction is withheld. The older mood previews remain explicitly experimental. |
| Breeze cloning | Temperature, acting direction, CFG when a nonempty acting direction is supplied. Plain cloning always uses CFG 1. |
| Kokoro stock | Speed. No cloning or acting direction. |
| Chatterbox Turbo | No parameter overrides or acting direction; supported sound tags still work. Its backend ignores CFG/exaggeration. |
| VoxCPM2 | Guidance strength (CFG), whole-number inference steps; clone mood direction stays experimental. See [Voices](voices.md). |
| Qwen CustomVoice | Direct preset identity; acting direction, temperature, top-k, top-p, repetition penalty and maximum speech tokens. No reference conditioning. |
| Chatterbox Original | English cloning; temperature, exaggeration, CFG weight, top-p, minimum probability and repetition penalty. No text direction, Turbo tags or native speed. |
| DramaBox | Retired in 1.1: its earlier takes still play. Its saved presets kept acting direction, CFG scale, STG scale and duration multiplier. |
| Fish Drama 3 | Experimental inline direction/cues, temperature, top-k, top-p and maximum seconds. |

Unsupported parameter overrides are rejected, including invalid/nonfinite numbers.
Changing a character's speaking adapter may require revising its existing performance
presets before using them. Controls describe adapter support rather than a guarantee
of perceived emotion or speaker similarity. Real Qwen temperature/pronunciation routing
was exercised; human pronunciation and identity listening remains pending.

**Voice effects** supplies separate CPU delivery controls and paired dry/processed
previews. A performance can override the voice's default effect settings, including
a dry override for a normal line. Native acting controls and identity remain separate;
see [reversible effects](voice-effects.md) for the workflow and supported formant modes.

## Same-script audition

Make at least two candidate takes using one exact script and language. **Same-script
comparisons** offers a group for each compatible script; create a session and audition
its candidates. Save a 1–5 star rating, listening notes and a favorite flag. **Assign
candidate** explicitly chooses a take for this character. These are human judgments,
not automatic quality or speaker-similarity scores. Different scripts/languages cannot
be combined in one session. Sessions retain copies of candidate metadata and file hashes;
their take files are protected from ordinary pruning and individual deletion.

The current local limit is 30 retained sessions per character, 2–12 candidates per
session. Ratings may be edited; session scripts and candidate metadata are fixed.
Comparisons stay local and are omitted from identity snapshots and game delivery packs.

## Scene version pins

In **Scenes**, each cast member has a **voice version** selector. Choose **Follow
character voice** for future character changes to affect the scene, or pin a current or
historical reference or native preset. Saving a pin freezes its speaking settings, dictionary and presets
alongside the immutable identity. Later character edits leave the pin unchanged.
Selecting a historical reference previews that version's saved performances; the server
reconstructs the authoritative snapshot when saving and ignores client snapshot edits.

Scene pins and Kokoro scene overrides are mutually exclusive. Changing the cast removes
the previous member's overrides. To deliberately refresh the settings of an existing
pin, save **Follow character voice**, then choose and save the reference again. Queued
scene jobs have frozen inputs; scene edits can make their output outdated. Explicit
**Retry** retains the existing scene behavior of rebuilding inputs from the current
saved scene. New jobs and restarted pending jobs use their captured snapshots.

## Stored data and compatibility

Character `voice.studio` contains rules, presets, the default and per-line assignments.
`voice.comparisons` holds retained auditions. Speech metadata adds `synthesis_text`,
`pronunciations_applied`, `performance`, `render_signature` and `voice_snapshot`.
Scene `voice_bindings` holds reference IDs and snapshots; beats store `performance_id`.
Voice packs and scene source manifests include the spoken text/performance provenance.
Reference IDs identify immutable media; separate SHA-256 checks guard reference reads
during synthesis. The signature covers effective inputs, not timestamps or unused rules.

Older clips with a voice snapshot can be checked against their captured settings.
Clips without one keep the old text/reference checks and become outdated when new rules
or performances alter their synthesis. Their past settings cannot be reconstructed;
regenerating records a complete signature. No databases, recordings or model environments
are migrated or upgraded automatically.

Lip sync and effects have pages of their own: [lip sync](voice-lip-sync.md) and [effects](voice-effects.md).
