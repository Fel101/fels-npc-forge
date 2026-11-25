# Voices

Start [Fel's Voice Lab](https://github.com/Fel101/fels-voice-lab/releases) on the
same computer. Install speech models under **Models & features** in Voice Lab.
Kokoro offers ready-made voices. To keep a new Qwen VoiceDesign voice, also install
a model that can speak from its reference, such as Qwen Base or Chatterbox Turbo.

Open **Voice Designer (Preview)** in Forge and select a saved character.

- Pick a ready voice from the Voice Lab list and select **Use this voice**. Forge
  uses that voice for auditions, new dialogue and scenes.
- Enter a description and select **Make this voice in Voice Lab**. The queue shows
  progress, and the finished Cast voice is named after the character.
- Open **Use a recording** to clone a recording in Voice Lab. Supply 3–40 seconds
  of clear speech, its exact words, its origin and the permitted use. Confirm that
  you have permission. Leave the file field empty to reuse the character's saved
  recording and transcript.

Voice Lab may finish a request that was already running when you cancelled it in
Forge. Refresh the Voice Lab list to find a finished Cast voice.

## Earlier Forge voices

Forge keeps existing WAVs, takes, reference versions and voice pack exports.
Select **Make this voice in Voice Lab** on the character's Voice card to use the
saved description or migrate its recording. Pick a preset directly from the
Voice Lab list. DramaBox is retired. Its recordings and previous exports still
play, and a consented recording can be used to make a new Cast voice.

## Authoring and game delivery

Use **Pronunciation & performances** for spoken spellings, acting direction on
supported voices and per-line presets. Dialogue and captions retain their written
text. Supported models perform cues such as `[laugh]` and `[sigh]`.

Use **Voice effects** for reversible processing and **Lip sync** for mouth and
viseme previews. **Download voice pack** includes current dialogue WAVs,
`lines.json`, Yarn dialogue, lip-sync tracks and Godot and Unreal playback files.
See [game exports](voice-game-exports.md), [character rigs](voice-character-mouths.md)
and [mesh mouths](voice-mesh-mouths.md).

Forge keeps its separate CPU aligner for lip sync on the delivered WAV, including
clips with effects applied. In a 30-take comparison, only 31 of 371 matched mouth
cues from Voice Lab were within one frame at 30 fps of Forge's cues. Forge uses
amplitude when its transcript and timing checks reject a clip. See the
[timing comparison and installation guide](voice-lip-sync.md).

Forge uses the local Voice Lab HTTP API. On this computer it finds the Lab through
Fel's Studio's address file, or through the `VOICE_LAB_URL` override when set.
Existing audio, exports and picture tools work while the Lab is closed.
