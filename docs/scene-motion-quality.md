# Scene motion and cast review

Scenes sample the original animation tracks directly at the requested scene time.
Preview, repeated frames, seeking and recorded frames therefore produce the same
bone pose. Resetting bones behind AnimationMixer's cached PropertyMixer values
previously dropped unchanged tracks and made the pose flicker.

Idle uses the clip's original timing. Walk phase follows the distance travelled
and the rig's recorded walking speed, with a short blend at each end. Waves start
at the beat, play once and blend back to idle. Nods and shakes are small, single
gestures. Body turns use the shortest angle and ease toward the requested facing;
being the current speaker no longer resets the actor to its initial facing.

Mouth weights use a deterministic 120 ms sampling window to soften rapid jaw
changes. A fresh animation pose underlies each mouth overlay, so opening the jaw
does not accumulate. Scene actors without a configured mouth rig use a small jaw
fallback; audio amplitude no longer bobs their head. Calibrate jaw angle per model
in Voice Designer. A jaw bone alone cannot produce lip closure, lip rounding,
cheek movement or expressive facial acting; those need suitable facial shapes
and mapping on the character's model.

Use **Cast and voice check** in Scenes to compare the portrait, profile and an
actual scene line (or saved voice reference). Check the model on stage as well:
a profile label cannot establish whether generated artwork matches the person.
Transcript and phoneme checks establish words and estimated timing, not casting
fit, natural delivery or visual quality.

For a first review, use two matched characters, three short neutral lines, a fixed
wide camera and no gestures or walking. Review preview and exported MP4 before
adding movement and camera direction. Preserve rejected drafts for comparison.

Regression checks:

```powershell
python -m pytest tests/test_scene_studio.py tests/test_scene_director.py tests/test_scenes.py tests/test_voice_mesh_rig.py tests/test_web_modules.py -q
node scripts/check_scene_motion_real.mjs path/to/model_3d_rigged.glb path/to/report.json
```

The optional GLB check reads actual joint tracks, verifies repeated frames and
arbitrary seeking, and measures adjacent idle head poses. It does not rate acting
quality or replace playback review.
