# Character mouth artwork

For existing 3D models, see [3D mouth targets and jaw controls](voice-mesh-mouths.md).

Open **Voice Designer → Lip sync → Character mouth artwork**, also available from
the character's Voice card. Choose Portrait or Sprite and **Create mouth artwork**.
Forge copies the source image into an immutable preview up to 512 pixels. A matching
living-portrait talk image becomes a second frame. Source artwork and speech stay intact.

Map each viseme and the amplitude-open pose to a saved frame. Import named aligned
PNG/JPEG/WebP images with the same layout/aspect ratio for more shapes. The browser
resizes the derived previews and retains alpha. Save mappings for scenes and exports;
restore an earlier version to undo a frame or mapping edit. Frames and version JSON
are checksum-bound under `voice/visual-rigs/`. Replacing a frame name preserves the
older bytes. If the source artwork changes, rebind deliberately; older versions remain.

**Preview mouth pose** auditions artwork without speech. Analyze a clip, then play
or seek it: the character canvas follows the final WAV's actual audio clock and shows
the current word, phone and group. Portraits blend with premultiplied alpha; sprites
choose the dominant mapped image after combining groups sharing that image. Silence
and the clip end use the silence image. Visible amplitude fallback uses its separate
open image without asserting phoneme timing.

The default bridge has two images, not fifteen physical mouth shapes. The original
rest portrait may be smiling. Import artist-created closed lips, teeth, tongue and
vowels for more accurate poses. Full frames must stay aligned to avoid flicker. Mouth
artwork overrides older living blink/expression selection while speaking. This feature
generates no new face artwork and promises no manual phonetic accuracy.

Source hashes, frame hashes and immutable manifests are checked. Missing/changed
artwork falls back to existing animation. Mouth edits leave speech fresh but invalidate
scene video fingerprints. Restoring the original configuration restores its fingerprint.
Saved card scenes use these mappings in browser playback and CPU MP4s; the timeline
reports `viseme_frames`, `amplitude_frames` or `amplitude_fallback`. 3D stage scenes
use amplitude animation.

Voice packs link `game/character-rigs/<kind>/rig.json` and PNG frames. Scene packs link
per-character files under `game/characters/<id>/rigs/`. Metadata includes mappings,
interpolation, frame checksums, Forge version provenance and the source-art checksum.
Full-resolution originals stay local. Godot's `load_sprite_rig(json_path, pack_root)`
builds an AnimatedSprite2D atlas from the exported PNGs. It uses dominant selection;
use `weights_changed` for a custom weighted portrait crossfade. Unreal can import the
PNGs as textures and select them using the artist mapping and exported curves.

## What was checked

The automated checks cover keeping the originals, hashes, making a version again and restoring one, mapping limits, the
amplitude fallback, and video-only changes. Two characters' portraits and living frames were animated with real speech;
Godot 4.7.2 loaded the exported atlas, and at 1.02 seconds the character and the audio matched `open / OW / oh`. Nobody but
their maker has yet judged how the voices sound or how exactly the mouths follow the sounds.
