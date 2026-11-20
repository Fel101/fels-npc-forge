# Skeleton and animations

`npcgen/rig3d.py` runs `npcgen/blender/rig_humanoid.py` in Blender (`blender -b --factory-startup`), on the CPU. It takes
the character's `model_3d.glb` and writes `model_3d_rigged.glb` (the same textured mesh, a skeleton, skin weights and
animation clips) plus `model_3d_rigged.png`, a still of the walk. When SDPose is installed in ComfyUI, the joints are placed
from pose keypoints first (below). Run the scripts by hand to see every step:

```bash
blender -b --factory-startup --python npcgen/blender/pose_views.py -- IN.glb views/
blender -b --factory-startup --python npcgen/blender/rig_humanoid.py -- IN.glb OUT.glb --height 1.7 --report report.json --preview previews/
blender -b --factory-startup --python npcgen/blender/rig_humanoid.py -- IN.glb OUT.glb --keypoints keys.json --skeleton medium
blender -b --factory-startup --python npcgen/blender/rig_humanoid.py -- IN.glb OUT.glb --library "animations/quaternius/UAL1_Standard.glb|animations/quaternius/UAL2_Standard.glb"
blender -b --factory-startup --python npcgen/blender/rig_humanoid.py -- IN.glb x.glb --debug joints.ppm --joints_only 1
```

(`keys.json` is what `npcgen.posekeys.keypoints(comfy, views_dir, name)` returns for the `views/` folder.)
`--preview DIR` renders six frames of every clip from the front, the three-quarter and the side view (18 Cycles CPU renders a clip, 54 for the three basic clips, a few minutes).
`--shape ORIGINAL.glb` works out the joints and skin weights on the generator's untouched model when `IN` is a cleaned-up
game version (see [model-cleanup.md](model-cleanup.md)): the voxel proxy needs a closed surface, and the game version has
open edges where its hidden inner skin was removed.
`--debug` writes the front silhouette with the arm tracks (blue) and joints (red).

## Skeletons

