# Importing a 3D model made elsewhere

NPC Forge can take a 3D model that another ComfyUI workflow made (a Trellis2 shape from your own graph, a Hunyuan3D run, a
GLB you downloaded), close its holes, bring it down to a sensible number of faces and keep it as a character's model.
From there it is an ordinary model: **Clean up + paint from pictures** paints it from the character's model-sheet
pictures, the skeleton step rigs it, and the library export includes it.

Nothing is imported unless you ask: the **Import a 3D model made elsewhere** section of the 3D card, or `--import-3d`.

## Why this exists

The best model measured so far was not made by one of NPC Forge's own generators but by feeding NPC Forge's clean-up a
shape made elsewhere (details in the table below). Those shapes are raw: millions of faces, holes, voxel steps. A
generic decimator shreds them (a pyfqmr edge collapse of a 10-million-face shape to a million faces left 751,000 loose
fragments), but ComfyUI's own remesh nodes, the ones a Pixal3D model already ends with, turn the same shape into a closed
695,000-face model in about 25 seconds. The import runs exactly that, so the shape can go on to the clean-up and paint.

## What you need

- ComfyUI running (the import runs in it) with its own mesh nodes: `Load3DAdvanced`, `Get3DComponents`, `RemeshMesh`,
  `DecimateMesh`, `MeshSmoothNormals`, `SaveGLB`, `RenderMesh`, `SaveImage`. They are in ComfyUI itself, so there is
  nothing to install; the app checks for them (`/api/health`, `import_3d`).
- A `.glb` file **that ComfyUI can see**. NPC Forge does not copy it anywhere and send it on, because ComfyUI refuses
  uploads of more than 100 MB by default (`--max-upload-size`) and a raw generator shape is 100 to 400 MB. So the file must be:
  - in ComfyUI's **output** folder (where another workflow's SaveGLB or a Trellis2 export put it), or
  - in its **input** folder, or in **input/3d**, where ComfyUI's Load 3D node looks.

  The list shows the top of the output and input folders (that is all ComfyUI lists) and input/3d. A file in a sub-folder
  (the Trellis2 pack saves into `output/3D/` unless told otherwise) is not listed: **type its name** under the list,
  `3D/Trellis2_00001_.glb`. A name with no folder in brackets is looked for in the output folder first, then the input
  folder; `[output]`, `[input]` or `[temp]` after it says which. From the command line a file on your computer of up to
  100 MB can also be named by its path; NPC Forge sends it to ComfyUI's temporary folder first.

## Or send a file from this computer

**Use my own 3D model…**, in the 3D card under the model's buttons (and under **Generate 3D model** when there is none), opens the browser's file picker: any `.glb` on this computer, up to 320 MB,
becomes the character's model **exactly as it is** (`POST /api/npcs/<id>/model-upload`: the bytes of the file with the content type `application/x-npcforge-glb`, `?name=` says what it is called; API 42).
ComfyUI is not needed, and nothing is closed, simplified or painted: the file is stored byte for byte, and the card says it was uploaded and how many faces it has. From there it is an ordinary model:
the clean-up and paint step, the skeleton step and the rest work on it like on any other. It must be a binary glTF 2.0 (a header that says more bytes than arrived is refused as incomplete, and so is
anything that is not a GLB or has no triangles), and one that stands along the Y axis and faces +Z lines up with the character's pictures; warnings about the file (a skeleton that is not kept, a model lying on its side)
are shown when it is sent. A raw generator shape is bigger than 320 MB and needs closing and simplifying first, which is what the import below does inside ComfyUI. The model it replaces is kept (*Earlier 3D models*, below).

## Using it

In the 3D card, open **Import a 3D model made elsewhere**, choose the file, look at what it says about it (faces, whether it
has colour of its own, any warnings), choose the options and press **Import**.

