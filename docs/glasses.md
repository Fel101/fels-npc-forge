# Glasses: built from measurements, not from a picture

The **Items** tab ([items.md](items.md)) makes most things from a picture: a main picture, four views, a Trellis2 shape, paint. That chain
cannot make glasses. A frame is a wire a millimetre and a half thick and a lens has no surface for a camera to find, so a generator
gives an open shell or a solid slab. So the **Glasses** family has a
maker of its own: `npcgen/glasses.py` builds the pair from the numbers an optician gives, in a few hundredths of a second, with no GPU
and no queue, and the same recipe always gives the same file.

## What it makes

One small GLB (about 200 to 250 KB) in **real metres**, facing **+Z**, with its **origin at the middle of the bridge** on the line through
the middle of the lenses (so a character's pair is put where the eyes are, and the Items list's usual `Head` bone is its parent):

| Node | What it is |
|---|---|
| `frame` | rims, bridge, end pieces, hinge pins, nose pads and their arms (materials `frame`, `hardware`, `pads`) |
| `lenses` | both lenses, each a slab with a front, a back and an edge, with a base curve (material `lens`) |
| `temple_left`, `temple_right` | the two arms, each with its **origin at its hinge**, so a game can turn one about the vertical axis to fold it |

Four flat-colour PBR materials (a gradient lens adds one tiny texture), so it loads the same in Godot, Unreal and three.js and the colours can be changed at run time. The
frame is metallic for wire and not for plastic; the hardware (pins, screws) is steel for a plastic frame, dark grey steel for a matte one and the frame's own colour for a metal one;
the lens is **alpha-blended** (set its transparency mode in the engine) and, being a slab with a front and a back surface, each surface
carries the darkness that gives the total asked for (`1 - sqrt(1 - darkness)`), so a "0.8" lens looks 0.8 dark, not 0.96. A mirrored lens is
opaque and metallic. A gradient lens (darker at the top) carries the gradient as the alpha of a 4 × 256 texture, with texture coordinates on the lens: every
engine reads that (vertex colours were tried first, and Godot leaves them off unless told to use them, so the gradient was lost there).

The settings the pair was built from are saved inside the file (`asset.extras.recipe`) as well as in the item.