| `--skeleton` | Bones | |
|---|---|---|
| `low` (default) | 23 | `root`, `pelvis`, `spine_01..03`, `neck_01`, `head`, and per side `clavicle`, `upperarm`, `lowerarm`, `hand`, `thigh`, `calf`, `foot`, `ball` (`_l` is the character's left, +X) |
| `medium` | 60 | the same 23, plus per side `thumb_01..03`, `index_01..03`, `middle_01..03`, `ring_01..03`, `pinky_01..03`, `upperarm_twist_01`, `lowerarm_twist_01` and `prop` (no weights: a point to attach a held object to), and `jaw` |

The names are the Unreal mannequin's (the jaw's is what the scene maker's 3D stage looks for: `web/stage3d.js` turns `jaw`
about its own X axis with the voice's mouth track, up to 0.28 rad). The 23 low bones are placed the same way in both, so a
clip made for one plays on the other. For comparison, the adult Sims 4 skeleton lists 165 joints: the same 23 core bones,
30 finger bones, 25 face bones, 18 helpers (twist, skirt, IK poles, carry), 16 Create-a-Sim slider bones and 53 attachment
slots; about 95 of them move the mesh.

## Clips

| `--clips` (the rig card's Clips) | |
|---|---|
| `auto` (default) | the library when it is installed, else the basic three |
| `basic` | Idle, Walk and Wave, keyframed in `rig_humanoid.py` (step 7) |
| `library` | the 84 clips of Quaternius' Universal Animation Library 1 and 2 (the free Standard packs, CC0), copied onto the skeleton, and our Wave: 85 |

The library is installed once with `python scripts/install_animations.py`: it downloads both packs from itch.io (35 MB)
and keeps the two GLB files the rig step reads (16 MB) in `animations/quaternius/`, which git ignores (`--zip A.zip B.zip`
takes the zips downloaded by hand; `"animation_library"` in `config.json` names another folder). Its `Idle_Loop` and
`Walk_Loop` are named `Idle` and `Walk`, the names the scene maker's 3D stage plays; every other clip keeps the library's
name (Quaternius' [animation viewer](https://quaternius.com/animviewer.html) plays them all):

- Standing: `Idle`, `Idle_Talking_Loop`, `Idle_TalkingPhone_Loop`, `Idle_FoldArms_Loop`, `Idle_No_Loop`, `Yes`,
  `Idle_Rail_Loop`, `Idle_Rail_Call`, `Idle_Lantern_Loop`, `Idle_Torch_Loop`, `Idle_Shield_Loop`, `Idle_Shield_Break`
- Moving: `Walk`, `Walk_Formal_Loop`, `Walk_Carry_Loop`, `Jog_Fwd_Loop`, `Sprint_Loop`, `Crouch_Idle_Loop`,
  `Crouch_Fwd_Loop`, `Push_Loop`, `Swim_Idle_Loop`, `Swim_Fwd_Loop`, `Jump_Start`, `Jump_Loop`, `Jump_Land`,
  `NinjaJump_Start`, `NinjaJump_Idle_Loop`, `NinjaJump_Land`, `ClimbUp_1m`, `Roll`, `Slide_Start`, `Slide_Loop`,
  `Slide_Exit`, `Zombie_Idle_Loop`, `Zombie_Walk_Fwd_Loop`, `Zombie_Scratch`
- Sitting and lying: `Sitting_Enter`, `Sitting_Idle_Loop`, `Sitting_Talking_Loop`, `Sitting_Exit`, `Driving_Loop`,
  `LayToIdle`
- Doing: `Interact`, `PickUp_Table`, `Consume` (drink or eat), `Chest_Open`, `Fixing_Kneeling`, `Farm_Harvest`,
  `Farm_PlantSeed`, `Farm_Watering`, `TreeChopping_Loop`, `OverhandThrow`, `Dance_Loop`
- Fighting: `Punch_Jab`, `Punch_Cross`, `Melee_Hook`, `Melee_Hook_Rec`, `Sword_Idle`, `Sword_Attack`, `Sword_Block`,
  `Sword_Dash`, `Sword_Regular_A`, `Sword_Regular_A_Rec`, `Sword_Regular_B`, `Sword_Regular_B_Rec`, `Sword_Regular_C`,
  `Sword_Regular_Combo`, `Sword_Heavy_Combo`, `Shield_Dash`, `Shield_OneShot`, `Pistol_Idle_Loop`, `Pistol_Aim_Up`,
  `Pistol_Aim_Neutral`, `Pistol_Aim_Down`, `Pistol_Shoot`, `Pistol_Reload`, `Spell_Simple_Enter`,
  `Spell_Simple_Idle_Loop`, `Spell_Simple_Shoot`, `Spell_Simple_Exit`, `Hit_Chest`, `Hit_Head`, `Hit_Knockback`, `Death01`
- Ours: `Wave` (the free packs have none)

Names ending in `_Loop` loop (so do `Idle` and `Walk`). Every clip is in place: the library's root-motion files (`_RM`) are
not copied. The rig report and the character's `rig_3d` record list each clip's length, whether it loops and which library
clip it came from (`clip_info`), and for the 27 clips that carry the character somewhere (walks, runs, crouching, swimming,
climbing, rolls, slides, some sword moves) how far (`moves`: forward, up, left in metres) and, for a loop, how fast
(`speed_m_s`), at the character's own size: the installer measures them from the root-motion files. Whatever moves a
character around a scene should move it that fast, or its feet slide (the library's walk: 1.3 m per 1.33 s cycle,
0.98 m/s on its 1.83 m mannequin).

**How a clip is copied** (`npcgen/blender/rig_library.py`, about 2 s for all of them). The library's rig has the Unreal
mannequin's names too (with a `Head`), but it stands in a T-pose, 1.83 m tall, and ours stand in their own A-pose. Its GLB
files are read directly (no import), and each clip is copied bone by bone in world space: each bone of ours turns exactly
as the library's bone turns away from its rest pose. The arm, hand, finger, thigh and calf bones are first swung, by the
least turn, to point where the library's point at rest, because there the two rest poses really differ (arms out or down,
fingers straight or curled, legs together or apart). The spine, head, collarbones and feet keep their own rest pose: those
bones point differently only because the two rigs place them differently (a collarbone that starts at the front of the
chest, a foot bone from ankle to ball in a high heel), and swinging them would bend the body or flatten the heel. The hips
move as the library's do, scaled by the ratio of hip heights, and are then lifted or lowered so that the lower foot is as
far above its rest height as the library's lower foot. Bones the library has no match for (the jaw, the prop bones) follow
their parents. On the medium skeleton each forearm's roll about its own length goes to the hand, and half of it to the
forearm's twist bone, and the upper arm's twist bone takes back half of the upper arm's roll, as in our own clips. A test
copies clips onto an exact copy of the library's own rig and checks that every bone turns exactly as in the library.

## Steps

1. **Normalise.** Scale to `--height` (1.7 m by default), feet on the ground, centred. Image-to-3D exports face -Y after
   import, the way Blender's front view looks.
2. **Joints from pose keypoints** (`--keypoints`; `npcgen/posekeys.py`, `npcgen/blender/pose_views.py`,
   `npcgen/blender/rig_keypoints.py`). Blender renders the normalised model textured, orthographic, from the front and from
   each side, 1536 x 2048 (3:4, SDPose's own shape, so nothing is stretched), in about 10 s. SDPose (ComfyUI's
   SDPoseKeypointExtractor, the whole-body model: 17 body, 6 feet, 68 face and 2 x 21 hand points) reads the three
   renders and a close-up of the face cut from the front one (2.2 times the face's size; its jaw, lips, eyes and brows came
   out well even behind sunglasses). Then:
   - Body joints (shoulders, elbows, wrists, hips, knees, ankles): across from the front view, height the mean of the front
     and side views, and depth the middle of the mesh around the joint, as the outline rig did. The side views' own depth is
     not used: these models' clothes are thin shells, and it put Laura's hips at the front of her dress and Imani's two hips
     on opposite faces of her trousers.
   - Fingers: from the side view that sees the hand (from the front the fingers overlap), and across from the middle of
     the mesh's vertices on the line through each point, in the outermost 5 cm (the hand hangs outside the thigh or skirt).
     A point more than 3.5 cm across from the hand's middle (its line met the trousers behind the hand) takes the middle.
     A finger SDPose draws longer than the mesh's (Devi's short, curled fingers: the fingertip points hung 3 cm below the
     lowest vertex of her hand) is followed from its last sure joint to where the mesh ends, and its remaining joints are
     spaced up to there.
   - Feet: heel and toes from the side view; the ball of the foot 72% of the way from heel to toes, just above the sole
     (so a high heel gets a steep foot bone).
   - Hip, shoulder and neck heights for the spine come from the hips, the shoulders and the narrowest body run between the
     shoulders and the chin. The arm's track (for the arm clean-up, step 5) follows the keypoint arm down to the fingertips.
   - A limb whose points score under 0.3, or come out in the wrong order, keeps its outline joints (the report says so).
   ComfyUI's node calls the hands the other way round (its "right hand" is the character's left, while its body points are
   right): the front view decides which hand is which by the wrists they start at, for all three views.
   - Clean hands (the clean-up's option, `docs/model-cleanup.md`): the model's hands were built by NPC Forge, and their
     21 points are kept (`model_3d.refined.hands`, `rig3d.run(hands=...)`, `--hands`). They replace SDPose's for the
     wrist, the hand's end and the finger bones, so the bones sit exactly in the new fingers; the outline rig (no SDPose)
     takes them too. The new hand's vertices go to the hand bone (fading in over 2 cm past the join) instead of taking
     the bone-heat proxy's weights, which come from the generator's model and its mitten, and the arm clean-up below
     never takes them off the arm; past the wrist, clothes within 6 cm of the new hand's skeleton do not follow the arm,
     so clothes the old hand was fused to stay with the body (further off, a wide sleeve hanging past the wrist still
     follows it).
3. **Joints from the outline** (without keypoints, and for the torso's depth). The vertices are binned into a front-view
   grid, and each row is split into solid runs.
   - Neck: the narrowest body run above 0.7 of the height. Shoulder joints sit 0.045 of the height below it.
   - Arms: from the top of the gap under each arm, follow the outermost run down to the fingertips. The wrist is a hand's
     length (0.1 of the height) back up the arm, and the elbow is just past halfway to it.
   - Hips: 0.04 of the height above where the legs part. A skirt, robe or coat hides that, and the proportions of the neck
     height are used instead (the report says so). A hem is told from a crotch by the gap between the legs: at a crotch it
     closes gradually, at a hem it is cut off while still wide.
   - Knees are 52% of the way from ankle to hip; feet and toes come from the lowest vertices.
   - Depth: each joint sits halfway between the front and back of the mesh around it.
4. **Skin weights.** Blender's bone-heat method on a proxy, then copied to the full mesh by nearest surface. The proxy is a
   voxel remesh (voxel = height/260), keeping only the largest piece, decimated to 60k faces. If bone heat then gives most
   bones no weight at all, the proxy is made again at height/200, /320, /230 and /290 in turn, and the step stops with an error
   naming the sizes if none works (see the traps below). The medium skeleton's extra bones sit this out (the voxels fuse
   fingers closer than 6.5 mm) and share out their parent bones' weights afterwards (step 6), so everything else is weighted
   exactly as on the low skeleton. Bones nothing ends up weighted to (a finger whose hand is hidden in the clothes) are named
   in the rig's notes: they move no part of the model.
5. **Arm and leg clean-up.** Below the armpit, arm bones may only move vertices inside that arm's own track, or within 2 cm
   of the hand's keypoint skeleton. Vertices that lose every weight go to the nearest body bone. Faces where the arm weight
   jumps by more than 0.5 across a single face are deleted: they are where the generator welded a resting hand to the hip or
   skirt. Below the knees, a vertex of one leg keeps no weight from the other leg's bones: feet or shoes standing together
   are one blob in the voxel proxy, and bone heat gave part of each shoe to the other foot, so a clip whose feet part tore
   strips off one shoe and dragged them after the other (Laura: 4545 vertices below 60 cm had over a quarter of their leg
   weight from the other leg). A vertex is of the leg whose calf, foot or toe bones (or the line down from the ankle to the
   floor, for a heel) it is nearer, within 7 cm. Where the shoes touch that is a close call, so a vertex goes with its piece
   of the mesh when it can: a generated model's lower legs are hundreds of separate pieces (336 on Laura), and a piece whose
   clearly placed vertices are nearly all of one leg goes wholly to that leg. Faces left joining the two legs are deleted
   (7 on Laura). The middle of a long skirt or robe, more than 7 cm from both legs, still stretches between them.
6. **Medium skeleton.**
   - Fingers: a vertex of the hand past a finger's knuckle (the thumb: past its base in the palm) and within 2-3 cm of that
     finger's line goes to it, and along the finger to its first, second or third bone, blended over a fifth of a bone at
     each joint; between two fingers that nearly touch, by distance (3 mm soft). Each finger bone's X axis is its curl
     axis: turning a finger about +X curls it towards the palm.
   - Twist bones take the forearm's weights towards the wrist and the upper arm's towards the shoulder.
   - Jaw: from the face points, its joint just inside the cheek beside the ear, level with the top of the jawline. The
     lips are cut apart with one straight cut from inner corner to inner corner, through the middle of their slight curve
     (1.8 mm at most on Laura), square to the line of sight, so it cuts every layer of these hollow surfaces at once; a dark
     mouth (a flattened ball in its own material, upper half on the head, lower half on the jaw) sits behind them. The jaw
     takes the head's weights below the lips and in front of its joint, fading out under the chin and towards the ears; on
     the cut, each side goes wholly with its own lip, and the lower lip pulls less towards the corners, so the mouth opens
     lens-shaped. A model with the detailed head attached (`docs/detailed-head.md`) is rigged like any other: SDPose finds
     the jaw and lips of its real face better, and the cut goes through the head's own lips (Devi, 2026-09-30: 60 bones, the
     jaw at 0.28 rad parts them); the seam where the patch meets the body's jaw line tears open visibly (still, with the outer skin only), because the weights come
     from the generator's own shape by nearest surface and patch and body do not agree there (a fix would take the weights from
     the head model where the patch is).
