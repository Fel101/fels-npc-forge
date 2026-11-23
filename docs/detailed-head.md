# Detailed head

A separate, much finer 3D model of one character's head, made from four close-ups of it. Only when asked (the **Detailed
head** section of the 3D model card, or `--head-3d`); the body model is never touched, unless you attach the head to it (below).
The code is `npcgen/head3d.py` (the
close-ups, the framing, the job steps), `npcgen/headpaint.py` (the colour), `three_d.pixal3d_head_graph` (the ComfyUI graph)
and the wording in `data/head3d.json`.

## Why

The whole-body Pixal3D run cannot draw a face, whatever the picture: its input is fixed at 1024 × 1024 per view and reads
16-pixel features, so with the figure filling the frame the head is about 125 pixels tall, and the grid puts a voxel about
1.1 mm across on the person (2.3 mm after the remesh). The faces come out as masks: goggle-like rings or holes for eyes, a
bump for a nose, a ridge for lips, glasses as solid plates. Bigger or upscaled pictures cannot help (`docs/model-cleanup.md`).
The head-only run spends the same 1024 pixels on the head: about 600 pixels from crown to chin, and a grid 3 to 6 times
finer (0.25 mm voxels at the Medium setting with the tight framing below).

## Using it

In the app, the **Detailed head** section of a character's 3D model card: pick the detail and the face, tick **Match the hair**
if you want it (below), press **Make a detailed head**. It draws four close-ups, then makes the model; a portrait is made
first if there is none and the model sheet has no front picture either (the close-ups are then drawn from that picture). The four pictures stay on the card, each with a **Redo**: it draws that picture again with a new seed
(from the front picture; the front one's Redo draws the other three again from it) and marks the head outdated, and **Rebuild the head from these pictures** then makes the model from the four as they are,
with a new seed (so it is also the way to try another shape from pictures you like). The pictures are the biggest cause of a
poor head, so look at them before the 15 minutes are spent, or after. The refresh icon draws all four again and rebuilds.
From a terminal:

```bash
python -m npcgen generate --ids <character-id> --head-3d [--head-quality medium|high] [--head-expression neutral|keep] [--head-match-hair]
```

It needs ComfyUI with Pixal3D (the models the whole-body option uses) and BiRefNet, but not NPC Forge's own head-texture node,
so it works without restarting ComfyUI; the hair option needs SAM 3.1. The files: `head_3d.glb` (cleaned and painted),
`head_3d_original.glb` (the generator's own), `head_3d.png` (its render) and `head_3d/` (the four close-ups, and `framed/`,
what Pixal3D saw). A head exports with its four pictures.

## What it does

1. **Four close-ups** (front, both sides, back) of the head and neck, by the edit model (the engine chosen in the Models
   card), 1024 × 1024, about 40 seconds each on Mage-Flow-Edit. **The front one is drawn from the portrait, and the other
   three from the front one**, so all four agree about the hair, the make-up and the skin (see Measured). The prompts (in
   `data/head3d.json`, editable) ask for a **neutral face**: a smile is baked into the geometry, and a jaw bone should open a
   mouth that starts closed. "As in the portrait" keeps the expression. The reference is the portrait; a portrait that
   shows the whole body (a photo of a standing person; found from BiRefNet's mask of it, a tall narrow one) gives the face
   too few pixels, so the model sheet's front picture with its head upscaled 4× (the "Sharper face" upscaler) is the
   reference instead, and the portrait itself if there is no sheet.
2. **Framing.** BiRefNet cuts each picture out; the shoulders are cut off (only the top 70% of each figure is kept: the head
   and a little neck, the chin having measured at 50 to 55% of it); then `head3d.frame_arrays` crops all four to squares
   whose side is 1.1 × the same height (the median of the four figures) and puts each figure in the middle of its own
   square, on black. Pixal3D reads the framing as the camera (the unit cube fills 1/1.1 of the frame, the same scale in
   every view), and its own crop node scales each picture to its own bounding box: right for a tall figure, where the
   height decides in every view, wrong for a head-and-shoulders image, where the width decides in some views (shoulders in the front and back)
   and the height in others. If the four heads were drawn at sizes more than 15% apart, a note says so.
3. **Pixal3D multi-view** (`three_d.pixal3d_head_graph`): the shape, the texture and the baked maps, from the framed pictures
   as they are (no crop or background-removal nodes in the graph, and not the whole-body run's head-texture node). The graph
   shares its stages with the whole-body one (`_pixal_shape`, `_pixal_finish`); the whole-body graph was checked to be
   byte-identical before and after that refactor.
4. **Clean-up.** The hidden inner skin (about 10 to 45% of the faces, as in the whole-body model) and the loose specks
   (connected pieces under 100 faces, found by position: the textured model's vertices are split along every texture seam)
   are removed. The generator's own file is kept as `head_3d_original.glb`.
5. **Painting** (`headpaint.paint`). Pixal3D's own colour for the head is off where it matters (below), so the texture is
   painted from the four pictures with the rig's exact cameras: a perspective camera with a 20° field of view, its distance
   set so the unit cube fills 1/1.1 of the picture, on the four sides. Each texel of the model's texture is a point on its
   surface; each picture that sees it (a depth buffer in that camera, the picture's outline, how squarely it faces the
   camera) gives its colour, weighted by how squarely it faces to the 6th power, the front picture three times as much:
   the face is the front picture's out to the cheeks, and the others take over only where they face the surface far better
   (a low power blends the four over wide strips, and each was drawn by a separate edit). How far the pictures replace the
   generator's colour depends on how squarely the best of them faces the texel (from 0.2 to 0.55), not on that power. Each
   picture is first lined up with the model's outline by a small scale and shift (fitted on the outlines' distance, over the
   nearest 60% of the outline only: silhouette overlap was fooled by a braid or a fall of hair one picture has and another
   does not, and scaled a side picture up 10%); the side and back pictures are balanced to the ones already counted where
   they see the same surface; where no picture sees a texel (the top of the head, under the chin) the generator's colour
   stays, shifted to the pictures' local tone. Pictures that do not line up (outline overlap under 60%) are not used, and
   if none does the head keeps Pixal3D's colours and says so. About 30 to 40 seconds on the CPU for a 2K texture.
6. **Match the hair** (only when ticked; `--head-match-hair`; SAM 3.1 through ComfyUI, the finder the whole-body option
   uses). Pixal3D turns loose curls seen from the front into flaps of hair beside the cheek, and a side picture, where
   there is only cheek, paints such a flap the colour of skin. SAM finds the hair in each picture and on the bare model drawn
   as that picture sees it (without its ears and eyes); a picture that shows no hair where the model has hair gives that
   surface no colour, and hair no picture then paints takes the colour of the well-painted hair around it. About 3 minutes
   more. Measured, it is not a clear win, which is why it is a tick box: on Natalie fewer skin patches beside the face but a
   smoother, less detailed hair where it filled; on Devi cleaner cheeks and hair over the ear the way the pictures have it;
   on Laura a grey patch on one lock. (Tried and changed on the way: a small opening of the model's hair mask left specks on
   the face that were filled black, and filling every low-weight hair texel rather than only those a picture refused did
   the same.)

## Settings

| | Grid | Steps | Remesh, faces | Textures | Time on the R9700 |
|---|---|---|---|---|---|
| Medium (default) | 1024 | 12 / 20 / 12 / 12 | 768, 500k | 2K | pictures about 3 minutes, then 16 to 19 minutes (the ComfyUI graph 13 to 15, the rest the clean-up and painting) |
| High | 1536 | 16 / 28 / 16 / 16 | 1024, 800k | 4K | pictures about 5 minutes, then 35 (the ComfyUI graph 31, the rest the clean-up and a 4K painting, about 4) |

The face is **neutral** or **as in the portrait** (`--head-expression`), the detail `--head-quality medium|high`.

**High is not better.** Measured once, on Natalie with the same pictures and the same seed (the side-by-side picture was not kept): 35 minutes against 18, 566k faces against 344k, a 72 MB file against 32, and a face that is
no better and in the eyes worse. The finer grid separates what Medium fuses: each eyelid, its lash line and its crease come
out as stacked thin plates with visible edges, where Medium has one lid with a lash fringe; the nose, lips, ears and skin
look the same, and the hair flaps beside the face are no better. The 4K texture adds nothing you can see, because the
pictures it is painted from are 1024 pixels and the face is 600 of them. One character and one seed is a small sample, but
it is 35 minutes to find out. High stays in the list because a future Pixal3D or a character with big, simple features may
want it; Medium is the default and the right setting.

## Measured

The shape alone, Medium steps (Natalie, seed of her library model): from the head-and-shoulders image framing with a smile 786 s, from her model
sheet's own head crops (no new pictures) 832 s, neutral 780 s, neutral with the tight framing 859 s. Devi: 793 s head-and-shoulders image, 796 s
tight; Laura 809 s. The whole app step (masks, the ComfyUI graph with texture and maps, clean-up, painting), with the tight
framing: Natalie 1094 s, Devi 1112 s, Laura 1106 s; the model files 33, 38 and 37 MB, 344k, 426k and 39k faces.

- Natalie and Devi's whole-body models have rings and holes for eyes; the head-only models have eyelids with lashes, brows,
  nostrils, lip contours and ears with a helix. Laura's whole-body model has her glasses as two solid plates over blank
  eyes (red, in its colour); the head-only model has thin wire frames with clear lenses and eyes behind them.
- Even from **the model sheet's own head crops** (no new pictures, the 4× upscaled heads "Sharper face" makes), the head-only
  run gives real eyelids, a nose and lips: the head-only framing does most of the work. The redrawn close-ups are better
  still (natural eye make-up, lips, an ear without the wire-like loop the crop version had), and they match the portrait
  rather than the sheet, whose front view can be a different woman with heavy eye make-up.
- **Sides and back drawn from the front picture** (instead of each from the portrait), same seeds, same framing, Medium:
  Devi's head went from noisy, speckled hair with a skin-coloured strip across the forehead and black eyes to smooth hair,
  natural eyes with lashes and irises and a clean profile; Natalie's has smoother, more coherent hair and fewer
  skin-coloured flaps beside the cheeks; Laura's came out about the same (a few pale hair strips either way, glasses drawn
  as double frames either way). A redo of the front picture therefore draws the other three again, and a side or back
  picture with no front one to draw from gets one first.
- **The tight framing** (the head and a little neck fill the frame) at the same scale on the same body: the head model is 1.4
  (Natalie, model scale 0.149 against 0.211 of the body) to 1.6 times (Devi, 0.154 against 0.240) finer, with separate lashes,
  thinner brows, real ears, and finer hair, at the same cost.
- It fits the whole-body model's head with nothing but a scale and a shift (`align_heads` in the exploration): median
  1.3 to 2 mm from its surface on the three characters, so an attached head would sit in the right place.
- **Pixal3D's own colour**, on Natalie: the skin an orange tan against her pale peach, and everything thin (the rims of the
  eyelids, the brows, the strands of hair at the sides) a muddy olive. Painted from the pictures the face is close to
  photographic: her skin tone, the irises and their highlights, the lashes, the brows, coppery strands, pores on the forehead.
- How well each picture lined up with the model's outline (overlap after the fit): the front 0.94 to 0.97, the sides 0.81 to
  0.96, the back 0.76 to 0.94; a back picture that drew the braid somewhere else than the model has it is trusted less.

## Attaching the head to the body

The head is its own model; the whole-body model still has its mask of a face. **Attach the detailed head** (an option of the
clean-up, under **Game version**; `--attach-head`, together with `--refine-3d` or on a character that has been cleaned up)
lays the head's face over the body's: a patch of the head model from the chin to the hairline and cheek to cheek is fitted on
the body's face and joined to it, and everything else stays the body's: its hair, ears, neck, clothes. The clean-up starts from
the generator's own model as always, so the attach is redone with every clean-up; the skeleton (`--rig-3d`) is made from the
result like from any game version. It needs the detailed head (made first), Blender and SDPose in ComfyUI, and adds about a
minute to the clean-up. The model has two materials afterwards, the body's and `npcforge_head`.

The code is `npcgen/headgraft.py` (the run, the colour match), `npcgen/blender/face_views.py` and `head_graft.py` (the
geometry) and `npcgen/blender/graft_math.py` (its numpy parts, tested outside Blender).

1. **Where the faces are.** Blender renders a front view of the body's head and one of the head model. SDPose finds the 68
   face points in each (a detection, then a close-up of the face cut from it: the close-up is what finds the jaw, lips and
   eyes; scores 0.96 to 0.97 on the three characters, on the body's blurry face too). The points are lifted onto the two models
   with rays.
2. **The fit.** Scale, the turn of the face in the picture and the shift across come from the nose, eyes and mouth points in the
   front plane; the depth from the nose and mouth alone, where the skin is (a mask's flat nose and glasses standing in front of
   the eyes make the eyes' depth useless). Mean distance of the points afterwards: 0.9 to 1.9 mm (at worst 2 to 7.5 mm).
3. **The patch.** The head model is a hollow shell (Pixal3D builds surface voxels): an outer skin, and an inner one a millimetre or
   three behind it that faces inwards and is joined to the outer one through the lids and nostrils. Only the outer skin is wanted:
   faces whose normal points into the head are taken off (round the eyes they stay: the lids' undersides and the eyeball's back are
   seen), which is a third of the faces. Of what is left, the faces inside a face-shaped outline (half a head wide at the cheeks, from
   the chin to 1.7 nose-to-chin distances above the nose with a round top, in front of the ears' plane) are cut along a **smooth
   curve**. In the outline's outer ring hair is looked for by the paint (hair is darker than skin whatever the shading, and often
   more saturated: Otsu's split of the log luminance, else two clusters in luminance and saturation, taken only when they are two
   real populations). Seen from the middle of the face, every direction gets the radius where the hair starts: walking in from the
   outline in slices of about 4 mm, for as long as at least half of a slice is hair, so a strand or a brow that stands alone, or skin
   between a strand and the hair, does not count. Those radii are eroded over 20 degrees each way, the curve may widen from each of
   them towards the outline only gently, is smoothed over half that width (so it can never go out past the hair) and pulled in
   2.5 mm. Faces that stand off the head like plates (above the brows only: the upper lids overhang) or float away from the body are
   dropped, the outline is opened (thin strips and stair-steps taken off, never the lids and lips), the piece with the nose is the
   patch, and its edge vertices are smoothed along the edge. When hair and skin cannot be told apart the patch stops mid-forehead
   instead.
4. **The edge.** Every patch vertex knows its distance along the surface from the patch's outer edge. The edge's vertices are carried to
   the body's outer surface (the nearest triangle that faces outwards: the body's head is a hollow shell, and its inner layers
   face inwards), smoothed along the edge but never more than a millimetre from the surface they are meant to lie on, and the rest
   follow, fading over 26 mm: within 10 mm of the edge the patch lies on the body's surface, 0.35 mm above it, and eases from there
   to its own shape. Where the body stands in front of the patch (hair over the forehead) the patch is not pulled out to it: it keeps
   its own shape and goes under it. The very edge dips 1.6 mm under the body's surface over 4 mm, so that the rim is where the two
   cross, not a step with a dark gap in it.
5. **The body's own face.** Not cut out: the head model is riddled with small holes (a hundred or so per patch: lids, lashes, gaps
   where hair was dropped), and every hole in front of a cut-out body would open onto the inside of the head. Instead the face is
   seen from the front, its deepest surface within 3 mm taken (thin wire glasses and lashes must not count) with the holes filled
   from round them: a smooth **backing** just behind the patch. The body's vertices in the face's area (further than 10 mm from the
   edge; within 30 mm of the backing, and in the middle of the face up to 75 mm in front of it, where a mask's nose and glasses
   stand out) are laid on the backing, and its inward-facing faces (the hollow head's inner skin) go. Within 10 mm of the edge, where
   the patch lies on the body and follows its nearest edge vertex, not the body's bumps, whatever of the body stands in front of
   the patch is pushed back to the backing (never brought forward). Nothing of the mask stands in front of the patch, and a hole in
   the patch shows the body's own skin.
6. **The colour.** The patch keeps the paint of the head model's pictures, so its tone differs from the body's (Devi's patch was
   20% brighter). Along its edge (9 mm) the paint crosses over to the colour the body has there, and the whole patch gets the
   per-channel gain that makes its skin the body's (the median ratio over texels where both are skin: not hair, not shade).
7. **The skin under the patch.** Seen from the front, the patch's colour at every pixel of its area is found (its holes filled
   from round them), and the body's paint at every texel of the surface laid there is replaced by it, fading in over 3 mm from
   the patch's edge (`headgraft.fill_backing_colour`). It matters little: from the front the patch has almost no see-through
   pixels (under 0.3% of its area), so the body's skin shows only in slivers along the edge and, on Laura, the lens plates.

### Measured (2026-09-30, Medium heads, Pixal3D bodies cleaned up to 100k faces)

| | Face points found (body / head) | Fit, mean / max | Patch | Faces after | Attach time | File |
|---|---|---|---|---|---|---|
| Natalie | 0.96 / 0.97 | 1.1 / 3.9 mm | 25.7k faces (the inner skin's 22.9k left out) | 124k | 60 s | 30 MB |
| Devi | 0.96 / 0.96 | 1.9 / 7.5 mm | 24.9k faces (the inner skin's 18.0k left out) | 124k | 63 s | 33 MB |
| Laura (glasses) | 0.96 / 0.97 | 0.9 / 2.0 mm | 28.2k faces (the inner skin's 15.2k left out) | 128k | 60 s | 33 MB |

The whole clean-up with the attach took 137.6, 175.0 and 169.4 s (Natalie, Devi, Laura). Devi rigged (medium skeleton, 60
bones, the animation library) from the attached model in under three minutes, and the jaw bone opens the head's real lips; but the
seam along the jaw line tears open visibly at 0.28 rad (a strip of the patch pulls away from the body there: the weights come from
the generator's own shape by nearest surface, and patch and body do not agree there).

What it looks like (2026-09-30: the same three characters cleaned up with and without the attach, same cameras). **The middle of
the face is a clear step up, and the patch now ends along a smooth curve at the hairline.** Where the plain game version has a
smeared mask (the eyes inside an oval hole, a nose and mouth without edges, glasses as a blur of lens plates), the attached one has
sharp eyes with lids, lashes and irises, nostrils, defined lips, pores and a clean profile; on Laura the head's own wire glasses
stand in front of the eyes. Devi and Natalie are clearly better for it and read as real faces from the front and the side. What is
left is at the join and in the head model's own defects (below): a faint crease along the cheeks from the side, a hair parting or
a lock that leaves a spike, a fold or a few triangular shards at the top of the forehead (worst on Laura), darker patches of paint on the
forehead, and Natalie's left
eye, which the head model itself made broken. Laura is still a mixed result: the glasses, eyes, nose and lips are sharper, but the
body's lens plates show across the eyes and her patch is paler than the body's own skin at the forehead. The option is opt-in and
the raw model is kept, so a clean-up without it returns to the plain game version.

### On Trellis2 bodies, and a second round of fixes (2026-10-02)

The attach was first measured on Pixal3D bodies. On the Trellis2 four-picture bodies ([model-import.md](model-import.md): painted,
cleaned up to 100k faces) it gives the best faces seen so far: Devi, Laura and Natalie fitted to 1.4-2.5 mm on average, patches of
25-31k faces, 124-130k faces in all, the clean-up with the attach 455-590 s. Eyes, lids, lips and nose are as sharp as before, and the
slits and raised rims the generator sculpted round the eyes lie hidden behind the patch. The same join problems show, and they were looked at again, with
renders from the same cameras and two numbers: how far the patch's rim lies from the body's outer surface, and how much of the patch
(seen from the front) the body stands in front of.

- **The rim now lies on the body (changed).** The patch's edge is carried to the body's surface wherever the body is less than
  `hidden` in front of it, and only where a hair lock stands well in front does the patch stay as it is and go under it. `hidden` was
  0.5 mm, so on Natalie 405 of 733 rim vertices, on Laura 221 of 420 and on Devi 106 of 299 stayed under the body's coarse faces, and
  the outline of the face was whatever those faces' lower edges made it: teeth along the forehead. At 8 mm 34, 7 and 0 do. Rim
  distance to the body's surface, mean / 90th percentile: Devi 2.7 / 6.4 mm before, 1.5 / 2.1 after; Natalie 4.1 / 9.3 before, 1.9 /
  2.3 after; Laura 3.4 / 7.8 before, 1.5 / 2.2 after. The share of the patch the body stands in front of: Devi 1.7% to 1.3%, Natalie
  5.3% to 3.6%, Laura 6.6% to 3.0%. In the renders Laura's forehead edge went from large jagged teeth to a rounded outline with small
  flakes; Natalie's improved less (hair lies there); Devi's cheek shows a thin dark line along the join where the crease used to be
  hidden.
- **Together with "Soften the eyes for the shape" it is better still** ([model-import.md](model-import.md)). Run through the app on
  Devi and Natalie with the softened shape (cleaned up and painted with the head attached), against the plain shape with the head attached: the
  head's eyes are as sharp in both, but the softened body has no slits or hair visor under or round them. Devi's temples lose their
  black cracks and her profile is cleaner; Natalie's hair no longer hangs in a flap across one eye, which the patch cannot hide
  (the strands in front of the face stay in front of it), and both eyes read clearly. Two characters, one run each: the best faces
  made so far, not a benchmark. A character with glasses is not softened (above), so there the head is attached to the plain shape.
- **What was tried and did not help** (all measured on the same three, then dropped):
  - A longer `hair_margin` (10 and 18 mm), to stop the patch below the body's coarse forehead: the flat facets of the body's own
    forehead then show instead.
  - Easing the body's vertices outside the patch back towards the laid ones along the surface (a ramp of 12 and 30 mm): at 12 mm it
    moved 82 vertices and changed nothing visible; at 30 mm, with the near-rim body pushed back too, it cut the overlap by three
    quarters and opened black cracks at the temples and the forehead (stretched faces showing the hollow head's inside).
  - Cutting the body's edges where they cross the laid-back area so that the slope lies under the patch: the overlap got larger
    (the new vertices stay at the surface, which is in front).
  - `sink` (the rim's dip under the surface) at 0: no consistent gain (with `hidden` at 8 the overlap went 436 to 328 mm² on
    Natalie and 490 to 509 on Laura) and a darker cheek line on Devi. The default stays.
- **The jaw seam was not a weights problem.** On Devi's rigged model the jaw weights along the patch's outer edge are the same on both
  sides (0 of 139 edge vertices differ; none has any jaw weight at all: the edge does not reach the jaw's influence). Where patch and the body layer
  just behind it differ is round the lips, and giving those body vertices the patch's weights made the open mouth worse: the
  stretched backing then fills the mouth with skin-coloured slivers instead of the dark interior. The mouth opens as it did, with
  the corners stretching vertically, as on a plain rigged Trellis2 model.
- **Glasses.** Everything of the body that stands in front of the patch inside its outline is already laid flat behind it (1059 of the
  1234 vertices standing 6 mm or more in front on Laura, 108 more pushed back), so the frames inside the face are the head
  model's. What doubles are the arms and the outer corners of the lenses: they lie beyond the patch's side edge (the cut stops at the
  hairline, and the arms run on to the ears), where the body's chunky frames stay. Keeping the body's frames instead and taking the
  head model's off (frames and arms) was tried: the eyes then sit behind thick rims and the body's dark lens shading, and are lost.
  The job now says so when the character sheet has glasses ("the head model's thin frames and the body's chunky ones both show"),
  and the model records it; whether to attach to a character with glasses is a judgement of the picture. The way round it is not to
  have the frames in either model: **Leave the glasses off the 3D model** (`--glasses-off`, see
  [model-import.md](model-import.md#leave-the-glasses-off-the-3d-model-optional)) draws the head's close-ups without them and makes
  the body from copies of the pictures without them, so there is nothing to double.

### What it does not do well

- **The join is still visible.** The outline is a smooth curve now (it used to follow every strand of the head's hair, and took
  the brows and upper lashes with it: a torn forehead, dark shards, slits above the eyes), but from the side it shows as a faint
  crease along the cheeks where the patch comes out from under the body's surface (the body's face is not the shape of the head's, so
  the join is a crossing, not a blend), and the patch's paint (sharper, and a different tone) does not melt into the body's: the
  colour cross-over makes the seam a change of texture more than of tone, but Laura's forehead is visibly paler. The very edge of
  the cut is a fine saw-tooth of 1 to 2 mm (the faces' size): smoothed, not gone.
- **The head model's own defects show through.** Hair-coloured strands and darker or lighter flat patches of paint on the forehead
  and the temples, thick crinkled eyelids, and, on Natalie, one broken eye (its eyeball is a faceted shard).
- **The jaw.** The seam along the jaw line was first put down to the weights (nearest surface on the generator's own shape); on a
  Trellis2 body they were measured and agree along the patch's edge, and the fix proposed here, the head model's weights for the
  patch, made the open mouth worse (above). What is left is the stretch of the skin at the mouth corners.
- **Glasses.** The patch has the head model's wire glasses (sharper than the body's blur), but the body's own lens plates stand
  40 to 60 mm out: those in the middle of the face are laid on the backing, pieces of them stay round the eyes and the temples,
  and on Laura they show as grey translucent shapes across both eyes and as a flat plate beside the right one. (On a Trellis2
  body, whose glasses are open frames, it is the arms and the outer corners of the lenses that show twice: above.)
- **Hair over the face.** Strands of the body's hair that hang within 30 mm of the backing in front of the face (75 mm in the middle of the face, where the glasses are) are laid flat under the patch
  (the fringe goes; hair farther out stays where it was, and can cover the patch); the head's own hair is cut off with the hairline.
  Skin-coloured hair flaps of the head model (Natalie) are removed by shape, some remain.
- **Thin things.** Eyelid and lash fins of the head model come along with their holes; the body's skin shows through them.
- **Weight.** The patch is 24.9 to 28.2k faces (the head model's own density, without its inner skin) on top of the game version's 100k, and the GLB
  carries the head's three textures. A lighter patch is not done: Blender's edge-collapse decimation shattered the head (426k to 60k
  faces turned its hollow thin sheets into spikes and left black holes in the baked colour).
- **Not for** characters without a human face (a face detector finds none on a paw or a beak), or with something over it
  (a helmet, a mask); the job says so and the model keeps its own face. Nothing has been tried on children, anthros, or a head
  turned away from the front in the body model.

### Traps found

- Fitting the head to the body's skull and hair (ICP) put Devi's face 2.5 cm too high and 15% too small: the body's hair volume is
  far bigger than the head model's. The fit is from the face's own points.
- The depth of the eye points is useless on a mask with glasses (the fit tilted Laura's head 22 degrees): the fit is in the front plane.
- The nearest body surface to a patch vertex is often an inner layer of the hollow head, not the outside: the patch's edge was eased
  behind the body's lips and cheeks. The outer surface is the nearest triangle that faces outwards.
- Cutting the body's face out around the patch opened black holes wherever the patch has a hole (they show the inner skin's back
  faces); pushing single vertices back shredded the mask into shards; both were dropped for the backing.
- Pushing hair strands back along the depth makes ribbons floating beside the cheek: only what is close in front, or (in the middle
  of the face) what a mask's glasses are, is laid down.
- The head model's paint varies a lot with its baked lighting (the cheeks are far darker than the nose), so hair is not "not the
  skin colour": it is a second population, judged on the outline's outer ring alone.
- A `lift` local shadowing the function of the same name in the Blender script: the first call failed with UnboundLocalError.
- Dropping the hair-labelled faces one by one made the outline follow every strand and also took the brows and the upper lashes
  (dark, and in the outline's outer ring): jagged holes along the brows. Hair now counts only where it runs unbroken from the outline
  inwards, and the curve is smooth. The plate test (faces that stand off the head) took the upper lids and brows, which overhang, for
  plates: slits above the eyes. It only runs above the brows now.
- The head model is a hollow shell: a third of the patch was its inward-facing inner skin, and the patch had two big boundary loops (the
  outer skin's and the inner skin's), so the rim easing worked on one of them and the front skin's rim floated 2 to 3 mm in front of the
  body (a dark line along the cheek in profile). Taking the inner skin off (round the eyes it stays) fixed that, and a third of the
  faces; the rim is also kept within a millimetre of the surface it is meant to lie on, and dips under it.
- The "flat tiles" on the cheeks and the forehead were not holes showing the body's skin (measured: the patch has under 0.3% see-through
  pixels, and painting the skin under the patch magenta showed slivers along the edge only). They were the head model's own paint and
  the body poking through the patch near its edge: pushing the body back there helped more than filling holes with the patch's colour.
- Pulling the patch's edge out to the body's nearest outward surface where the body is hair, in front of the forehead, dragged the
  forehead 15 mm forward into a ridge. Where the body stands in front of the patch the patch keeps its shape and goes under it.
- A hair parting is real skin that runs up between two masses of hair: as a narrow spike in the cut it was eased into a fold. The
  hairline is eroded over 20 degrees each way, which takes such spikes off.

## Limits

- **Eyelids and brows are thin fins**, hair is sheets or noodles: a model of the picture's make-up and strands (lashes come
  out as a fringe of separate lashes in the tight framing).
- **The four pictures are four separate edits**, so hair can differ between them (Natalie's braid is at her shoulder in the
  front picture and down the middle of her back in the back one). The shape is a compromise, and where the compromise puts
  a flap of hair beside the cheek, the side picture shows skin there: the flap is painted skin-coloured from the side and
  hair-coloured from the front. Pictures cannot fix geometry that disagrees with them (Match the hair helps a little).
- **The face depends on the pictures**: a mouth that is drawn slightly differently in the front and side pictures comes out
  lumpy (Devi's neutral mouth); glasses drawn a little differently in each picture come out as two frames (Laura's front);
  a new attempt (Redo the picture that differs, or the refresh button for all four) usually helps.
- A few small stray shards remain (a fragment between Devi's brows, a plate beside Natalie's nose in one run).
- The head is its own model until you attach it to the body's (above).
- Not tried: animals, robots and cartoons; hats and helmets.

## Traps found

- Pixal3D's own crop node cannot be used for a head-and-shoulders image (above).
- The textured model's vertices are split along texture seams: finding loose pieces by vertex number cut holes in the nostrils
  and around the eyes; it goes by position now.
- A silhouette overlap fits a picture to the model badly when the hair differs; the outlines' distance, trimmed, does not.
- Whole-body portraits were first found from an outline against the picture's edge colour, which a grey studio gradient
  fooled (Imani's photo-made portrait counted as a head-and-shoulders image); BiRefNet's mask does not.
- The app allows one process per library: a scratch server cannot open a library a command-line run is using.
- The whole-body test suite's tests of the ComfyUI lock and the GPU gate (`test_comfy`, `test_voice`, the "unreachable
  ComfyUI" server test) fail while a head run holds the GPU; they pass on an idle card.
- Background Blender renders with a relative output path land in `C:\`; give absolute paths.
