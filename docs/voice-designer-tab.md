# Voice Designer tab

The native Voice Designer tab sits between Items and Scenes. A character's Voice
card opens it with that character selected.

It also has a **Voice library**, a history of each reference recording, approved reusable voices, and a
waveform and recording editor.

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

## Where the voices come from

Voices come from [Fel's Voice Lab](https://github.com/Fel101/fels-voice-lab), running on the same computer: its ready
voices, Cast voices it designs from a description, and voices it clones from a recording you have permission to use. Which
models are offered, and which moods and controls each one takes, depend on what is installed in Voice Lab. Forge keeps the
character's takes, dialogue, pronunciation, performances, effects and lip sync; Voice Lab keeps its own Cast. See
[Voices](voices.md) for setting it up.

Guides: [voices](voices.md), [pronunciation and performances](voice-authoring.md), [effects](voice-effects.md),
[auditions](voice-auditions.md), [lip sync](voice-lip-sync.md) and [game exports](voice-game-exports.md).
