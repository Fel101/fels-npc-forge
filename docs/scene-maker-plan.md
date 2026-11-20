# Scene maker plan

**Earlier implementation (September 2026):** This plan described portrait-card scenes. The intended feature was clarified as
rigged 3D characters walking and speaking in front of a generated setting. See [3d-scenes.md](3d-scenes.md) for the animated
stage; portrait-card scenes remain readable for existing saved work.

## Goal

Let someone make a short, shareable exchange between NPC Forge characters using the portraits, living frames, voices, and sprites already in the library. A saved scene should remain editable, play in the app, and export as a video with sound and subtitles. This is an offline feature: creating a scene does not require ComfyUI unless the user chooses to make missing character assets.

## First release

- Two speakers, chosen from the library or prefilled from a cast pack. A third NPC can wait for a later release.
- An ordered script of up to 20 beats: spoken line, silent reaction, or pause. Lines have editable text, speaker, mood, and optional expression. A beat can be reordered or removed.
- A fixed set of visual layouts: speaker close-up, two-person split screen, and full-body sprite. Each beat chooses one layout. Living portrait frames supply blinking and mouth movement when available; a still portrait is a valid fallback.
- One simple backdrop per scene (solid/gradient and title). No generated location art is required.
- Browser playback with subtitles, timing, and audio. The editor clearly labels missing or outdated assets and supports silent/subtitled playback before voice generation.
- Export a 720p, 25 fps MP4 in 16:9 or 9:16. Cap total length at 60 seconds. A saved scene manifest and the rendered file remain in the local library.

The first release is a scene editor and compositor, not an open-ended video generator. It should use existing visual assets, so characters retain their established appearance.

## User flow

1. Open **Scenes** from the main navigation, create a scene, and pick two NPCs. A cast pack can fill the picker but does not own the scene.
2. Add or edit beats. Start from existing dialogue lines if useful; copy their text into the scene so later NPC dialogue edits do not silently rewrite the script.
3. Press **Preview** at any time. Missing voices play as timed subtitles. Missing living frames use the portrait. The editor lists which assets would improve the result.
4. Press **Voice lines** to make scene-specific WAV clips in each NPC's chosen voice. Allow one-line redo and mood changes. Never store these clips in `npc.json` or the NPC dialogue pack.
5. Press **Render video**. Show a job with progress, cancellation, and any actionable error. Download or replay the finished MP4 from the scene page.

## Data and ownership

Add `npcgen/scenes.py` for validation and a `Scenes` store, following the atomic JSON write and lock pattern in `npcgen/cast_packs.py`. Keep each scene under `library/.npcforge-scenes/<scene-id>/`:

```text
scene.json                editable script, layout choices, version, timestamps
audio/<beat-id>.wav       scene-owned speech clips
render/<render-id>.mp4    completed export
sources.json              source fingerprints and licence notes in the downloadable ZIP
```

Use stable scene and beat IDs. A scene record should include `version`, `id`, `title`, `cast` (NPC IDs), `aspect`, `backdrop`, `beats`, `created`, and `updated`. Each beat should include `id`, `kind`, `speaker_id` where relevant, `text`, `mood`, `expression`, `layout`, and any pause duration. Voice metadata should record the text hash, mood, selected voice reference ID, engine, duration, 25 Hz mouth track, and WAV path. Validate text lengths, allowed enum values, NPC membership, beat count, and total duration before writing or rendering.

Scene data refers to NPCs by ID; it does not copy their full records. On preview and render, resolve current assets and compare their creation stamps or hashes with the last render. Changing a portrait, living set, sprite, voice, script line, or layout marks the relevant preview/audio/render outdated. If an NPC is deleted, keep the scene and its completed exports, but show the missing cast member and block a new render until replaced. Delete a scene's own folder only when that scene is deleted.

## Playback and rendering

Create a timeline compiler in `npcgen/scene_timeline.py`. It turns the validated scene plus current voice metadata into integer-frame start/end times, visual source choices, subtitles, and mouth-frame indices. At 25 fps, each mouth-track sample maps to one video frame. This compiled timeline is the contract for both browser playback and export.

