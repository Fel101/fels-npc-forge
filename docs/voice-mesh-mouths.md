# 3D character mouth controls

Open **Voice Designer → Lip sync → 3D mouth targets** (also available from a
character's Voice card). Rig or import the character's GLB first. Forge discovers
named morph targets and skin joints from that actual model. Map each mouth group
to an existing target, or leave it neutral. Choose the rig author's jaw joint,
axis, maximum opening and direction; tune each group's jaw amount. Save the mapping,
select **3D model**, analyze existing speech, and play or seek the final WAV.
The close-up preview shows the current word, phone and mouth group.

A medium Forge rig can have a jaw without facial morphs. Its phoneme groups vary
jaw opening; they cannot create distinct lip, tongue or teeth shapes. Low rigs may
have neither jaw nor morphs. The interface reports this limitation. Full facial
poses require artist-created morph targets in the imported model. Forge does not
automatically create ARKit/MetaHuman shapes or retarget an arbitrary skeleton.

The mapping belongs to the character, independently of voice identity and synthesis
settings. Saving preserves the original GLB and WAV, creates an immutable model
snapshot, and records a checksum-bound mapping version under `voice/visual-rigs/mesh`.
Replacing the source model marks its previous mapping stale. Configure and save
the new model explicitly. Old model and mapping bytes remain; no mesh-version
restoration UI is provided yet.

Native 3D scenes load the checked model snapshot. Mapped morph weights sum and clamp
when groups share a target; jaw opening combines the group's configured amounts.
These controls overlay body animation without accumulating rotations, and mapped
targets clear when their weights fall to zero. Live speech uses the audio clock.
The deterministic MP4 capture uses frame seconds and the same mapping path, then
muxes the final dialogue audio. Stop clears the mouth overlay. Invalid alignment
uses `mouth_open` with amplitude and keeps that status visible. Missing/stale mappings
retain the stage's previous amplitude animation. Mapping changes invalidate scene
video freshness while leaving generated speech intact.

Voice and scene packages include `rig.json` and its exact `model.glb`, linked from
`character_rigs.mesh`. The JSON contains the model SHA, mapping version, target names,
joint names and jaw controls. Per-clip JSON/Unreal curves still carry final-audio
checksums and absolute seconds.

## Godot 4.7.2

Instantiate the exported GLB with its original names. Add `game/godot/forge_mesh_mouth.gd`
as a child of its `Skeleton3D`, and set `model_root` to the imported scene root.
After entering the tree, call `load_rig(rig_json_path, original_glb_path)` and check
the returned Error. It verifies original GLB bytes, the jaw and mapped morph names.
Connect `forge_lip_player.weights_changed` to this modifier's `set_weights`.
The modifier runs after the skeleton's body animation; it drives the jaw and all
matching mesh targets under `model_root`. Avoid also assigning those same targets
to `forge_lip_player.face_mesh`. Tune the local jaw axis/direction for the imported
rig's coordinate convention. Keep source GLB/JSON/WAV files in package include filters
when using byte-hash checks. Existing 4.6 projects retain their own engine.

## Unreal Engine 5.8.2

Use the existing checked CurveTable/SoundWave importer described in
[voice-game-exports.md](voice-game-exports.md). Import/retarget the model skeleton
with your normal art workflow. In the Animation Blueprint, evaluate group curves at
the current absolute audio seconds. Sum mapped morph weights per target and clamp
to one. For the jaw, sum `weight × jaw_amounts[group]`, using one for `mouth_open`,
clamp to one, multiply by `jaw_radians × jaw_sign`, and convert radians to degrees.
Apply that amount on `jaw_axis` to `jaw_bone` with **Transform (Modify) Bone** after
body animation, using **Add to Existing** in **Bone Space**. Tune the axis/direction
against the imported rig. Clear on stop, hold on pause and update the absolute time
on seek. This is an explicit mapping recipe, not a packaged AnimBlueprint or an
automatic skeletal importer. Unreal curve/audio imports were tested independently;
this particular jaw adapter was executed in Godot and the Forge browser.

## What was checked

`scripts/check_voice_mesh_real.py` uses copied characters, real speech and a rigged GLB with a jaw (1,503 vertices with jaw
skin weights and no named facial morph targets). Godot loads the exported model and WAV, checks all 169 stored poses,
advances the audio clock, seeks, holds the pose on pause and returns the jaw to rest on stop. In the page's close-up the mouth
opens at 1.02 s (`OW`/`oh`). A two-character scene of 5.2 seconds exported as 130 frames, with its audio matching the planned
mix. This shows the pipeline works; it does not show that a body, portrait and voice belong to one person, or that the mouth
shapes are phonetically right.
