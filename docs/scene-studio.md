# Scene Studio

Scene Maker now combines visual staging, acting, moving cameras, alternative voice takes, sound tracks, Items props and an editable writing assistant. Existing scenes keep their cast, positions, speech and legacy camera framing until edited. Scene limits remain 20 beats and 60 seconds; ensembles can contain two to six characters.

## Stage and cast

Use **Visual staging** beside the preview. Its top-down map shows starting positions. Drag a character, or focus its marker and use arrow keys. Select a character to change its rotation in degrees. Select a script beat, choose **Walk destination**, then click or drag on the map to place that beat's destination. Selecting a different timeline beat updates the map's editing target.

The cast section adds available library characters up to six. Removing someone reassigns their beats to the first remaining character; Undo restores the earlier cast and directions. The existing character pickers still replace individual cast members. Portrait-card scenes support ensemble grids in CPU video export.

Items with a finished GLB appear under **Props and scenery**. Position, height, rotation and scale are editable. Saving copies the exact model into the scene, so later edits to the source item do not silently change an existing scene. Props are free-standing stage objects; this does not introduce equipment attachment or collision simulation.

## Editing safety

Undo/Redo covers authoring fields, including script, cast, staging, camera directions and track settings. It preserves newer server-owned audio, media and render metadata. Save establishes a new history baseline. **Duplicate scene** copies its script, retained takes, sound files, background and pinned props, leaving the original unchanged and clearing the copied video.

Unsaved authoring edits are retained in this browser's local storage. Reopen a scene to choose **Recover draft** or keep its saved version. An older saved revision is flagged before recovery. Browser recovery is independent of server Save; it is not a cross-device backup. Media files are imported only after saving the scene.

## Acting and cameras

Expand **Acting and listening reactions** on a beat. Each character can face a chosen character, the current speaker or the camera; use a fixed rotation when preferred. Head look targets, wave/nod/head-shake/point gestures and listening presets use the bones available on that model. Expression controls appear only for recognized expression morph targets. They do not manufacture facial rigs on models without those controls.

Wide, Medium, Close-up and Over-the-shoulder shots remain available. Movement adds Hold shot, Push in, Pan left/right and Track subject, with an amount control and a transition duration. Hold anchors framing at the beat's start; Track follows with an adjustable lag. Existing scenes without a movement field retain **Follow framing (legacy)**. Transitions blend the previous shot into the new one. The same deterministic blocking, actor directions and camera calculation drive preview and native MP4 capture.

## Voice takes

**Generate 3 takes** queues three ordinary Forge voice jobs with the scene's captured words, voice identity and performance settings. Earlier WAVs and their delivery/lip-sync metadata remain retained. Play clips individually to compare them, then use **Use take** or **Approve take**. Approval is a manual listening judgment; it does not override word-check or alignment warnings.

An approved take stays selected while new alternatives finish. Script, speaker or voice changes make earlier takes outdated; the server refuses to select or approve an incompatible take. Voice checksums are verified when available. Selecting a take also selects its own mouth timing; no timing is borrowed from another recording. Packages retain the available alternative files and selection/review metadata.

## Sound

Import browser-decodable recordings of 0.1–60 seconds. The browser converts them to mono 24 kHz, 16-bit WAV before local upload. A scene retains up to 24 recordings and 12 independent ambience, music or effect tracks. Each track has a start time, recording offset, duration, volume, looping and dialogue-ducking switch. A loop repeats the portion after its recording offset. Tracks stop at the end of the scene.

Preview preloads audio and schedules sources on the browser's audio clock, so short effects do not depend on animation-frame frequency. Pause, seek and speech buffering reschedule from the scene clock. Ducking reduces enabled tracks to 30% of their chosen volume during actual speech, with an 80 ms attack and 160 ms release. Track playback indicators show the active recording time and effective volume. CPU and 3D MP4 exports share the same mix and envelope; source recordings are included in the scene package.

## Writing assistant

Choose an objective, location, story template and tone. **Suggest scene** creates a local profile-based proposal using the selected characters' demeanor and goals, with suggested actions and camera shots. The proposal shows relevant profile notes and remains separate from the script until **Replace script with draft** or **Append draft** is chosen. It is then editable in the normal script editor, and Undo restores the previous script. This mode requires no language model or external service and is labeled as template-based output.

## Verification

`tests/test_scene_studio.py` and the Node check scripts cover legacy defaults, ensembles, validation, undo/recovery boundaries, immutable voice alternatives and approval staleness, camera/acting determinism, sound scheduling, exact mix ducking and pinned Items models. Existing scene/voice/server/web checks remain part of the regression run.
