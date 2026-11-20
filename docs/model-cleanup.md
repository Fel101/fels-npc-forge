# 3D model clean-up and painting

**Clean up + paint from pictures** in the 3D card (or `--refine-3d`) runs the `refine_3d` job step (`npcgen/cleanup3d.py`),
which turns a Pixal3D model into a game version (a Hunyuan model gets a texture layout first: see *Painting Hunyuan models*). It never runs on its own: a future generator or LoRA may not need it.

1. **Hidden inner skin removed** (`cleanup3d.inner_skin`, NumPy).
2. **Painted from the turnaround pictures** (`npcgen/paint3d.py`, NumPy and Pillow).
3. **Simplified to 100k faces** by Blender, with the colour, occlusion/roughness and full detail baked from the full
   model into new maps (`npcgen/blender/game_lod.py`, Cycles on the CPU), plus a preview still.

The generator's model is kept as `model_3d_original.glb` (the model's `raw` file). A redo always starts from it, and the
rig step uses it as the shape to work out joints and skin weights on (below). Try it by hand:

```bash
python -m npcgen.paint3d library/<id>/model_3d_original.glb library/<id>/sheet painted.glb --debug debug/
blender -b --factory-startup --python npcgen/blender/game_lod.py -- in.glb out.glb --faces 100000 --poster still.png
```

## Why

Measured on Clara Kowalski (Pixal3D medium) against the front picture it was made from:

- The picture's face is about 158 pixels across and readable.
- The model's face is not. Pixal3D's colour comes out of a texture grid decoded by its texture model and baked to UVs, so
  the eyes became black rings, Imani's mouth a smudge, and Clara's green skin lost its colour. The existing head-detail
  option only changes what that grid is conditioned on.

Projecting the pictures' own pixels gets past that limit. The mesh itself is a thin hollow shell: slices through the
legs, chest and head show a second skin a millimetre or two inside every surface, facing inwards. About half the faces
are never visible from any of 64 directions all round.

## Painting (`paint3d.paint`)

- **Lining up.** Each picture gets a scale and offset that maximise the overlap of the model's outline with the
  picture's (IoU, searched coarse to fine on one pre-rendered outline). The head then gets its own nudge (the top 16%
  of the height), blended into the body fit down the neck.
- **Visibility.** A depth buffer per view at twice the picture's resolution. A texel counts as seen if it lies within
  0.6% of the height of the nearest surface, and no surface within 2 picture pixels is 3% of the height nearer (an
  occluding edge).
- **Weights.** Weight comes from how squarely the surface faces the camera. The facing is judged on normals smoothed over
  about 1 cm, so the walls of small ridges take the direction of the surface they sit on. Each view is also scaled by how
  well its outline matched, relative to the best view.
- **Unseen surfaces.** Where no picture sees a surface, it takes the nearest painted colour within a few millimetres. If
  there is none, it keeps the generator's colour, shifted to the pictures' local tone.
- **Seams.** UV padding gets the new colour of the island next to it, and triangles too thin to hold a texel centre are
  sampled at their centre and corners.

### Raised views (`front_high`, `back_high`)

The four turnaround views are level, so a surface that faces up (the dome of a helmet, a crate's lid, a boot's opening) is seen only
edge-on and comes out smeared and blotchy. The Items tab ([items.md](items.md)) therefore draws two more pictures with the camera 45
degrees up, in front of the thing and behind it, and `paint` takes them as two more views named `front_high` and `back_high`. Each
view has its own **up axis** (`paint3d.UPS`; the four level views keep +Y) in the outline fit, the depth buffer and the colour
lookup, and a raised view has no head fit of its own. The convention is the same as the level views' (picture right x picture up =
towards the camera). Checked on a cube drawn by hand from both raised cameras (the top painted from the top face, the near side from
the near face, the bottom left alone) and on a helmet: painted from the four level pictures its dome had a dark patch, white blotches
and smeared streaks; with the two raised pictures added it was a continuous hammered surface (the same shape, 443 s against 410 s). A raised
picture is the Edit model's guess at a 45-degree camera, and its outline can disagree with the shape (the helmet's front-high picture
matched at 0.75, its back-high at 0.87): the weight a picture gets already falls with its outline match, so a poor one counts less.

## Measured (2026-09-26)