```bash
# a file in ComfyUI's output folder (the output folder is tried first, then the input folder; a sub-folder goes before the name)
python -m npcgen generate --ids <character-id> --import-3d Elf_knight_00001_.glb
python -m npcgen generate --ids <character-id> --import-3d "3D/Trellis2_00001_.glb [output]"

# a file of up to 100 MB on this computer
python -m npcgen generate --ids <character-id> --import-3d C:\models\shape.glb

# and paint it from the pictures in the same go (needs the character's model sheet; made first if missing)
python -m npcgen generate --ids <character-id> --import-3d "shape.glb [output]" --refine-3d
```

It goes into **one existing character** (`--ids` with a single id). If the character already has a 3D model, the new
one replaces it and its clean-up goes with it; its skeleton is kept but marked out of date (the rig card makes it again); the card asks first.

### Options

- **Close the holes and simplify the mesh in ComfyUI** (`--import-clean auto|yes|no`). *Auto* means yes for a model with no
  colour of its own and more than a million faces (a raw generator shape), and no otherwise: a model that has its own
  texture would lose it (the remesh builds a new surface, which has no texture layout), and a model that is already a
  sensible size needs nothing. *No* stores the file exactly as it is.
- **Faces to keep** (`--import-detail low|medium|high`): the remesh grid and face count of Pixal3D's three settings: Low
  closes the surface on a 512 grid and keeps 350,000 faces, Medium 768 and 700,000, High 1,024 and 1,000,000. (The textures
  and the time those names also stand for in the Pixal3D picker do not apply: nothing is generated or baked here.)

### What it checks

It reads the start of the file (its description, not the geometry, so even a 400 MB file is looked at in an instant) and
refuses, in words, a file that is not a GLB, is glTF 1.0, requires glTF extensions ComfyUI's loader cannot read (Draco or
meshopt compression, quantization: export it again without them), is damaged or has no triangles. It warns, without refusing,
when

- the model does not stand along the Y axis (glTF's up). The painting step expects the model standing on +Y facing +Z, which
  is how Trellis2 and Pixal3D write theirs; a model lying down or facing another way will not line up with the pictures;
- it carries a skeleton or animations (NPC Forge makes its own, so they are not kept);
- parts of it are lines or points rather than surfaces (left out).

Size and position do not matter: the painting step fits each picture to the model.

## What is stored

`model_3d.glb` (and its small preview render when the mesh was cleaned), with the model's record saying
`engine: "Imported: <file name>"`, `engine_id: "import"`, and `imported`: the file's name and folder, its faces before and
after, whether it was cleaned, and the seconds it took. `textured` is true only for a file that kept its own texture (not
cleaned), so the clean-up paints every other import from the pictures, as it does for Hunyuan3D's shapes
([model-cleanup.md](model-cleanup.md)). The original file stays where it was.

If you ask for the clean-up in the same request and the character has no model sheet yet, the missing pictures (and the
sprite they are drawn from) are made first, after the import, which comes first so that a file that cannot be used stops
the job at once. A failed import ends the job: nothing after it has a model to work on.

## Earlier 3D models

A 3D model that is replaced (by another generator's, an import, an uploaded file or a designed body from the Body Designer) or deleted by hand is kept, with its skeleton and everything else the model owned in
the character's folder (its original, its picture, its clean-up board, its sharper face's close-ups: every file whose name starts `model_3d`), in `model_history/<id>/`, and listed in `npc.json` →
`assets.model_history` (`id`, `replaced`, `model`, `rig`, `names`, `bytes`). The 3D card lists them under the model, each with its picture, its size and its dates, and **Bring back** and Delete.
Bringing one back swaps it with the model in use: that one is kept in turn, so nothing is lost either way, and the skeleton the earlier model had comes back with it
(`POST /api/npcs/<id>/model-history/<id>/restore`; `DELETE` on `/api/npcs/<id>/model-history/<id>` removes one for good; both answer 409 while a job for the character is queued or running; API 41).
The last two are kept per character (`config.json` → `library.model_history`; 0 keeps none), because a picture-made model with its skeleton takes 60 to 140 MB. The files are copied before anything is written over them, so
a replacement that fails leaves the model as it was, and a model whose copy cannot be made (no room on the disk) is replaced as it would have been without a history; bringing a model back refuses instead of giving
up the one in use. An earlier model made from a sprite that has been drawn again since is marked out of date when it comes back. A model replaced before this existed was not kept; the generators' own copies are
still in ComfyUI's output folder (`output/npcforge/`, named after the character and the job) and can be imported or uploaded again. Clean-up, rigging and the Items tab's try-ons change a model in place and do not count as replacing it.

## Making that shape inside NPC Forge: Trellis2 from four pictures

The shape that did best does not have to be made by hand in another workflow. Choose **Trellis2 four pictures (shape only)**
as the 3D model generator in the Models card (`--three-d-model trellis2`) and **Generate 3D model** does what the comparison
below did, in one ComfyUI job: it makes the character's four model-sheet pictures if they are missing, cuts each out and sends
the front, left, back and right ones (azimuths 0, 90, 180 and 270 degrees) to the Trellis2 node pack's Pixal3D multi-view
route, decodes the shape and runs the same mesh clean-up as an import. The result is kept as the character's model. Like an
import it has no colour of its own, so paint it afterwards with **Clean up + paint from pictures** (never automatic).

```bash
python -m npcgen generate --ids <character-id> --model-3d --three-d-model trellis2 --refine-3d
```

`--three-d-quality low|medium|high` sets how many faces it keeps, as for an import (medium: 700,000). It is the only setting:
the sampling is the node pack's own example for this route (heun, 25 + 12 + 12 steps, cascade to 1024; a finer cascade was not
better, below). Measured on Airi: 232 seconds for the shape and about 25 for the clean-up when the route was run by hand, and
248 seconds for the whole step when NPC Forge ran it (2026-10-02, a different seed: 696,156 faces). In that run Windows' commit
charge went from 22 to a peak of 45.7 of 68.3 GiB (a Pixal3D run peaks at about 52) and free RAM never fell below 32 GiB.

