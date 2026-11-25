# Scene Director

Scene Maker adds a timeline, camera direction and an actionable readiness checklist to existing portrait and 3D scenes. Existing scenes default to the previous wide camera. Save scene persists edits; Update preview previews the draft without saving it.

## Timeline and playback

- Scrub the playhead or select a beat to inspect that moment and highlight its script card.
- Pause holds the current frame; Resume continues there. Stop returns to frame zero.
- Drag timeline beats to reorder them, or use the script's up/down buttons. Beat IDs and retained speech stay attached to their beats.
- Edit pause and reaction lengths in seconds. Spoken beat duration follows its speech, with the existing minimum and trailing padding.
- Speech playback follows the actual audio clock and holds during buffering. Silence and the trailing pause advance with a monotonic clock.

## Cameras

Each 3D beat has Wide, Medium, Close-up or Over the shoulder, a cast member as subject, and Left/Right side. The camera follows that subject's blocking. Over the shoulder uses the other cast member in the foreground and falls back to Medium if actors overlap. Preview and MP4 capture use the same camera calculation. Camera edits invalidate an old video without invalidating its speech.

By default shots cut at beat boundaries. [Scene Studio](scene-studio.md) adds camera movement and adjustable shot transitions, plus visual staging, acting, ensembles, retained voice takes, sound tracks, props and writing suggestions. Manual lens controls remain outside this editor. Framing uses the stage's actor scale; portrait scenes retain their card layout.

## Scene readiness

Readiness is derived from the current compiled preview. It identifies missing characters or rigs, outdated visuals, missing or outdated speech, missing settings, failed or unavailable word checks, amplitude mouth tracks, and older exports. Buttons open the affected character, focus a cast picker, generate the setting or speech, review the clip, or render again.

Missing characters and missing 3D rigs block export. Other issues remain warnings or information: a plain setting and subtitle-only lines remain supported. Listening to a flagged clip does not certify its words, identity or mouth quality. Generation and export actions require saving a dirty draft first.

## Verification

The automated suite covers camera validation and compatibility, readiness levels, speech clocks including buffering and resume, and controller behavior for draft pause edits, seek/resume and stable beat reordering.