| | Clara (skirt) | Imani (trousers) |
|---|---|---|
| Faces as generated | 696,684 | 691,755 |
| Inner skin removed | 234,227 (34%) | 216,587 (31%) |
| Game version | 99,993 faces, 19.5 MB | 99,997 faces, 21.5 MB |
| Time (inner skin + paint + game version) | 30 + 36 + 12 s | about 90 s in the app |
| Outline match front / left / back / right | 0.94 / 0.87 / 0.93 / 0.84 | 0.92 / 0.92 / 0.83 / 0.91 |
| Rig on the game version | 22 s, every bone weighted, 136 faces cut | every bone weighted, no notes |

- **Painting doesn't change from run to run:** a repeat run gave the same texture pixel for pixel.
- **The game version looks like the full model:** lit renders at 100k faces look the same as at 460k faces.
- **The baked normal map carries real detail:** a spread of about 25 levels around flat blue.

## Traps found on the way

- **`side_left` is the character's own left side**, with them facing the left of the picture. The outline matched 0.87
  this way and 0.69 mirrored.
- **Pixal3D's input is fixed at 1024 × 1024 per view** (`comfy_extras/nodes_trellis2.py`). Its shape stages read 16-pixel
  image features at 512 and 1024. Our 576 × 1152 views already give the figure about 1040 px of height, so bigger or
  upscaled pictures cannot give it more detail.
- **The dark rings around some eyes are sculpted geometry, not paint.** Pixal3D read eye makeup as shape and built a
  thick loop under each eye. Eight rounds of Taubin smoothing barely change it, and anything stronger would melt the nose
  and lips.
- **The rig's voxel proxy needs a closed surface.** On the game version, whose inner skin is gone, it fell into 307
  pieces and left the legs and pelvis without weights. The rig now takes the original as `--shape`: joints and the
  weight proxy come from it, and the weights are copied onto the game version.
- **UV padding must take the new colour, not the change.** Spreading the change left the generator's black padding
  showing as thin lines across the face.