7. **Clips.** The library's (above), or keyframed in code: 30 fps, in place (no root motion). Each clip is an action in its
   own NLA track, and the glTF exporter writes each one as a named animation. The basic clips:
   - Idle: a 4 s loop. One slow breath in the chest and shoulders, a small drift of the head, arms lowered from the A-pose.
     No hip sway: an earlier version rocked the pelvis side to side and looked like swaying.
   - Walk: a 1 s loop.
   - Wave: 3 s, right hand. The upper arm goes out to the side, a little forward; the forearm points up with the palm to
     the front and swings side to side from the elbow. The pose is solved from those goal directions, and the upper-arm
     twist is picked so the elbow only bends. The first version raised the arm sideways with the palm facing out, so it
     waved at someone standing to the side.
   - Medium: the fingers relax a little past the model's own pose (breathing with the idle), the waving hand opens flat,
     and each forearm's roll about its own length goes to the hand, half of it to the forearm's twist bone, so the forearm
     turns gradually from elbow to wrist; the upper arm's twist bone takes back half of the upper arm's roll.
8. **Export.** GLB with the skin and animations; limited to 4 bone influences per vertex, as game engines expect. Every clip
   keys every bone, so an engine that adds to a bone's rotation each frame (the 3D stage's jaw) starts from the clip's pose.
   The mesh's armature deform is switched off while the exporter runs: it samples every frame of every clip and would deform
   the whole mesh at each one (Laura with the library's 85 clips: 85 s, against 40 s without; the file is byte for byte the
   same). Channels that never change (scale, most translations, the jaw in a clip) are written with two keys, so the 85
   clips are 3.3 MB of the GLB.

