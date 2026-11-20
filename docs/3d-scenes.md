# Animated 3D scenes

NPC Forge's **Animated 3D stage** uses two rigged GLB characters on a shared Three.js stage. A scene describes an empty
setting in text; **Generate background** sends that description through the normal ComfyUI job queue and saves a 16:9 PNG
inside the scene folder. The picture is a background plate behind the 3D actors. It is not a navigable environment mesh.

The plate is drawn with the picture engine chosen in **Models and options** (Qwen-Image 2512 by default, since 2026-10-14; it was
always Mage-Flow before), with the same **Let Mage-Flow draw when the chosen model cannot** switch as every other picture: by default
a plate Qwen cannot draw (it needs about 29 GB of free graphics memory) fails with that reason and a retry works once the card is
free. The scene's record names the engine that drew it (`background.engine`). Qwen ignores negative prompts, so the emptiness is in
the wording itself: for every engine but Mage-Flow the plate is asked for as a deserted place where everyone has just gone home and
the stalls, counters and seats stand unattended ("empty scene with no people" was not enough: Qwen still drew vendors behind the
stalls of a market square in 7 of 12 pictures, and this wording in none of 6 seen at full size, though two have a dark patch at an
edge that might be one). Measured the same way on a forest gate, a tavern, an Old West street and a spaceship corridor, three seeds
each, nobody was drawn. The price is furniture: with nobody there, Qwen fills the plate with unattended counters, benches and
booths, so a "checkpoint" gets guard huts; a very busy setting can still get a small figure in a corner, and **Generate
background** again draws another picture in about 8 seconds once the model is loaded. Black bars at the top and bottom (what a
"wide cinematic" wording draws) were seen once in 102 test plates; bars like that are cut off and the job says so.

Each line or reaction has a 3D action: stand, wave, or walk to a mark. Marks are horizontal X (-3 to 3) and depth (-2 to 2)
coordinates. Characters start on opposite sides of the frame. A walk interpolates their position during the first 80% of
its beat while the embedded GLB `Walk` clip plays. Existing `Idle` and `Wave` clips play for the other actions. Dialogue
uses the scene voice clips already saved by NPC Forge. A scene can select a Kokoro stock voice for a cast member without
changing the character's own voice record. If a rig has a jaw bone, its mouth track drives that bone; older rigs use a
small head movement. A missing voice produces subtitles only. An outdated rig can be previewed, but the editor
warns that its appearance may differ from the latest model.

**Export 3D MP4** renders each timeline frame in the browser at 25 fps, including the title and subtitles. The browser
sends the ordered JPEG frames to the local server. FFmpeg combines those frames with the saved scene voices into an
H.264/AAC MP4, which is stored and downloaded like the earlier portrait scene exports. Export requires WebGL and canvas
JPEG support. Rendering may take longer than the final video duration, but the MP4 receives every timeline frame.

Older portrait scenes remain editable under **Portrait cards**. New scenes default to **Animated 3D stage**. The scene
stores only references to the NPC rigs and voice files, so character model work can continue independently.