Built and looked at (Blender renders of every style from the front, the side, the top and three-quarters, and the app's own viewer): the
12 styles below, folded and open, clear, tinted, mirrored and gradient lenses, wrap up to 16 degrees. Cost: **0.02 s** to build; about
**5,000 triangles** at *Light*, **11,000 to 13,000** at *Standard* and **28,000 to 31,000** at *Detailed* (the item's "game version" setting
chooses how finely the curves are built, and the count never goes over it).

## The settings

Sizes are in millimetres, as a pair is sold: the lens box (`lens_w` × `lens_h`), the gap between the two boxes (`bridge`) and the arm from its
hinge to its tip (`temple_len`). A lens fits its box exactly, whatever its shape. The ranges below are the ones the server checks and the
editor's sliders use (`data/glasses.json`, read fresh on every request, so a style or a swatch can be added there without restarting).

| Setting | Meaning | Group | Range | Default |
|---|---|---|---|---|
| `lens_w` | Lens width | Lenses | 36 to 66 mm | 52 |
| `lens_h` | Lens height | Lenses | 24 to 60 mm | 42 |
| `bridge` | Bridge (gap between the lenses) | Lenses | 8 to 30 mm | 19 |
| `base_curve` | Lens curve (0 is flat; a higher base is rounder) | Lenses | 0 to 10 base | 4 |
| `bridge_height` | Where the bridge joins the rims | Frame | 0 to 0.4 of the lens | 0.22 |
| `rim_w` | Rim thickness seen from the front | Frame | 0.8 to 9 mm | 5.2 |
| `rim_d` | Rim depth, front to back | Frame | 0.8 to 9 mm | 5 |
| `brow_w` | Heavy top bar of a browline | Frame | 2 to 12 mm | 6 |
| `wrap` | The two halves bent back round the face | Frame | 0 to 20 deg | 5 |
| `temple_len` | Arm length | Arms | 110 to 170 mm | 145 |
| `temple_h` | Arm height at the hinge (it tapers to about half at the tip) | Arms | 1.6 to 14 mm | 7.5 |
| `temple_w` | Arm thickness | Arms | 1.2 to 6 mm | 3.6 |
| `temple_drop` | The bend down behind the ear (0 is straight) | Arms | 0 to 85 deg | 28 |
| `temple_bend` | Where that bend starts along the arm | Arms | 0.45 to 0.9 | 0.75 |
| `splay` | Arms opened outward from the straight | Arms | 0 to 10 deg | 2 |
| `hinge_y` | Hinge height on the lens | Arms | 0 to 0.45 of the lens | 0.3 |
| `lens_opacity` | Lens darkness | Tint | 0 to 1 | 0.07 |
| `scale` | Overall scale | Size | 0.5 to 3 | 1 |

And the choices: `shape` (`classic`, `round`, `oval`, `rectangle`, `square`, `aviator`, `cat_eye`, `hexagon`, `octagon`, `half_moon`), `rim`
(`full`, `half` with a thread under the lens, `browline`, `rimless` with screws through the lens), `bridge_style` (`arch`, `keyhole`,
`double` with a brow bar), `frame_material` (`plastic`, `metal`, `matte`), `frame_color` (a `#rrggbb` or the name of a swatch),
`lens_tint` (`clear`, `light`, `grey`, `dark`, `brown`, `amber`, `green`, `blue`, `rose`, `yellow`, `purple`, `custom`), `lens_color`,
`lens_finish` (`glass` or `mirror`), `lens_gradient`, `pads`, `pose` (`open` or `folded`) and `preset`, the style it started from.

**Styles** (a style sets the shape, frame and arms, and leaves the lenses, pose and scale as they are): classic plastic, round wire, aviator,
browline, cat-eye, half-rim, rimless, half-moon reading, hexagon wire, oval wire, chunky square and sport wrap. The two kinds start from a style
and lenses: *Glasses* from round wire with clear lenses, *Sunglasses* from aviator with grey lenses.

Setting a thing that makes another thing wrong is not allowed to: a wire frame brings wire-thin rims, thin arms and nose pads (a plastic one
brings the thick ones back), so changing the material in the editor, or saying "wire" in the description, gives glasses that look right.

## Using it

In the app: **Items → New item → Glasses**. The page is the designer: style, shape, rim and bridge; the lens size, curve, tint (swatches or any
colour), mirror and gradient; the frame's material and colour; the arms; the pose and the scale. A **live preview** beside it shows the glasses
as they are *now* (saved or not): the server builds them for each change and the viewer keeps the angle you turned it to. **Save changes** (**Create item** for a new pair) builds the model
and the picture for the Items list is drawn in the background a few seconds later (Blender on the CPU, never the GPU, so it does not wait behind a
long job); the page says when it is ready, and **Draw the picture again** builds again. **Set the controls from my words** reads the description
box ("thick black cat-eye glasses with rose-tinted lenses", "oversized gold aviator sunglasses", "folded half-rim glasses with nose pads") and sets
the controls: a style or a shape, wire or plastic, a frame colour, a colour next to "lenses" for the lenses, sunglasses, mirrored, gradient, bigger or
smaller, thicker or thinner, long arms, nose pads, folded. Words it does not know change nothing, and what it understood is shown (the vocabulary is
`words` in `data/glasses.json`).

On the command line (built on the spot, nothing is queued):

```bash
python -m npcgen item make --family eyewear --kind sunglasses --style aviator --set lens_tint=green --name "Green aviators"
python -m npcgen item make --family eyewear --kind glasses --style cat_eye --set frame_color=burgundy --set lens_w=54 --set pose=folded
python -m npcgen item make --id aviators --set lens_w=60 --set lens_gradient=true     # change what is named, keep the rest
python -m npcgen item make --id aviators                                              # build it again
```

The API: the item routes of [items.md](items.md) work for glasses too (`recipe` is a field of the item; creating or saving builds the model and
`POST /api/items/<id>/make` builds it again and returns the item, with `"job": null`), plus `POST /api/items/recipe-preview` (body
`{"kind", "recipe", "faces"}` → the GLB itself, with the triangle count and size in the `X-Item-Info` header, nothing saved) and
`POST /api/items/recipe-from-text` (body `{"text", "kind", "recipe"}` → `{"recipe", "changed", "understood"}`). The tables for the editor come with
`/api/config` (`items.glasses`).

## On a character's face

A saved pair can be tried on any character that has a 3D model: **Items → the pair → Try them on a character**, or `python -m npcgen item fit`.
The pair is put where the character's eyes and nose are, sized to the face, and it is yours to nudge; what you keep is the nudge.

**1. Finding the face** (`npcgen/facescan.py`, `npcgen/blender/face_scan.py`; once for each character and model, about 40 seconds, kept in the character's
folder as `face_scan.json` with the model it was made from, so a new model asks for a new scan). Blender draws the head from the front on the CPU, SDPose in
ComfyUI finds the 68 face points in that picture (a few seconds of GPU, the same step the detailed head uses) and Blender lifts them onto the model: the
middle of each eye (the mean of its six points), the width of the face at the jaw's two ends, and the front surface of the face as a grid of depths (a ray from
the front for every millimetre). If ComfyUI is not running, or no face is found (a helmet, a mask, an animal's face), the eyes are placed by proportion
instead (0.911 of the height up, 65 mm apart at 1.7 m: the medians of the eleven faces SDPose found in this library, a little lower and wider than a textbook
adult's), with no GPU, and the scan says so; **Place by proportions** asks for that on purpose. A score under 0.6 from the detector is said too.
The scan runs on a thread of its own, one at a time, so it never holds up a request; ComfyUI is unloaded afterwards unless the job worker has work.
A model that says where its own eyes are needs none of this detection: a designed body (made by the Body Designer add-on) carries `eye_l` and `eye_r` in `asset.extras.landmarks`
(the front of each eyeball), so the scan takes them as they are (`source: "landmarks"`), reads the surface exactly, uses no GPU and no picture, and takes about 5 seconds.
On the female and the male designed body (2026-10-07) a round wire pair came out 16 and 17 mm from the eyes with nothing inside the face; the female's nose is wide for the
aviator pair's bridge, which the fit says (the lenses are held at 20 mm and the inner rims pressed 7 mm into the nose). What landmarks cannot give is the face's width, so the scale
comes from the eyes alone; their eyes are 57 and 59 mm apart, so a pair comes out 10 and 7 per cent smaller than for a 63 mm adult.

**2. The fit** (`npcgen/facefit.py`: numpy only, milliseconds, redone on the server for every move of a slider). In the model's own space (+Y up, the face
toward +Z, the wearer's left at +X):

- *Turn.* The line of lenses follows the line between the eyes: a roll about the front-to-back axis (never more than 15 degrees) and a yaw about the vertical one
  (never more than 30; three of the first twelve characters were turned 14 to 20 degrees in their own front view), each ignored under one degree. It is never
  pitched; **Tilt** is a nudge.
- *Size.* The mean (geometric) of the eyes' distance over an adult's 63 mm and the face's width over 135 mm. The pair's own `scale` is already in the file and
  is not touched; a model that is not in metres (a raw generator file) is sized the same way, so the pair comes out in that model's units.
- *Across and up.* The middle of the bridge goes to the middle of the eyes.
- *Depth.* The pair slides forward until no vertex of the frame or the lenses is inside the skin (a millimetre of room): it rests where a real pair rests.
  Three refinements, each from a character that looked wrong without it. The lenses stay at least 10 mm in front of the eyes (a flat nose would let them touch
  the lashes). The frame's upper rim and its outer corners (beyond 34 mm from the middle) may push the pair out by only 4 mm more than the rest of it: a fringe, a
  brow, a hat's brim or hair falling over the temples is in front of the face there, not part of it, and would float the pair away. And the nose is a soft contact:
  the inner rims and the bridge below the nasion (within 24 mm of the middle and no higher than 6 mm above the line of lens centres) are never allowed to hold the
  lenses more than 20 mm from the eyes, and neither is anything else but a real cheek: they are pressed into the nose, or the hair, instead, and the report says how
  far. A wide nose wants a wider bridge in the design than the 19 mm of the default; the note says so.
- *Nudges.* **Up or down** and **forward or back** in millimetres, **tilt** in degrees (top forward) and **size** (0.5 to 2 times what was found), applied last along
  the pair's own axes. Tilt and size are fitted again, so the pair stays out of the face.

The report says what it did: the scale and where it came from, the roll and yaw, how far the lenses are from the eyes (`vertex_distance_mm`), the gap at the nose, how far
anything is inside the face (`behind_mm`), the nose (`nose_into_mm`) or hair, a brow or a hat (`hair_into_mm`), and notes in words. Its millimetres are the pair's own, so they mean the same on any model; the eyes' distance and the face's width are given as if
the character were 1.7 m tall.

Checked on the twelve characters of the library that have a 3D model (2026-10-06; Blender close-ups from the front, three-quarters and the side, and the
page in a browser): SDPose found a face in all twelve (scores 0.87 to 0.97, and 0.40 for an alien drone that has no human face, which the note says); on the eleven
real faces the lenses came to rest 13 to 20 mm from the eyes (13 to 18 where the face itself held them, 20 where a wide nose, hair or a brim would have held them
further out), nothing was inside a face, the pair followed heads turned up to 20 degrees and tilted 9, and a nose was pressed into by 5 mm on two of them and 15 mm
on one (a long wide nose: its note asks for a wider bridge). Models in a raw generator's units (no rig, under a metre tall) are fitted the same way.

**3. What is kept.** Only the nudge, for each character (`fits` in the item). The pair is fitted again to the face whenever the pair is saved again or the
character's model changes, so a changed pair never leaves a stale fit behind.

**4. What you get.** The character's own model with the pair added under its **head joint** (`head`, or `Head` in the animation library's skeleton; found without caring
about capitals), so it follows every clip. Nothing of the character is rewritten (its buffers, skin and animations stay as they are); the pair's materials, textures and
meshes are added to the file, a material named like one of the character's is renamed `<name> (glasses)`, and the node's `extras` say `npcforge: glasses`. A game that would
rather attach the pair itself gets the **attach block**, the pair's place in the head joint's own space (`bone`, `translation`, `rotation`, `scale`, or a `matrix` when the bone
is scaled unevenly): the `X-Fit` header of the download, and printed by the command line when it is given `--out`.

On the command line (the face is found first if it has not been, then the fit is printed):

```bash
python -m npcgen item fit round-wire natalie-reed                        # find the face (once), fit, print the report
python -m npcgen item fit round-wire natalie-reed --forward -2 --up 1 --save    # nudge it and keep that for this character
python -m npcgen item fit round-wire natalie-reed --out out/             # also write natalie-reed-<pair>.glb: the character wearing them
python -m npcgen item fit round-wire natalie-reed --estimate             # place the eyes by proportions (no GPU)
```

The API: `GET /api/npcs/<id>/face` (the state: `none`, `queued`, `running`, `done`, `error` or `cancelled`, with a summary of the scan), `POST /api/npcs/<id>/face/scan` (body
`{"force": bool, "estimate": bool}`; starts the scan and returns the state, which the page asks for again until it is done) and `DELETE` of the same to stop it;
`POST /api/items/<id>/fit` (body `{"npc": id, "nudge": {up, forward, tilt, size}}` → `position`, `rotation` (a quaternion), `scale` and `matrix` in the character's space, the
report, `focus` and the saved fit; 409 until the face has been scanned), `PUT` and `DELETE /api/items/<id>/fits/<npc id>` (keep or forget a nudge) and
`GET /api/items/<id>/worn/<npc id>.glb` (the character wearing the pair, with `X-Fit`; the saved nudge, or `?up=&forward=&tilt=&size=`).

## For your game

- Put it on the `Head` bone (the animation library's name) with its origin between the eyes: the bridge of the pair rests on the bridge of the nose.
  The size is a real adult's, so a character that is not 1.75 m tall, or has a stylised head, wants the pair scaled with the head. **Try them on a
  character** does all of this for a character that has a 3D model (the section above): the download has the pair under the head joint and the attach block
  gives its place, rotation and scale in that joint's space.
- Fold or open the arms by turning `temple_left` and `temple_right` about their own Y axis; the built **folded** pose is those two turns.
- Change the lens colour at run time through the material called `lens`.
- **A character wearing them, in Godot 4.7** (checked 2026-10-06 on a rigged and an unrigged character from the library, headless): the file imports with the pair at
  `Armature > Skeleton3D > head (BoneAttachment3D) > glasses`, so it follows the head bone; its global position, scale and turn come out where the fit put it, to
  within 0.05 mm (the printed precision), and the character's 23 bones, clips and triangles are as they were. An unrigged model gets `glasses` at the top of the scene.
- **Godot 4.7** (checked by importing every style, folded, mirrored and gradient, headless): the nodes arrive as `glasses` > `frame`, `lenses`, `temple_left`,
  `temple_right` with the arms at their hinges (a folded pair at 90 and -85 degrees); the lens comes in alpha-blended (alpha with depth pre-pass), the
  gradient as its albedo texture, and a mirrored lens opaque and metallic. Nothing needs to be changed in the import dialog.

## Limits

- **Fitted to a face, not bent to a head.** The pair is placed (turned, sized, pushed forward until it clears the skin) but never reshaped: a wide nose is pressed into
  rather than given a wider bridge, the arms stay straight lines with a bend and go through the hair at the temples, and a head that is not a human's (a helmet, a mask,
  an animal's muzzle) gets the proportions of an adult's and a note, to nudge by hand. Hair, a brow or a hat in front of the upper rim is passed through, not fitted
  around: the hat's brim over the eyes is told apart from the face only by being above the lenses.
- **One character at a time, and only glasses.** The scan is a head-on view of the face (a ray from the front for every millimetre), so it cannot see under a brim or
  behind a strand; hats, helmets and masks are not fitted by this yet, though the scan and `glbmerge` are what they would use.
- **Flat colours only.** There is no tortoiseshell pattern, no wood and no engraving: a colour, a metal, a finish.
- **The lens is a tinted, alpha-blended slab**, not glass with refraction or reflections of its own; a mirrored lens only looks like a mirror where the
  engine has something to reflect.
- **The two lenses are mirror images**, as are the arms; the hinge is a pin and a straight cut, not a modelled hinge with knuckles; the nose pads are
  simple ovals on wire arms.
- **No collision with a head.** The arms are straight lines with a bend, not shaped to a skull; the splay and the arm bend are for the eye, not measured
  against a head.
- Round things that need the same treatment (rings, a monocle's chain, goggles with a strap) are not made yet; the kernel (`npcgen/procmesh.py`:
  sweeps along a path, lens slabs, a GLB writer) is what they would be made with.