## Measured

2026-09-25, Clara Kowalski, Pixal3D medium, 700k faces, low skeleton from the outline:

| | |
|---|---|
| Time in the app | 37-43 s (without the 36 preview renders) |
| Proxy | 76k faces after the voxel remesh; 28 floating pieces dropped |
| Weights | every bone weighted, no unweighted vertices |
| Faces cut (hand welded to body) | 253 |
| Output | 41 MB GLB, 23 joints, the three PBR textures kept; Idle and Walk loop exactly (keys from frame 0); all three clips load and play in the app's model-viewer |

Imani Adeyemi (trousers, Pixal3D) rigged with no changes: hips found from where her legs part, 14 floating pieces
dropped, every bone weighted. Her raised arm in the wave pinches a little at the shoulder: plain linear skinning does that
above about 90 degrees.

2026-09-26, joints from SDPose, five Pixal3D models (Laura as generated, the others their cleaned-up game versions with the
original as `--shape`), checked by importing each exported GLB back and posing it the way an engine does (the wave, both
hands curled into fists by turning every finger joint +65 degrees about X, the jaw opened 0.28 rad):

| Model | SDPose scores (body / face / hands) | Rig time | Finger bones without vertices | Finger points unsure (left, right) | Faces cut (hand welded to body) | Jaw vertices |
|---|---|---|---|---|---|---|
| Laura Foster (as generated, long-sleeved dress, heels) | 0.92 / 0.95 / 0.94, 0.98 | 84 s | none | 0, 0 | 0 | 3613 |
| Airi Nakamura (game version, long coat) | 0.88 / 0.96 / 0.91, 0.96 | 88 s | ring_02_r | 7, 6 | 0 | 926 |
| Clara Kowalski (game version, skirt) | 0.92 / 0.9 / 0.91, 0.98 | 69 s | none | 5, 3 | 256 | 2055 |
| Imani Adeyemi (game version, trousers) | 0.91 / 0.95 / 0.76, 0.87 | 76 s | pinky_03_l, pinky_02_r, pinky_03_r | 4, 9 | 0 | 2321 |
| Devi Chaudhry (game version, trousers) | 0.92 / 0.95 / 0.94, 1.0 | 72 s | pinky_02_l, pinky_03_l | 10, 6 | 11 | 1425 |