- **Imani's head sat 9 picture pixels off the whole-body fit**, which doubled her lips until the head got its own fit.
- **No `@`:** `npcgen/*.py` must not use numpy's matrix product (see `anim._apply`), so the projections are written out as
  `paint3d.dot3` (the scripts in `npcgen/blender/` run in Blender's own Python and do use it).

## Tried and dropped

- **A smooth warp of each picture onto the model** (block matching against the generator's own colours, up to 8 px). It
  cannot move a fringe off an eye without moving the eye too, and it cost 7 s per view.
- **Leaving out a view where the picture shows the surface behind a thin layer** (a fringe over the forehead). It
  brought Imani's doubled lips back and hardly changed the fringe.
- **Removing every hidden surface.** From one direction, the inside of a hollow limb looks the same as the gap between a
  vest and the body, so the body under clothes would have gone too. A walking leg can show it under a skirt.

## Sharper face (`npcgen/headdetail.py`)

Only from its own button (or `--sharp-face`). Measured on Clara and Imani (2026-09-26), the face was held back twice:
the pictures show it about 145 pixels across, and the generator's texture gives every surface the same density, about
190 texels across the face. Sharper pictures alone could gain at most 1.3×, and a bigger texture alone nothing. So:

1. **Close-ups.** Each turnaround view's head (a square a quarter of the figure's height) is upscaled 4× in ComfyUI with
   an upscale model. It lines up with its view pixel for pixel, so paint3d places it with the view's own fit, scaled, and
   lets it outweigh the view 20 to 1 where it sees, fading out near its edges.
2. **Head layout.** The colour and occlusion/roughness textures become twice as wide: the left half is the generator's
   texture as it was, the right half the head alone (the top 18% of the height), unwrapped afresh by xatlas. The
   generator's normal map stays on its own layout as a second UV set until the game version bakes a new one at the same
   2:1 shape and drops that set.

| Clara | Before | Sharper face |
|---|---|---|
| Head texels per unit of height | 1,367 | 3,130 |
| Time (close-ups + inner skin + layout + paint + game version) | 30 + 36 + 12 s | 13 + 35 + 45 + 65 + 20 s |
| Game version | 19.5 MB, 2048² maps | 29 MB, 4096 × 2048 maps |

Imani, from the app's button: head 1,214 -> 3,285 texels per unit, and the skeleton made again on it with every bone
weighted and no notes (32 MB rigged).

Lit renders: the iris rim, eyelid line, lip edges, wounds, Imani's neck tattoo and hair strands are clearly crisper; the
sculpted eye rings are now the most visible flaw.

**Upscaler.** RealESRGAN_x4plus (BSD-3-Clause) is the default: crisp edges and nothing invented, a little smooth.
4x-UltraSharp keeps more skin, wound and tattoo texture, but it invents a little (a white highlight under Imani's nose,
which shows on the model) and is non-commercial. `"face_upscaler"` in config.json picks it.

**Tried and dropped on the way:**
- **Mage-Flow-Edit redrawing the close-ups** (strength 0.35, 0.55, 1). Even at 0.35 it turned Imani's lips red in the
  side view only; higher, it added eyelashes and restyled the line work. A change in one view but not the others shows
  as a mismatch on the model.
- **4x-ESRGAN.pth**: barely sharper than a plain resize.
- **Repacking the whole texture with Blender.** After the clean-up the generator's layout is about 6,500 islands, 1,600
  of them a single triangle (the mesh has about 4,800 small loose fragments). Blender's `smart_project` made 18,000
  islands, even on a heavily smoothed copy, and `pack_islands` gave different, overlapping layouts from run to run with
  the same settings. xatlas on the head alone: about 4,700 charts, no overlaps, 25 s.
- **xatlas repacking the generator's own charts** with the head scaled up: it ignores the charts' UV scale, so the head
  gained nothing.

## Baking the game version's colour

The game version first reused the full model's colour texture through its simplified UV layout. Simplifying stretches
that layout, and once the paint had real detail (the sharper face) it broke into visible triangles: shards across the
eyes, brows and nose bridge of every version, plain clean-up included. The colour and occlusion/roughness maps are now
baked from the full model onto the game version (each texel takes the value on the full surface at that point), like
the normal map. It costs about 12 s.

## Smooth eye rings (`npcgen/eyes.py`)

Only from its option (or `--smooth-eyes`), and only for models that have the rings. On Clara, Pixal3D turned her dark
eye make-up into a structure of its own round each eye: a slice at eye height shows a closed loop of surface about
1.3 cm across, with walls running back into the head, so from the side she wore goggles.

1. **Finding the eyes.** LM Studio's vision model marks the two eyes on the front picture's head crop (768 px). On
   Clara, Imani and Devi, `qwen/qwen3.6-35b-a3b` and `qwen/qwen3.8-27b` agreed to within half a percent of the picture,
   on plain crops as well as upscaled close-ups; `google/gemma-4-e4b` misplaced Clara's and Devi's. About 4 s a question
   once loaded, 30-40 s to load. The model is loaded only for the question (context 4096) and unloaded afterwards, under
   NPC Forge's one-model-on-the-GPU rule. The answer is checked: two boxes, level, of similar size, side by side.
2. **The region.** Round each eye, reaching 3 half-widths towards the temple and 1.8 towards the nose, 2.4 half-heights
   down the cheek and 1.2 up (the brows stay). It takes in the surface near the face there, and everything joined to it
   inside the region however far it stands out (the loops), but not hair, which is not joined there.
3. **Relaxing.** The mesh's copies of each point along texture seams are joined first (or relaxing would tear the seams).
   The region starts on a smooth surface fitted to the face in a band just outside it, then 300 rounds move every region
   point to the average of its neighbours while everything outside stays put: a membrane pinned to the face, with no
   seam where it meets it. Pressing the region onto the fitted surface alone left arcs where the two did not meet, and
   fitting the surface on a band round the eye itself fitted the rings, which lie there.
4. **The walls.** The loops' walls end up folded flat in the membrane, some facing backwards, which shaded as dark
   flecks; they are turned to face forward. Removing them instead left holes where they were the only surface.
5. Smoothing the eyes also gives the head its own texture (as the sharper face does), so the smoothed surface gets fresh
   texture room.

Measured on Clara: about 12,500 points moved, 10 s; the goggles are gone from every angle and the pictures paint the
eyes onto the smooth surface. A few small dark flecks remain in the eye paint.

**Why it is an option you tick, not something detected.** Imani's and Devi's eyes are sculpted eyelids, and smoothing
them flattened their eyes. Three measures of the shape were tried and none told Clara apart from them: the mean
distance of the eye region from a fitted face (0.008-0.009 of the height for Clara, 0.006-0.009 for them), the share of
the region with three or more layers of surface (0.12-0.13 against 0.11-0.25), and how far the region stands in front
of the face.

## Painting Hunyuan models (`npcgen/unwrap.py`)

Hunyuan3D v2.1 makes shape only: a surface-net mesh (about 130k faces for a person at octree 256) with no texture
coordinates, no material and no normals. `refine` notices that (`unwrap.needs_texture`) and, before painting:

1. **Unwraps it with xatlas** in two parts, laid side by side on one 4096 × 2048 texture: the body on the left half, the
   head (the top 18% of the height) on the right, the shape a sharper face's head layout has, so the close-ups paint
   straight onto it. On Clara: 1.7k head charts in 6 s, 4.6k body charts in 40 s; about 2,100 texels per unit on the head,
   1,000 on the body.
2. **Gives it normals.** The surface net joins three or four faces along a third of its edges (hair, hems), and plain
   vertex normals there cancel or point backwards: 5.3k faces on Clara had a corner normal facing away, which rendered as
   black slivers in her hair. Those corners take the mean of the faces round the vertex within 60° of their own face;
   every other corner keeps the smooth vertex normal. Averaging every corner that way showed the voxel steps as flat
   facets on the nose, and so did working the normals out after unwrapping (every chart edge became a crease).
3. **Paints from blank** (`paint(blank=True)`): with no generator colour to keep, a texel no picture sees takes the mean
   painted colour within a few millimetres, then a few centimetres, then the nearest further out.

It needs all four sheet pictures; the plan queues the missing ones before the clean-up.

Any other model with no texture takes the same path: one imported from another workflow or made by the Trellis2 generator
([model-import.md](model-import.md)). Those are much denser, so the unwrap dominates: 14 to 15 minutes for the 695,000-face
Trellis2 shape of Airi (836 s alone, 920 s while the machine was busy), against 2 to 4 for Hunyuan's 130,000 to 280,000 faces.
A model that came with its own texture is imported as it is and takes the Pixal3D path (126 s for a 697,000-face one).

**The picture Hunyuan is made from matters most.** Made from Imani's sprite, in which she stands slightly turned, her model
matched the sheet's front picture at 0.807 and painting it put copies of her face on her cheeks (the side pictures'
profile landing on a head turned differently). Made from the sheet's front picture it matched at 0.934 and painted
cleanly. That is the **Hunyuan3D picture** choice (`three_d_hunyuan_source`, sprite by default so existing recipes are
unchanged).