Add a focused editor module (`web/scenes-view.js`) and styles instead of extending the already large `web/app.js` with the whole editor. The browser preview uses the compiled timeline, existing `/api/npcs/<id>/files/...` URLs, and the same layout values as export. For final output, use Pillow to composite frames from saved image assets and an optional local FFmpeg executable to encode H.264/AAC MP4 and mix WAV clips. Detect FFmpeg at startup or render time; explain how to enable export if absent. Preview, saving, and silent scene work remain available. Avoid adding a Python video framework or a GPU render requirement.

Rendering must write to a temporary path and atomically publish a completed MP4. Cancellation removes temporary files, and a failed render leaves the previous completed export intact. Restrict all scene paths to the scene root, as `Library.resolve` does for NPC assets. Put a cap on render size, duration, and intermediate disk use.

## Voice and jobs

Refactor the reusable speech portion of `npcgen/voice/steps.py` so a scene beat can request speech through the existing voice runtime and GPU coordination. Give scene lines their own job steps and store their WAV and mouth track in the scene folder. A scene line becomes outdated when its text, mood, speaker, selected voice reference, or speaking engine changes. Reuse a valid clip when only visual layout changes.

The current `Jobs` records and queue are NPC-centric. Extend job records with an explicit target type and ID, while preserving recovery of old NPC job records. Scene voice jobs share the existing GPU queue. CPU video renders should run in a separate bounded worker so a long encode does not block character generation or a quick voice preview. Both job types should appear in the existing queue UI and support cancel/retry. Keep scene saves independent of a running render; render from a captured, validated scene revision so mid-render edits cannot change its output.

## API and integration

- `GET/POST /api/scenes`, `GET/PUT/DELETE /api/scenes/<id>`: list, create, load, update, delete.
- `GET /api/scenes/<id>/timeline`: compiled timeline and missing/outdated asset warnings.
- `POST /api/scenes/<id>/voice`: enqueue selected beat IDs, with an option to redo them.
- `POST /api/scenes/<id>/render`: enqueue a render with aspect and quality settings.
- `GET /api/scenes/<id>/files/<safe-path>`: serve only approved scene media types from inside that scene's folder.

Wire the store into `npcgen/app.py`, routes into `server.py`, and a Scenes tab into `web/app.js`. Add a scene entry point from a selected cast pack and from a character's page. Increment the API version and use the existing outdated-server banner behavior. Keep the current host/origin/auth checks on every new route. Add scene metadata to a scene export ZIP alongside the MP4, source attributions, and applicable model/voice licence notes using the information already carried by NPC assets and `NOTICE.md`.

## Delivery sequence

1. **Scene store and contracts.** Define the versioned manifest, validation, safe file resolver, CRUD routes, and tests for malformed data, missing NPCs, deletion, and reload. Include a tiny hand-written two-NPC fixture.
2. **Editor and preview.** Add Scenes navigation, cast picker, beat editor, layout choices, subtitles, timeline compilation, and browser playback. Confirm portrait-only scenes work and living frames animate where present.
3. **Scene speech.** Add scene-owned TTS jobs, clip reuse/staleness rules, per-line redo, mouth animation, and restart/cancel tests. Confirm NPC dialogue packs are untouched.
4. **Video export.** Add CPU render worker, FFmpeg capability check, temporary-file handling, download route, and 16:9/9:16 outputs. Compare representative preview frames with exported frames and check audio/subtitle timing.
5. **Polish and documentation.** Surface missing assets and licensing, support cast-pack entry, document FFmpeg setup and scene format, and run the focused API/UI tests plus one end-to-end export with fixture assets.

## Release criteria

- A user can create, save, reopen, and edit a two-NPC scene without any running image model.
- A scene with only portraits and text previews and exports successfully; available living frames, voices, and sprites improve it automatically or by explicit layout choice.
- A custom scene line is voiced in the selected NPC voice, follows its 25 Hz mouth track, and becomes outdated after a relevant change without altering the NPC's dialogue pack.
- Exported MP4 duration, subtitle timing, speaker order, and aspect match the preview within one frame; cancellation and restart cannot publish a partial file.
- Missing or deleted source assets produce clear warnings; existing completed exports remain playable.