- **Laura Foster** (long-sleeved dress, high heels): from the outline the wrists were at her elbows (the arm could not be
  followed past the cuff, where the sleeve touches the dress), the hips 18 cm low (0.74 m against 0.91 m: the dress's hem
  taken for the crotch) and the ankles and knees placed for flat feet. Her wave raised the whole arm as one piece. From
  SDPose the forearm lifts and the hand waves.
- **Airi Nakamura** (a long coat): the legs are hidden, and SDPose places the hips, knees and ankles through the coat where
  the outline used proportions.
- On Clara and Imani both methods agree to 2-3 cm; SDPose's hips are 3 cm higher on Imani (0.87 m against 0.84 m), which
  is anatomically closer for her height.
- The jaw at 0.28 rad parts the lips over the dark mouth on all five; in the 3D stage's own three.js (Laura, rigged from
  the app) the dark area of her mouth grew by 42%. Without the cut, the same turn stretched her lower face into a smear.
- The grey inside of a raised sleeve (Laura) shows on every rig: no picture sees it, so the clean-up has no paint for it.
- On a cleaned-up game version (100k faces for the whole body) a hand has only a few hundred vertices, about 200 of them
  below Devi's knuckles, so some fingertip bones get none and the fingers bend coarsely; the report lists the finger
  points SDPose was unsure of. The model as generated (700k faces) has fingers to spare.