What stays: the face is only as good as Hunyuan's shape of it. At octree 256 a voxel is about 7 mm, so a nose is a few
voxels wide; where its nose or hairline differs from the picture, the nose side shows through the cheek paint or a
nostril lands on the tip. The **Hunyuan3D detail** choice (`three_d_hunyuan_detail`) reads the shape from a finer grid:

| Clara, seed 7 | Faces | Generate | Paint + clean-up |
|---|---|---|---|
| Standard (octree 256) | 136k | 57 s | 128 s |
| Fine (octree 384) | 296k | 107 s | 234 s |
| octree 512 (not offered) | 500k | 195 s | 320 s |

Fine gives a visibly smoother nose, and the 100k game version keeps most of it; 512 looked much the same as 384 there,
for two and a half minutes more, so it is not offered. Lining the face up by the model's shading instead of the outline was tried: the
match was weak (correlation 0.24-0.31) and pointed at a shift of only 2-3 pixels, so the misplacement is the shape's.

## Match the hair (`npcgen/hairmask.py`)

Only from its option (or `--match-hair`). A generator often makes the hair bigger than the pictures have it: Imani's
Pixal3D fringe hangs lower than in her turnaround pictures, Devi's side locks fall over her cheeks, Clara's hair covers
her ears. Painted from the pictures, those strands took what the pictures show behind them: skin-coloured streaks, and
on Devi a ghost of her profile on a lock of hair.