### What it needs

- The Trellis2 node pack for ComfyUI (<https://github.com/visualbruno/ComfyUI-Trellis2>), a version with Pixal3D multi-view
  support: its model loader has a `pixal3d_multiview` setting (1.0.27 here), plus the pack's own requirements.
- The `TencentARC/Pixal3D` model in that pack's model list: Pixal3D's multi-view weights (about 43 GB with everything in the
  release; this route reads the `_mv` ones).
- BiRefNet in `models/background_removal`, the file Pixal3D already uses.
- NPC Forge's own decode node, below: `python scripts/install_pixal_head_node.py <ComfyUI folder> --trellis2`, then restart
  ComfyUI once.

The Models card lists what is missing and keeps the option switched off until it is there. A copy of the node from before the
last update is named too: ComfyUI keeps a custom node as it was when it started, so after the file is replaced the old one
keeps running until ComfyUI is restarted.

### Why a node of NPC Forge's own sits in the middle

The pack's own decode node (`Trellis2DecodeLatents`) always ends a shape decode with a CuMesh hole fill. On an AMD card under
Windows (ROCm 7.15, R9700) that CuMesh kernel failed with `hipErrorLaunchFailure` on the four-picture mesh: Windows reset the
GPU (event LiveKernelEvent 141) and ComfyUI aborted, losing the job. `NPCForgeTrellis2ShapeDecode` decodes the same latent
without the fill and hands the mesh on twice, as the pack's own mesh type and as ComfyUI's core one, which ComfyUI's RemeshMesh
takes: the remesh closes the holes the fill was for. Do not use the pack's `Trellis2DecodeLatents`, FillHoles, Reconstruct or
Simplify nodes (all CuMesh) on such a card. The single-picture Trellis2 workflows that skip the fill (the GGUF pack's) give
a mesh with pits instead, which the remesh closes as well.

Licences: the node pack, TRELLIS.2 and Pixal3D are MIT; see [NOTICE.md](../NOTICE.md) for DINOv3 and the others.

### Soften the eyes for the shape (optional)

At the model sheet's size an eye is about 17 pixels wide. Trellis2 cannot tell dark eye make-up, lashes and lid lines from
shape, and sculpts them: a narrow slit with a raised rim round each eye (Devi, Laura), and on Natalie a visor of hair over
them. The paint draws the eyes anyway, from the pictures, so **Soften the eyes for the shape** (Models card, shown for
Trellis2 only; `--soften-eyes`) makes the shape from copies of the four pictures in which each eye is filled with the skin
round it:

1. SDPose (the model the medium skeleton uses) finds the face points in the front, left and right pictures, and in the front
   one again on a close-up. The back picture is left as it is.
2. Each eye whose points scored well is a soft ellipse that reaches up to the lid and stops short of the eyebrow, filled from
   the skin around it. Nothing else in the picture changes, and the white background never leaks into the fill.
3. Only the shape generator sees these copies. The model sheet's own pictures, which the paint reads, are never touched.

It adds two SDPose runs (a few seconds each, plus loading the model) to the four minutes, and the model records that it was
done (`eyes_softened`, shown on the 3D card). It needs SDPose in ComfyUI, like the medium skeleton, and a face SDPose can find:
when it cannot find the front picture's eyes the job stops before the generator runs, with a message, instead of making an
unchanged model.

Measured 2026-10-02 on Devi, Laura and Natalie (the same seeds, the plain model once and the softened one once each through the
app; Devi's and Natalie's softened shape once more by hand), clay renders and then both painted by the clean-up with the sharper
face:

- **Devi:** the plain shape has slits with raised lids; the softened one has none. Painted, the plain model's eyes are ringed
  and uneven, the softened model's are even and natural. Of the two softened runs one left a flat band across the eyes, the
  other a smooth face with no sockets at all.
- **Natalie:** the plain shape has a visor of hair over the eyes; softened, the hair parts and the eyes are soft hollows. Painted,
  the softened model's eyes are clearly better, though a hair strand still ghosts across one of them.
- **Laura (glasses):** it went wrong. With the eyes behind the glasses filled in, the generator built solid lenses, like
  sunglasses, where the plain run has open frames; the painted model hid it by painting the eyes onto the lenses (one eye landed
  on the near frame in the three-quarter view), but the shape is wrong. A character whose sheet says it wears glasses (the
  `glasses` appearance field, or sunglasses, spectacles, goggles, a monocle in the accessory, extra or distinguishing-features
  fields) is therefore left alone: the job's step says "Eyes not softened: this character wears glasses ...", the model records
  `eyes_not_softened`, and the 3D card says "eyes not softened (glasses)". (With **Leave the glasses off the 3D model**, next, the
  pictures have no glasses and the eyes are softened like anyone else's.)

With the clean-up's **Attach the detailed head** afterwards the softened shape did better still on Devi and Natalie (no slits or hair
visor under the head's own eyes; see [detailed-head.md](detailed-head.md)): a shape with the eyes softened, cleaned up and painted
with the head attached, is the best chain tried so far for a character without glasses (the sharper face was not part of that run:
the head's face replaces it).

Limits: the generator is not bit-for-bit repeatable (the same seed gave different hair in each run), so a plain/softened
pair differs by chance too; the slits were gone in every softened run (Devi and Natalie, two runs each) and there in every plain
one. The eyes it leaves are flat, with no eye sockets: the paint has to draw them. A model whose eyes come out well anyway
gains nothing, so it stays off unless ticked.

### Leave the glasses off the 3D model (optional)

Glasses are the one thing that went wrong in every part of the chain: thin frames are what an image-to-3D generator reads worst. Laura
(thin round glasses), 2026-10-02: with the glasses in the pictures Trellis2 made open frames, and the detailed head attached to that
body then showed the arms and the outer corners of the lenses twice; with the eyes behind them softened it made solid lenses, like
sunglasses. **Leave the glasses off the 3D model** (Models card, Trellis2 only; `--glasses-off`) makes the model and the paint from copies of
the four model-sheet pictures with the glasses taken off, and draws the detailed head's close-ups without them, so there is nothing to
turn into lenses or to double, and the eyes can be softened:

1. For each of the four pictures the head is cut out, made three times as large (the whole figure's face is only about 100 pixels wide, too
   small for an edit model to redraw faithfully) and given to the job's own **Edit model** with "take the glasses off this character; keep
   everything else exactly the same". The result is made small again, and only the pixels that changed are put back: the ones the model
   redrew strongly (the glasses, the eyes), and everything within 14 pixels of them that differs by more than 6 levels (of 255), which takes away the faint
   lens rims the edit leaves behind. The rest of the picture is exactly as it was.
2. The copies are kept next to the sheet (`sheet/no_glasses/`) and recorded on the model (`glasses_off`). The shape, the eye softening,
   the clean-up and paint, the sharper face, the hair, the hands and the head attach all read them. The sheet's own pictures, and
   every picture of the character, keep the glasses.
3. The detailed head's front close-up is drawn with "the face is bare" and no glasses in its description, and the other three from it
   (`head_3d.glasses_off`); a head made with the glasses is drawn again by Best quality.

Measured on Laura's four pictures: both Edit models take them off. Mage-Flow-Edit (built in, about 40 s a picture) redrew 0.6% of the
head crop and left her face as it was; Qwen-Image-Edit 2511 (about 35 s, 29 GB of VRAM) redrew 12%, the eyes with liner, and also kept
her recognisable. Editing the whole picture instead changed the face noticeably with either, and a plain threshold on the changed
pixels left the lens rims as pale arcs on the cheeks (which the wider area removes). If the edit leaves the glasses on, the step stops
with a message; inside **Best quality** it carries on with the pictures as they are and says so.

Through the whole chain (`--best-3d` on a scratch copy of Laura, no detailed head yet, 2026-10-02; the job's Edit model was the built-in one):
the four edits redrew 0.4% to 0.6% of each head crop (5% to 13% was put back once the wider area was added), the shape step took 389 s with
them in it (240 to 340 s for the other characters without them), the four close-ups of the head 188 s, the head model 1041 s and the
clean-up and paint 609 s: 37 minutes in all. Her model has no frames and no lens rims, the head's close-ups were drawn bare, the eyes were
softened like anyone's, and the attach step had no note about glasses to give because body and head both lack them. The face shows
the same weak spot as the other characters (patchy skin at the forehead and on one cheek, a few dark flecks at one temple: the attach
step's edge). No version of her was made the other way from the same roll, so that is not a comparison.

Limits: the 3D model has no glasses, by design: they are meant to come back as a separate, removable item. For sunglasses, goggles or a
visor the Edit model has to invent the eyes behind them. Only a character whose sheet names glasses (above) is touched.

## Best quality in one click

The 3D card's **Best quality: make it all** (`--best-3d` on the command line) ticks, for one character, the chain that came out best
when the same characters were made every way, and runs it as a single job. Nothing in it is new: each part is an option that has its
own button, because a future generator or LoRA may not need some of them, and this is only a shortcut for pressing them together.

1. **The shape:** Trellis2 from the model sheet's four pictures (made first if missing), 700,000 faces, with the eyes filled with
   skin in the copies the shape is made from. For a character whose sheet says it wears glasses the copies have them taken off first
   (above), which is also what lets the eyes be softened.
2. **A detailed head** ([detailed-head.md](detailed-head.md)), if the character has none: its four close-ups (drawn without the
   glasses, for a wearer) and the Pixal3D model at Medium detail with a neutral face. A head that is up to date is used as it is; one
   whose close-ups exist but is out of date is built again from those close-ups only; a wearer's head that was drawn with the glasses
   is drawn again.
3. **The clean-up and paint** from the pictures with four options on: the sharper face, the hair matched to the pictures, clean
   five-finger hands and the detailed head attached.

It sets the generator and those settings itself, whatever the Models card says (they are the ones the chain was measured with:
use the separate buttons to try another). It never starts on its own, asks before replacing a model that exists, and does not
add a skeleton or the other options: a skeleton goes into the cleaned-up model, so **Add skeleton + animations** comes after it,
and **Smooth eye rings** and the LM Studio check stay separate (the first flattens normal eyes).

It needs everything its parts need: the Trellis2 nodes and Pixal3D's multi-view model, SDPose, SAM 3.1, the upscaler, Pixal3D and
BiRefNet for the head, and Blender. The button is off and names what is missing.

```bash
python -m npcgen generate --ids <character-id> --best-3d
```

Measured 2026-10-02 through `--best-3d` on scratch copies of Devi and Natalie, whose detailed heads were already made (a new Trellis2
roll each, so the shapes are not the ones of the earlier runs): Devi 241 s for the shape and 732 s for the clean-up and paint with its
four options, 16 minutes in all; Natalie 344 s and 582 s, 15.5 minutes. Windows' commit charge peaked at 56.9 of 68.3 GiB and free RAM
never fell below 31 GiB. Against the earlier best chain (the softened shape with the head attached and none of the other options) the
faces look about the same, and the other options did what they are for: Natalie's trousers lost the skin-coloured patches the old
hands had painted on them, both got five-finger hands, and Devi's hair lost most of the skin tint it had near the face and the
shoulders. Not fixed by any of it: the patch's forehead edge and the skin-coloured strands of hair at the cheeks.

A character with no detailed head yet takes longer, because the chain makes it: Laura (glasses) took 37 minutes in all, the shape 389 s
(with the four glasses edits), the head's four close-ups 188 s, the head model 1041 s and the clean-up and paint 609 s.

## Measured

Airi Nakamura, the same four turnaround pictures for everything, AMD Radeon AI PRO R9700, ComfyUI 0.37.0 (2026-10-01).
All rows painted by the clean-up from the pictures into a 100,000-face game version. *Outline match* is how well the
model's silhouette agrees with each picture's (1.0 is perfect; the paint step reports it).

| Where the shape came from | Time to make it | Imported as | Outline match front / left / back / right | What the painted model looks like |
|---|---|---|---|---|
| Trellis2 nodes, **one** picture | 4 min, 10.1M faces | 700k, 25 s | 0.95 / 0.66 / 0.91 / 0.64 | The best hair, hands and coat detail, but blocky patches on the trousers and lower coat and pale slits under the eyes: one picture cannot say how deep the model is, so its sides do not agree with the side pictures |
| Trellis2 nodes, **four** pictures | 232 s, 7.5M faces | 695k, 24 s | 0.965 / 0.931 / 0.945 / 0.929 | The best of everything tried: clean body and hair, no blocky patches, no pale slits, a readable face (the paint step took 14 minutes) |
| NPC Forge's Pixal3D, Medium | 22 min, 697k faces | none needed | 0.95 / 0.94 / 0.93 / 0.93 | The most even texture, but a soft face and hair melted around it |

One character, one run each: indicative, not a benchmark. A finer Trellis2 cascade (1536 instead of 1024) made 24
million faces and was not better: stronger ring-shaped rims around the eyes and more holes.

The import itself was then run inside the app, on a scratch copy of her library, with the four-picture shape in ComfyUI's
output folder: 24 seconds to a 694,546-face model, and with `--refine-3d` in the same request 920 seconds for the paint (the
machine was busy with other work; 836 s when it was not), giving the same outline match (0.9649 / 0.9304 / 0.9452 / 0.929) and a
99,999-face game version. A model that came with its own texture (NPC Forge's own Pixal3D model of her, handed over as a
file of up to 100 MB) was kept as it is and painted the Pixal3D way in 126 seconds. The import list and the Import button
were driven in a browser.

## Limits

- The file has to be visible to ComfyUI (above). There is no GLB upload from the browser: the API takes small JSON requests (2 MB at most), apart from the Scenes tab's recorded frames.
- Compressed GLBs, other formats (FBX, OBJ) and GLB files with several characters in them are not handled.
- A cleaned import loses its texture (the remesh builds a new surface with no texture layout); keep a texture by choosing
  not to clean the file. The clean-up then paints over it from the pictures wherever they see it.
- The remesh closes holes by rebuilding the surface, so very thin parts (a loose strand, a thin blade) can be thickened or
  lost. Look at the preview before you paint it.