- Where a hand rests against the clothes, the clean-up painted the hand onto the clothes behind it (the pictures show the
  hand there), so fingers that curl or an arm that swings leaves painted fingers on the trousers (Devi, Airi). That is for
  the painting step to fix, not the rig: with Clean hands it is (the pictures' hands are kept off what lies behind them).

2026-09-27, the same five with the animation library (medium skeleton, SDPose, `rig3d.run` as the app calls it):

| Model | Rig time (with 3 clips before) | GLB | Clips | Vertices that lost the other leg's weights | Faces cut between the legs |
|---|---|---|---|---|---|
| Laura Foster (feet together, heels) | 133 s (84 s) | 46.2 MB | 85 | 21521 | 7 |
| Airi Nakamura (long coat) | 131 s (88 s) | 38.2 MB | 85 | 351 | 0 |
| Clara Kowalski (skirt) | 124 s (69 s) | 25.3 MB | 85 | 0 | 0 |
| Imani Adeyemi (trousers) | 134 s (76 s) | 27.4 MB | 85 | 53 | 0 |
| Devi Chaudhry (trousers) | 134 s (72 s) | 26.5 MB | 85 | 79 | 0 |

The low skeleton with the library: 68 s on Laura from saved keypoints (no renders). Checked by importing the exported GLBs
back and rendering: every one of the 85 clips on Laura, ten (idle, walk, sitting, phone, drinking, arms folded, picking
up, watering, dancing, sword idle) on all five, and close-ups of the feet mid-stride, the folded arms and the phone call.
Before the leg clean-up, Laura's walk dragged strips of one shoe after the other foot; after it, both shoes stay whole.
Folded arms fold in front of the chest even in Airi's coat. The phone hand lands beside the head a few centimetres out
from the ear: turning bones copies angles, not where a hand ends up, and our arms and heads are not the mannequin's.
Hands hold nothing in the sword, pistol, shield, torch and lantern clips.

2026-09-27, **SkinTokens** tried as the source of the skin weights (VAST-AI's learned rigging, MIT, 1.6 GB, through the
ComfyUI-SkinTokens nodes, in its mode that keeps the model's own skeleton). Our rigs went in without their clips. It gives
its bones back unnamed, in its own order, without the root and the prop bones, so each was matched to ours by where its
head is and its weights were copied onto our rig vertex by vertex (the same mesh), keeping our skeleton and clips. About a
minute per game version, 2.5 minutes for Laura's full model, little VRAM. Rendered in the same frames as ours:

- Medium skeleton, game versions: it kept 54-57 of the 60 bones and gave each hand weights on the other hand's finger
  bones (Imani's right hand followed three left-hand finger bones), so clips that move the fingers drew spikes from hand
  to hand (Clara, Imani, Devi). On Laura's full model its fists were as good as ours.
- Jaw: its weights tilt the whole lower face and the mouth stays shut; it knows nothing of the lip cut.
- Body (low skeleton): Laura's armpits were smoother with both arms overhead, but her shoes bled into each other in a
  crouch (522 vertices with over a quarter of their leg weight from the other leg, against 194) until our leg clean-up
  ran on its weights. Clara, whose hands the generator welded to her skirt: with our weights a sheet of skirt follows her
  arm when she picks something up, with SkinTokens' it does not. Airi: her raised arm dragged a wedge of her coat down her
  side. Imani and Devi: no visible difference. Its own voxel post-process tore Laura's knees and jacket.

It is not used: the fingers and the jaw need our rules, and on the body it is better in one case of five and worse in
another. What it showed is a weakness of our arm clean-up where a hand rests on the clothes (Clara).

## Traps found on the way

- **Floating bits make bone-heat fail for every bone**, not just nearby ones ("Bone Heat Weighting: failed to find a
  solution"). The voxel proxy of a Pixal3D model has about 28 of them; keeping only the largest piece fixed it. Scaling the
  mesh 10x (the usual advice) did not help.
- **Bone heat can fail for every bone at one voxel size and work at the next.** Two of the Trellis2 generator's models (Airi and
  Natalie, 2026-10-02) came out with no skin weight on any of the 22 body bones at height/260 (the jaw bone held only its
  mouth ball), and the step still reported success: a skeleton that does not move the model. The proxy had no floating pieces
  and no holes; height/200 and height/320 both worked on Airi. Nothing in the proxy explains it, so the step now tries the other
  sizes and fails loudly if none works. Devi's model, which worked at height/260, is weighted as before.
- **Headless Blender cannot open an OpenGL context on this AMD/Windows machine.** Workbench and EEVEE renders crash in
  `GPU_fence_signal`, so previews and the pose renders use Cycles on the CPU.
- **The glTF exporter times keys by the scene frame rate** (24 by default). Keys spaced for 30 fps played 25% slow until the
  scene was set to 30 fps.
- **Round half to even.** Mapping a row's centre height back to a row with `round()` lands on the next row half the time.
  Every other row of an arm then kept its body weights, and the arm looked shredded. Rows are floored now.
- **Blending into a twisted pose with a single slerp takes the arm behind the body.** The wave's upper arm is swung and
  twisted; slerping the whole rotation swung the arm out behind her on the way up. It is lifted along the direct arc, with
  the twist added gradually.
- **These surfaces are thin shells.** A finger is a hollow tube of skin about 5 mm thick, a dress a sheet of cloth: the
  first piece of mesh a ray passes is only the near skin, so depths come from the vertices around a point instead.
- **SDPose needs the person around a hand.** A close-up of a hand alone scored 0.2-0.4; the hands in the whole-figure
  renders (about 120 pixels high) scored 0.9-1.0 from the side.
- **Following the lip line with a cut per segment left bridges of skin** where the segments met, and judging which lip a
  vertex belongs to against the curved line instead of the straight cut hung the upper lip's edge across the open mouth.
- Hands resting against the body merge with it in the outline, so without keypoints the fingertips are estimated from the
  expected arm length (the report notes it).
- **Swinging every bone to the library's rest pose bends the body.** Only bones whose rest poses really differ between the
  two rigs (arms, hands, fingers, thighs, calves) are lined up; a collarbone or a foot bone points elsewhere only because
  the rigs place it elsewhere.
- **The glTF exporter deforms the whole mesh at every frame it samples**, although it only reads the bones: with 85 clips
  that was most of the rig's time, until the deform was switched off for the export.
- **Blender 5.2 crashed** (access violation) when a script set `animation_data.action_slot` itself. Assigning the action
  picks the slot named after the armature, and the clips are built that way.

## In Godot

Measured with Godot 4.7.2 (headless, 2026-10-06) on a designed body (the Body Designer add-on), which carries the same 85 clips by the same copier as a generated
character's rig, and on Quaternius' own file where noted. The file imports as a `Skeleton3D` (the bones in the order of the hierarchy, not of the glTF skin: match
them by name) with the meshes under it and an `AnimationPlayer`; the rest pose is the file's inverse bind matrices to 0.0001 mm. These come from Godot's import, not
from the file, and a game wants to know them:

- **The editor's import does not reproduce the clips exactly.** It bakes each clip again at its `animation/fps` setting (30 by default) and drops keys it judges
  redundant: 13 of the 14 keys of a sword swing's forearm track survived, and the missing one was not redundant. Over the 85 clips, at three instants each, the worst
  bone of a clip was a median 0.5 mm (female, low skeleton) and 0.9 mm (male, medium) off, 2.2 and 2.7 mm at the 90th percentile, and only 2 and 3 clips were more than
  5 mm off: the two sword swings (21 and 82 mm at the worst sampled instant), `Dance_Loop` (9 and 10 mm) and `Slide_Exit` (5 mm). A higher import FPS helps a little
  (240: 8.7 mm at worst) and the per-animation optimizer switch changed nothing; Quaternius' own file loses keys the same way. Loaded at run time
  (`GLTFDocument.append_from_file`) the file matches the independent evaluation to 0.0015 mm and 0.07 degrees for every clip and bone sampled, so a game that needs
  the exact swing can load the GLB at run time.
- **Godot loops a clip only by its name.** It strips a `_Loop` or `-loop` suffix and loops the clip (29 of the 85 here), and ignores `extras.loop`, so our `Idle` and
  `Walk` (which the library calls `Idle_Loop` and `Walk_Loop`, renamed for the 3D stage) do not loop until the game sets their loop mode.
- **A clip does not reset the bones it has no track for.** The import removes tracks that never leave the rest pose, so after `Walk` a clip that leaves the legs alone
  (a wave) leaves them where the walk left them. Reset the skeleton (`Skeleton3D.reset_bone_poses()`) or give the player a `RESET` animation (Import As RESET) and turn on its
  `deterministic` property.
- Godot reorders and merges vertices at import (`generate_lods`), so vertex N of the scene is not vertex N of the file.
- Three of the clips are 0.17 s still poses by design (`Pistol_Aim_Down`, `Pistol_Aim_Neutral`, `Pistol_Aim_Up`).
- **Spring bones are the engine's job.** A designed body made with Swinging Long hair or Swinging skirt or dress has extra bones (`secondary_hair_*`, `secondary_skirt_*`, `secondary_dress_*`) that no clip keys, with their settings in `extras.forge_secondary` on each chain's first node; `examples/godot/forge_secondary_motion.gd` makes a `SpringBoneSimulator3D` from them (checked on 4.7.2: the chain bones move up to about 100 mm under a Dance clip and the body's own bones are untouched: the Body Designer add-on's own notes). The file has no colliders, so a game that wants the hair or the hem kept out of a body part has to add its own. A female body made with Soft-body motion has the same kind of chains at the chest and the butt and, when asked, the belly and the thighs (`soft_chest_*`, `soft_butt_*`, `soft_belly_*`, `soft_thighs_*`, with `kind` `chest`, `butt`, `belly` or `thighs`, a `max_angle_deg` that the helper enforces and, for the belly and the thighs, a `center_bone`), and the helper's `attach(skeleton, ["chest"])` gives one part its own simulator, whose `influence` is a live strength control (the Body Designer add-on's own notes).

## Not done yet

- Facial expressions (brows, eyelids, cheeks, lip corners) and eyes that look around: the generated face has no eyeballs,
  and its eyes are paint, so they need the face rebuilt (separate eyeballs) before bones can move them (the "high" tier).
- Coat chains, and chains of spring bones on a model generated from pictures: a designed body's Long hair and its skirt or dress can carry them (the Body Designer add-on's own notes), and a female designed body's chest, butt, belly and thighs (the Body Designer add-on's own notes); nothing else does.
- Painting the body behind a resting hand from the other pictures without Clean hands (see the painted fingers above), and
  behind a forearm held against the body (an orange stripe of Clara's arm on her skirt).
- Shoulder helper bones (or dual-quaternion skinning) for arms raised above the shoulder.
- Other body plans (taur, quadruped, merfolk, serpentine); `rig3d.SUPPORTED_MORPHOLOGIES` blocks them.
- Props for the library's clips that hold something (sword, shield, pistol, torch, lantern, a carried box): the hands
  hold nothing. The medium skeleton's prop bones are where a prop would go.
- Hands and feet pinned where they belong: the library's clips are copied by turning bones, so where our limbs are longer
  or shorter than the mannequin's, a planted foot slides a little and a hand meant for the ear or a table lands a few
  centimetres off. IK on the hands and feet would pin them.
- The 3D stage moves characters at its own pace; `clip_info`'s `speed_m_s` is the pace that matches their steps.
- A long skirt or coat follows the thighs when sitting (plain skinning); skirt bones would fix that.
- Where a hand was welded to the clothes, part of the clothes still follows the arm (a sheet of Clara's skirt when she
  picks something up) unless the model has Clean hands: the arm clean-up judges the arm's allowance from the front outline
  and from 2 cm around the hand's keypoints, and the clothes around a fused hand fall inside both.
- Character height: every character is 1.7 m. `rig3d.run(height_m=...)` takes another height (the rig step passes none and no character record stores one).