1. **Finding the hair.** SAM 3.1 (ComfyUI's own `SAM3_Detect` node, asked for "hair") marks the hair in each turnaround
   picture, and in the bare model drawn grey exactly as that picture sees it (`paint3d.clay_view`, the same fit the
   painter uses, so the two line up pixel for pixel). A picture shows the whole figure, so a head is only about a
   hundred pixels wide: both are asked again on a close-up of the hair, about 5.5 times as large (SAM works at
   1008 × 1008), so the masks follow single strands. The first try without the close-up left skin on the thinnest
   fringe tips.
2. **What is not hair.** The model's ears and eyes are asked for too (on the grey close-up) and never count as hair:
   Pixal3D painted Clara's hair colour over her sculpted ears, and on Imani's Hunyuan model, whose hair and face run
   into one another, SAM's hair reached over her right eye. Loose bits at the edge of the model's hair mask are
   dropped.
3. **Painting.** A picture's colour counts as "behind the hair" where the model, drawn as that picture sees it, has
   hair and the picture has none. A texel that got more than 5% of its paint from there takes the colour of the
   pictures that were not refused instead, or where none is left, of the well-painted hair nearby (read trilinearly
   from a 3D grid, so it has no blocks); hair no picture sees (the underside of a fringe) takes the hair colour too.
   Which texels change is smoothed over about 3 mm of surface, so the change has no hard edge. Judged per picture, so
   where the pictures disagree among themselves (a temple the front shows as skin and the side as hair) they still
   blend as before.
4. **Short hair the shape does not show** (a crop the model has no strands for) is left alone: the rule only keeps
   skin off the model's hair, never hair off its skin.

Tried and dropped on the way: refusing a picture's colour texel by texel with no smoothing (hard, stepped edges where
a picture sees the surface edge-on); deciding hair or not for the whole texel from all views together (the pictures
disagree at the temples, and texels flipped one by one into specks); filling in cell-sized blocks (visible 8 mm steps);
turning a texel towards the hair colour by the refused share of its paint (a faint ghost of Devi's profile stayed on
her lock); protecting the pictures' eyes and brows instead of the model's eyes (Devi's side picture put her eye on
the lock hanging in front of it); a hair mask shrunk by 2 pixels (it cut off the tips of Imani's fringe).

Measured 2026-09-26 on five models, lit renders from five directions against the plain clean-up:

| Model | Plain clean-up | Match the hair |
|---|---|---|
| Imani, Pixal3D | skin-coloured streaks through the fringe, a skin wedge in its middle, skin on the temple locks | fringe and temples hair to the tips; face unchanged |
| Devi, Pixal3D | skin and a ghost of her profile on the locks over her cheek | hair; the ghost gone |
| Clara, Pixal3D | ears painted on the hair over her ears | hair; her right ear kept; her left ear, which the model has lower and further forward than the picture, is hair (the picture shows hair where the model's ear is) |
| Imani, Hunyuan3D (from the sheet) | skin patches on the hair at the temples and sides | hair; soft edges round the face; eyes untouched |
| Clara, Hunyuan3D (from the sprite, a ponytail the pictures lack) | skin smears and a ghost face on the extra hair | hair; the left ear as on Pixal3D |

On Imani (Pixal3D) the whole clean-up took 191 s against about 130 s: 24 questions to SAM (59 s with loading it) and a
longer painting step. The LM Studio check (Muse Glimmer 30B) of the result no longer named the "hair colour patches"
and "forehead artefact" it listed for the plain clean-up (score 7.0 against 6.5).

## Clean hands (`npcgen/hands3d.py`)

Only from its option (or `--clean-hands`). A generator reads each picture in 16-pixel squares, and in a full-body view a
finger is about 6 pixels wide: all nine library models came out with mittens, two or three lumps for five fingers,
although their turnaround pictures draw five clean fingers. Pixal3D's High setting cannot help (its grid is already
finer than a finger). A mitten cannot close into a fist, and it looks wrong close up. The option replaces each hand.

1. **Finding the hands.** Right after the inner skin goes, Blender renders the model for the pose detector (the rig's own
   `pose_views.py`), and SDPose finds the wrists, elbows and the 21 points of each hand. It also finds the hands in the
   turnaround pictures.
2. **Painting around them.** The pictures draw longer, straighter fingers than the mitten has, and in Imani's side
   pictures her hand and tattooed forearm are drawn a little off the model's: painting put finger-shaped skin on the
   trousers beside Devi's hands, a whole second hand on Imani's thigh and a forearm down Naomi's skirt. Now a picture
   gives no colour where it shows a hand or forearm in front of the surface: where the picture itself draws one
   (SDPose's hand, grown by about 1.2% of the picture's height, and the line from its elbow to its wrist, 4.5% of the
   height across), or within 3.5 cm across of one of the model's finger bones (4.5 cm of its forearm) at least 1 cm
   nearer the camera. The model's own hand and forearm (within 3 cm of its hand bones, 4.5 cm of the forearm's line,
   where the surface faces away from them) are left alone; clothes the generator pressed against a hand lie that close
   too but face the hand, and had kept the pictures' hand painted on them (skin on Naomi's black skirt, which the facing
   test cleared). Such surface takes the other pictures' colour where they see it properly; otherwise it runs on from the
   rest of its own piece of the texture (its UV island: behind Imani's hand the thigh's colour, not her forearm a few
   centimetres away), and where that piece has nothing painted, from the painted surface around it (never the model's
   own hand, whose skin came through); so does surface within 15 cm of a hand that no picture sees. The generator's own
   colour there had the hand in it too (pink blocks on Devi's trousers, orange ones on Mabel's dress, from its coarse
   colour grid), and a picture seeing the surface only edge-on gave Laura's coat a dark patch behind each hand from its
   outline. Where SDPose was unsure of the model's own wrist or elbow, the pictures' hand and forearm outlines are not
   used (nothing would keep them off that forearm); the test against the model's bones still is.
3. **The cut** (`npcgen/blender/hands.py`, on the game version). Each arm is cut at its narrowest cross-section (the
   convex hull of where its edges cross a plane across the forearm) from just above the wrist joint to a quarter of a
   hand past it, and on while it still narrows: past a sleeve's cuff (Devi), past a coat sleeve that covers the wrist
   (Airi, Laura). What hangs together with the cut beyond it is the old hand, except what runs on far past the wrist
   from a piece of the cut that does not go round the arm, where another piece does: the plane also cuts a thigh or
   skirt beside the wrist, along one side of the arm only, and flooding down the leg from there took Naomi's and Clara's
   resting hands for fused ones. A hand the generator really joined to the clothes (Briar's right one, in her rags) floods into them: then only
   what lies within about 4 cm of its palm's bones and 2.5 cm of its fingers' goes, and the holes left in the clothes
   are closed, each patch in the one colour most like the rest of its border (the first texel beside a hole could be the
   arm's skin: a skin-coloured patch in Naomi's black skirt). Small loose bits of the old hand that were not joined to
   it go too (they floated beside Airi's and Imani's new fingers).
4. **The new hand** (`npcgen/blender/hand_shape.py`). Distance functions (rounded cones for the finger bones and the
   wrist, a rounded box and ellipsoids for the palm and the balls of the thumb and little finger, smooth unions for the
   webs, the fingers kept apart), meshed with surface nets and remeshed on a voxel grid in Blender, so its surface is
   always closed. Typical adult proportions, relaxed and a little curled. Its length is a tenth of the height, or the old
   hand's reach if that is longer (a curled mitten reaches less than a relaxed hand); its wrist is squeezed to the arm's
   cut; it bends from the forearm's line to the hand's (at most 20 degrees; a thumb SDPose read as pointing backwards is
   turned round); it is simplified to about 3,000 triangles (about 2,300 are left once its wrist is cut off); a band
   of triangles joins it to the arm. It is cut no further out than 0.3 hand lengths past its wrist: its thumb joins the
   palm only behind about 0.37, and where the arm's cut lay further out (Laura's coat sleeves, at 0.33 and 0.39) the
   thumbs came away as loose pieces open at their root. There the hand comes that much further out of the sleeve instead,
   and its notes say so.
5. **Its colour**: the skin colour of the pictures' hands (the pixels inside each hand SDPose finds there, their typical
   colour), darkened in the hand's own creases (its ambient occlusion), lighter on the palm (more so on darker skin) and
   with nails, in a 512 × 512 texture of its own (material `npcforge_hand`). A bare arm's own colour runs a few
   centimetres past the join, so it shows no step. Nails and lighter palms only when the colour looks like skin: a glove
   or a green hand keeps one colour. Where the pictures show no hand clearly (or the model has no sheet), the colour comes
   from the old hand's paint instead, and the job and the card say so.
6. **For the rig** (`docs/rigging.md`): the new hand's 21 points, its wrist ring and its length are kept
   (`model_3d.refined.hands`, in the model's own frame), and the rig puts the wrist, the hand's end and the medium
   skeleton's finger bones on them, with or without SDPose for the rig itself.

If a hand cannot be found or joined, the rest of the clean-up is kept, the model keeps its own hands, and the job and the
card say why.

Measured on the nine library models (Pixal3D; 2026-09-28), the whole clean-up with Clean hands and then a medium rig, as
the app runs them: every new hand is one closed piece joined to its arm, every hand took the pictures' skin colour, every
rig bone has weights, and the fists close.

| | Natalie | Naomi | Imani | Clara | Devi | Laura | Airi | Briar | Mabel |
|---|---|---|---|---|---|---|---|---|---|
| Finding the hands (s) | 40 | 56 | 49 | 52 | 42 | 41 | 42 | 46 | 43 |
| Painting (s) | 58 | 63 | 77 | 86 | 70 | 75 | 80 | 89 | 79 |
| The new hands (s) | 14 | 14 | 18 | 17 | 17 | 18 | 14 | 15 | 15 |
| Whole clean-up (s) | 166 | 187 | 211 | 219 | 202 | 199 | 210 | 243 | 215 |
| Medium rig afterwards (s) | 95 | 98 | 110 | 111 | 113 | 119 | 108 | 117 | 106 |

A plain clean-up of the same models takes about 90 to 140 s, so the option adds about a minute and a half. Laura's
hands and Airi's left came 0.8 to 1.9 cm further out of their sleeves (THUMB_WEB); Briar's right hand hangs in her
rags, which are joined to it, and was cut out of them. Painting around the hands first took twice as long: each finger
bone was tested against every texel of the model, and the island fill worked on the whole box around the gaps, which
lie all over the texture. Each bone now looks only at the points in a box around it, and the fill only at the gap's own
texels, with the same result byte for byte (Natalie: 131 s to 50 s).

Tried and dropped: copying the old mitten's paint onto the new hand (it is the pictures' fingers smeared over a mitten:
dark smudges on Devi's new fingers); the old hand's paint as the skin colour (Naomi's hands, fused into her black skirt,
came out black); searching for the arm's narrowest point further up the forearm (Devi's sleeve is narrower above its
cuff: the cut went into the sleeve and gave her a skin-coloured forearm); taking a fused hand's clothes within 8 cm of
the hand's line (large holes in Naomi's skirt); keeping the painter off only what lies behind the model's own hand bones
(Imani's second hand stayed); flooding from every piece of the arm's cut (the skirt beside Naomi's and Clara's wrists
flooded down the leg, so their hands were taken for fused ones: skirt cut away, holes patched flat, flaps at the
wrists); flooding only from the pieces of the cut that go round the arm (the end of Airi's wide coat sleeve, which the
cut met only in arcs, was left in torn flaps); cutting the new hand wherever the arm was cut (Laura's thumbs came off); counting everything within 3 cm of
the hand's bones as the hand (the skirt Naomi's mitten was pressed against kept the pictures' hand painted on it);
filling what the hand hid from the nearest painted surface, the mitten included (its skin came through).

Known limits:

- A wrist the generator fused to the clothes above the cut keeps a thin web of the clothes, which the rig then stretches
  (a dark flap at Naomi's wrists when her arms move). The cut's cross-section there takes the clothes in too, so the
  new wrist is widened towards it (up to 1.4 times) and shows a step.
- Where every picture drew the hand over the clothes, the colour there is a guess from around it: a soft, sometimes
  darker patch (Laura's coat).
- Paws, claws and hooves: the new hand is a human one. Leave the option off for them.

## Attach the detailed head (`npcgen/headgraft.py`)

Only from its option (or `--attach-head`), and only for a character that has the detailed head (`docs/detailed-head.md`, which
has the method, the measurements and the limits). The whole-body model's face is a mask; the option lays the head model's face
(a patch from the chin to the hairline and cheek to cheek) over it, in the body's skin tone, and keeps everything else of the
body. It runs last of all, on the game version, after the clean hands: Blender renders both faces, SDPose finds their 68 points,
`blender/head_graft.py` fits, cuts, eases and joins them, and `headgraft.blend_colour` matches the paint. About a minute; the model
has two materials afterwards. If a face cannot be found or fitted, the model keeps its own face and the job says why. A first
version: the eyes, nose and lips are much sharper and the patch ends along a smooth curve at the hairline; the seam shows from the
side as a faint crease (`docs/detailed-head.md` says what is left).

## Checking a clean-up (`npcgen/check3d.py`)

Optional, from **Check with LM Studio** under a cleaned-up model. `blender/check_render.py` renders the generator's
original and the game version from the front (the whole figure and the face, the same camera and light for both); they
are set beside the model sheet's front picture as columns "A" and "B", and a vision model in LM Studio answers in JSON
which is better, a 1-10 score for each and a short list of problems. It is asked a second time with A and B swapped (one
model load, two questions, `eyes.converse`), and the verdict stands only if both answers pick the same model; otherwise
"no clear difference". Near-duplicate problem phrases from the two answers are merged.

Measured on ten boards (five pairs, each in both orders), 2026-09-26:

| Pair | Right answer | muse-glimmer-30b | qwen3.8-27b | qwen3.6-35b-a3b |
|---|---|---|---|---|
| Imani from her sprite vs from the sheet (Hunyuan, painted) | sheet: the sprite one has a doubled face | both orders; named the doubled face | both orders; named the doubled face | both orders |
| Hunyuan model unpainted vs painted | painted | both orders | both orders | both orders |
| Clara, Pixal3D original vs cleaned up | cleaned up: black eye rings, washed-out skin | both orders; named both | both orders; named both | both orders |
| Imani, Pixal3D original vs cleaned up | cleaned up; its hair has skin-coloured streaks | both orders; "hair colour mismatch" | both orders; named the streaks | both orders; missed the streaks |
| Clara, Hunyuan Standard vs Fine | Fine, by a little (a smoother face) | Fine, both orders (7 to 6, 5 to 4) | picked the first column both times | once no answer (ran out of room thinking) |

So `local/muse-glimmer-30b` is asked first where it is installed (it does not think aloud; 33-47 s an answer), then
qwen3.8-27b (20-35 s), which names faults more exactly but on a close pair favoured whichever model stood first. The
two-way asking is what keeps such a position bias from turning into a verdict. The whole check took 96 s on Clara with
qwen3.8-27b (renders 15 s, model load about 30 s); Muse takes about half a minute longer.

The renders are Blender's, which draws back faces: on a Hunyuan model the check can name "black specks in the hair" that
are the tiny backward scraps described under Not done yet, which a game engine culls (seen on Imani, 2026-09-26).

## The game version's size: a choice per character

The game version keeps 100,000 faces unless you say otherwise, and the choice is made **per character**: the Game size picker on the 3D card's Game version
section (Light 25,000, Standard 50,000, Detailed 100,000, High 200,000), `--game-size` on the command line (`python -m npcgen generate --refine-3d --game-size light`, a preset
name or any whole number from 10,000 to 400,000), or the `game_faces` option of a request. `options.game_faces` in `config.json` changes the default for every request
that names none. The size a model was made with is kept (`assets.model_3d.refined.faces_requested`) and the picker offers it again next time; a value outside
10,000 to 400,000 or a name that is not a preset is a 400, not the default.

Measured on 2026-10-13 through the real queue, the same model made again at each size (every run starts from the generator's original, not from the last game
version):

| Character and model | 25,000 | 50,000 | 100,000 | 200,000 |
|---|---|---|---|---|
| Clara (Pixal3D, plain clean-up) | 24,992 faces, 15.4 MB, 72 s | 49,994, 18.7 MB, 72 s | 99,993, 21.6 MB, 75 s | 199,996, 25.7 MB, 72 s |
| Aubrey (Trellis2, with clean hands) | 24,999, 16.0 MB, 694 s | | 100,000, 21.5 MB, 686 s | 200,000, 25.9 MB, 679 s |
| Dace (Trellis2, with clean hands and the detailed head) | 44,289, 28.9 MB, 922 s | 68,613, 31.9 MB, 1,091 s | 117,211 (made earlier) | |

- **The time does not depend on the size.** It is spent on the texture layout and the painting, and on SDPose and Blender when the hands and the head are asked for.
- **The file shrinks by about 10 MB from 200,000 to 25,000 faces**, not by seven eighths: the baked maps (2048 px, 4096 × 2048 on a head with its own half) are most of it.
- **The detailed head adds its own faces** to whatever size you pick (about 19,000 on Dace: 25,000 became 44,289, 50,000 became 68,613), so "25,000" with the head is a
  model of about 44,000. It attached at both small sizes on Dace; on Aubrey it did not at any size (`The nose tip or the chin was not found on the head`, the same as in the
  library at 100,000), so that failure is the head's own, not the size's.
- **Clean hands need 100,000 faces or more to have fingers.** The faces within a hand's reach (a sphere round its 21 SDPose points, Aubrey, left and right): 15 and 129 at 25,000,
  123 and 519 at 100,000, 321 and 1,175 at 200,000. At 25,000 the step still runs (both hands were placed) but the hands come out as mittens again, so the picker warns when
  the two are chosen together. The medium skeleton's finger bones need the same.
- **The skeleton is the same at both ends**: Clara's low skeleton took 63 s at 25,000 and 57 s at 200,000, with every bone weighted and no notes either time.

## Not done yet

- **An ear the model has in another place than the pictures** takes the hair colour the pictures show there (Clara's
  left ear); lining the pictures up with the model's features locally would be the fix.
- **Telling rings from normal eyes automatically** (see above), and a few small dark flecks left in smoothed eyes.
- **Faces that match the pictures better on Hunyuan models.**
- **Tiny backward scraps inside Hunyuan's surface.** The surface net leaves a few small faces turned inwards just under
  the surface (one sat 0.2 mm under Clara's iris). The game version is single-sided, so a game engine culls them and
  shows the surface behind; Blender draws back faces, so its renders (and the card's still) show them as black specks.
  Turning back the faces simplifying folds over was tried: it changed no rendered pixel that mattered.
