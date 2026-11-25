# 3D models

Choose **Hunyuan3D v2.1** or **Pixal3D multi-view** in the Models card, then tick **3D model** under What to make or use
**Generate 3D model** on a character. Both generators run locally through ComfyUI. Hunyuan makes the shape only (no colour),
from the full-body sprite or, with **Hunyuan3D picture: Model sheet front** in the Models card
(`--three-d-hunyuan-source turnaround`), from the model sheet's straight-on front picture, made first if missing; choose
that one if you will paint the model (below), since the shape then lines up with the side and back pictures.
**Hunyuan3D detail: Fine** (`--three-d-hunyuan-detail fine`) reads the shape from a finer grid: a smoother nose and face,
for about a minute more to generate and two more to paint. Pixal3D makes any missing front, left, back and right model-sheet views, then removes their backgrounds, creates the shape,
unwraps the mesh and bakes base-colour, metallic, roughness, normal and ambient-occlusion maps into a textured GLB. Forge
saves the model and a render preview in the character library, and includes them in the library ZIP export. From a terminal,
use:

```bash
python -m npcgen generate --ids <character-id> --model-3d
python -m npcgen generate --ids <character-id> --model-3d --three-d-model pixal3d
```

A newly rolled character can use `--model-3d` too.

## Importing a 3D model made elsewhere

A model another ComfyUI workflow made (a Trellis2 shape, a Hunyuan3D run, a downloaded GLB) can be brought into a character
with **Import a 3D model made elsewhere** in the 3D card, or `--import-3d`. ComfyUI's own mesh nodes close its holes and bring
it down to 350,000, 700,000 or 1,000,000 faces (about 25 seconds for a raw shape of 7.5 million), and it becomes the
character's model, ready for the clean-up and paint below. The file has to be in ComfyUI's output, input or input/3d folder.
In the one comparison made, a Trellis2 shape built from all four of a character's pictures and imported this way gave the
best-looking model of everything tried. Details, numbers and limits: [model-import.md](model-import.md).

A file of your own can also be sent from this computer with **Use my own 3D model…** in the 3D card (any `.glb` up to 320 MB, kept exactly as it is, no ComfyUI needed). And a 3D model that is replaced
(by another generator's, an import, an uploaded file or a designed body) or deleted is not lost: the last two are kept with their skeletons in a list of **Earlier 3D models** under the model, and **Bring back**
swaps one with the model in use ([docs/model-import.md](model-import.md#earlier-3d-models)).

```bash
python -m npcgen generate --ids <character-id> --import-3d "shape_00001_.glb [output]" --refine-3d
```

NPC Forge can also make that shape itself: choose **Trellis2 four pictures (shape only)** as the 3D model generator in the Models
card (`--three-d-model trellis2`). It sends the model sheet's four pictures to the Trellis2 ComfyUI node pack (about four minutes
on the test card) and runs the same mesh clean-up, in one job. It needs that node pack, Pixal3D's multi-view model and a small
node of NPC Forge's own: `python scripts/install_pixal_head_node.py <ComfyUI folder> --trellis2`. The Models card lists what is
missing. Optionally (**Soften the eyes for the shape**, `--soften-eyes`, needs SDPose) the shape is made from copies of the pictures
with the eyes filled with skin, which takes away the slits and raised rims Trellis2 sculpts round the eyes; the paint still reads
the original pictures.

**Best quality in one click:** the 3D card's **Best quality: make it all** (`--best-3d`) runs the chain that came out best so far as
one job: the Trellis2 shape with the eyes softened, a detailed head if the character has none yet, then the clean-up with a sharper
face, the hair matched to the pictures, clean hands and the detailed head attached. It needs everything those parts need (the button
names what is missing), never starts by itself, and every part is still an option of its own. See
[docs/model-import.md](model-import.md#best-quality-in-one-click).

**Leave the glasses off the 3D model** (Trellis2, `--glasses-off`): glasses are what an image-to-3D generator reads worst (solid lenses,
or arms shown twice once the detailed head is attached), so for a character whose sheet has them the Edit model takes them off copies
of the four model-sheet pictures and the detailed head's close-ups are drawn without them; the model has none, and the character's own
pictures keep theirs. See [docs/model-import.md](model-import.md#leave-the-glasses-off-the-3d-model-optional).

## Game version: clean-up and painting

Pixal3D rebuilds colour through a coarse texture grid, so a face that is sharp in the model sheet comes out smudged on the
model, and every surface of its mesh is a hollow shell with a second, hidden skin just inside it. **Clean up + paint from
pictures** in the 3D card (never done automatically, since a future generator may not need it) makes NPC Forge, on the
CPU in about two minutes:

1. removes that hidden inner skin (about a third of the faces), only where it sits right behind a visible surface;
2. paints the model with the front, side and back pictures' own pixels, each picture lined up with the model's outline
   (and the head lined up on its own), keeping the generator's colour only where no picture sees the surface;
3. makes a game version with Blender, its colour and detail baked from the full model into new maps (about 20 MB instead of
   41 MB). It keeps 100,000 faces unless you choose otherwise, **per character**: Light 25,000, Standard 50,000, Detailed 100,000 or
   High 200,000 in the Game size picker (or any number from 10,000 to 400,000 with `--game-size` or the `game_faces` option). The time is
   the same at every size and the file is 16 MB at 25,000 faces and 26 MB at 200,000; clean hands need 100,000 or more to have real
   fingers (docs/model-cleanup.md has the measurements).

A Hunyuan model has no colour at all, so the button reads **Paint from pictures + clean up**: the model first gets a texture
layout of its own (unwrapped with `xatlas`, the head on its own half of a 4096 × 2048 texture), then is painted from the
model sheet's front, side and back pictures, which are made first if missing; what no picture sees takes the nearest
painted colour. About two minutes on the CPU (four for a Fine model). The body comes out well; the face is only as good
as Hunyuan's shape of it (a voxel grid about 7 mm across, 4.5 mm at Fine), so a nose or hairline that does not match the
picture shows.

The generator's own model is kept as `model_3d_original.glb` (**Original GLB** in the card), and doing it again always starts
from it. From a terminal, use `python -m npcgen generate --ids <character-id> --refine-3d`. What it fixes, what it does not (eye shapes the generator
sculpted, hair it made bigger than the pictures have it) and the measurements are in [model-cleanup.md](model-cleanup.md).

Five options above the button, ticked per character (each also a terminal flag):

**Sharper face** (`--sharp-face`): the head of each turnaround picture is upscaled 4× by an upscale model in ComfyUI, and the head gets a texture of its
own (the colour texture becomes twice as wide, the body's half untouched), so the model can hold the extra detail: about
2.3× the texture detail on the head, about three minutes in all, and a game version of about 30 MB. It needs the `xatlas`
package (in `requirements.txt`) and a 4x upscale model in ComfyUI's `models/upscale_models`: by default
[RealESRGAN_x4plus.pth](https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth) (BSD-3-Clause,
67 MB; crisp and faithful). `"face_upscaler": "4x-UltraSharp.pth"` in `config.json` keeps more skin texture but invents a
little and is **non-commercial only** (see [NOTICE.md](../NOTICE.md)).

**Smooth eye rings** (`--smooth-eyes`): for a model with ring- or goggle-like loops sculpted around the eyes (Pixal3D
sometimes reads dark eye make-up as shape). A vision model in [LM Studio](https://lmstudio.ai/) finds the eyes on the front
picture (by default `qwen/qwen3.6-35b-a3b`, or `qwen/qwen3.8-27b`; `"vision_model"` in `config.json` names another), and
the region round each eye is relaxed into a smooth surface pinned to the face around it; the pictures then paint the eyes
onto it. Only tick it for models that have the rings: it flattens normal sculpted eyelids too. LM Studio's local server
must be on; its model is loaded only for the question and unloaded again (one model on the GPU at a time).

**Match the hair** (`--match-hair`): for a model whose hair the generator made bigger than the pictures have it (a fringe
hanging lower, locks over the cheeks), which painting from the pictures streaks with the skin behind it. SAM 3.1 in
ComfyUI finds the hair in each turnaround picture and on the bare model drawn the same way, and the model's hair is then
painted only from hair (its ears and eyes never count as hair). Hair the pictures show but the shape does not (a short
crop) is still painted on. It needs [sam3.1_multiplex_fp16.safetensors](https://huggingface.co/Comfy-Org/sam3.1/resolve/main/checkpoints/sam3.1_multiplex_fp16.safetensors)
(1.75 GB, SAM Licence: commercial use allowed; see [NOTICE.md](../NOTICE.md)) in ComfyUI's `models/checkpoints`, and adds
about a minute. Where the model has an ear somewhere other than the pictures do, that ear takes the hair colour the
pictures show there.

**Clean hands** (`--clean-hands`): the generators make mittens (two or three lumps for five fingers), because in a
full-body picture a finger is only a few pixels wide. SDPose in ComfyUI (the one the rig uses) finds the hands on the model
and in the turnaround pictures; each mitten is cut off at the wrist and replaced by a clean five-finger hand of the
right size, joined to the arm and painted in the skin colour of the pictures' hands, with creases, lighter palms and
nails. The pictures' hands and forearms are no longer painted onto the clothes behind them, and the medium skeleton's finger
bones sit in the new fingers, so fists and pointing work. Not for paws, claws or hooves. Adds about a minute and a half;
see [model-cleanup.md](model-cleanup.md).

**Attach the detailed head** (`--attach-head`, needs the detailed head below): the generators' faces are masks. SDPose finds
the face on the model and on the detailed head, the head is fitted on the body's face from the nose, eyes and mouth, and a patch
of it (chin to the hairline, cheek to cheek, cut along a smooth curve; the head model's inner skin left out) is laid over the mask and joined to it, in the body's skin tone; the body keeps its hair,
ears and neck, and the skeleton and the game version carry the new face. Adds about a minute. A first version: eyes, nose and
lips come out much sharper and the patch ends along a smooth curve at the hairline, but the seam shows from the side as a faint
crease, it tears open along the jaw when the jaw bone moves, and glasses and hair over the face come out worst; look at the result
before relying on it, and it is not for faces that are not human. See [detailed-head.md](detailed-head.md).

**Check with LM Studio** (`--check-3d`), under a cleaned-up model: Blender renders the generator's original and the
cleaned-up model from the front, and a vision model in LM Studio (the first installed of `local/muse-glimmer-30b`,
`qwen/qwen3.8-27b` and `qwen/qwen3.6-35b-a3b`)
is shown them beside the model sheet's front picture and asked which is closer to the character and what looks wrong
with each. It is asked twice with the two swapped; if the answers disagree, the card says there is no clear difference.
The card shows the pictures it compared, the verdict, its 1-10 scores and the problems it named. About two minutes. It
is the model's opinion, not a measurement: it reliably spots a doubled face, unpainted grey, black eye rings or streaks
of the wrong colour, but it cannot tell two close results apart.

## Detailed head

Only when asked, under the 3D model (`--head-3d`): a separate 3D model of the character's head alone. The whole-body run
reads a face from about 125 pixels of a 1024-pixel picture, so its faces come out as masks: goggle-like eye rings or holes,
a bump for a nose, a ridge for lips, and glasses as solid plates. Given only the head, filling the same frame, Pixal3D builds
real eyelids, nostrils, lips, ears, and thin wire glasses with the eyes behind them.

Four close-ups of the head and neck (front, both sides, back) are drawn by the edit model (the engine chosen in the Models
card): the front one from the portrait, and the other three from the front one, so all four agree about the hair and the
skin. The face is **neutral** unless you pick "as in the portrait" (a smile is baked into the shape,
and a jaw bone should open a mouth that starts closed). A portrait that shows the whole body (a photo of a standing person)
gives the face too few pixels, so the model sheet's front picture, its head upscaled 4x, is the reference instead. The four
are cut out and framed with one shared scale, Pixal3D makes the shape from them, the hidden inner skin and loose specks are
removed, and the colour is **painted from the four pictures** with the exact cameras Pixal3D read them through (its own
colour for a head is an orange tan, with muddy olive on everything thin), so the face comes out close to the pictures: skin
tone, irises, lashes, brows. Medium takes about 15 to 25 minutes on the GPU. High takes twice as long (35 minutes) and, on
the one character it was measured on, gave no better a face (the finer grid pulls each eyelid apart into stacked plates), so
Medium is the default. It needs Pixal3D and BiRefNet in ComfyUI, but not NPC Forge's own head-texture node, so ComfyUI needs
no restart. The result is its own asset (`head_3d.glb`, with the original and the four pictures); the body model is not
touched until you attach the head to it (Game version, **Attach the detailed head**).

The four pictures stay on the card, each with a **Redo** (a new attempt at that one picture, which marks the head outdated);
**Rebuild the head from these pictures** then makes the model from the four as they are, so a picture that came out wrong
(a braid on the wrong side, a lopsided mouth, glasses drawn differently in each view) costs a few minutes to fix, not a whole
run. **Match the hair** (`--head-match-hair`, off unless ticked, needs SAM 3.1 in ComfyUI, about 3 minutes more) keeps skin
colour off hair that falls over the face, where a side picture shows the cheek behind a lock the front picture shows as
hair; measured, it helps on some characters and leaves a grey patch on others. Details, measurements and limits:
[detailed-head.md](detailed-head.md).

## Skeleton and animations

On a character with a 3D model, **Add skeleton + animations** (under the model) rigs it with [Blender](https://www.blender.org/)
running in the background on the CPU. The result is a second GLB, `model_3d_rigged.glb`: the same textured mesh, a humanoid
skeleton with Unreal mannequin bone names (`pelvis`, `spine_01`, `upperarm_l` ...), which Unreal's and Godot's retargeters
recognise, skin weights, and in-place animation clips (below) that play in the app's 3D preview and in the scene maker's 3D
stage. From a terminal, use `python -m npcgen generate --ids <character-id> --rig-3d`, which makes the 3D model first if
there is none.

The **Skeleton** choice above the button picks how much of the body moves (`--skeleton`):

| | Bones | What it adds |
|---|---|---|
| Low (the default) | 23 | Body, arms, legs and head: walking, standing, gestures. The hands move as one piece and the face is still. |
| Medium | 60 | Three bones per finger (hands that grip, point or make a fist), a **jaw** for talking (the lips are cut apart along the line where they meet, with a dark mouth behind them; the scene maker's 3D stage moves it with the voice), a twist bone in each upper arm and forearm (the forearm turns gradually instead of all at once at the elbow), and a prop bone in each palm to attach a held object to. |

The low skeleton's 23 bones are the same in both, so a clip made for one plays on the other. The joints are found with
**SDPose** in ComfyUI when it is installed (see `NOTICE.md`): Blender renders the model from the front and both sides, SDPose
finds the body, hand and face points in the renders, and they are turned into 3D. Without it they come from the model's
front outline, as before, which misses arms that touch the clothes: on a character in a long-sleeved dress it put the wrists
at the elbows and the hips 18 cm low. The medium skeleton needs SDPose. A rig takes about a minute and a half (40 seconds
from the outline alone), and about 45 seconds more with the animation library.

The **Clips** choice picks the animations (`--clips`):

| | Clips |
|---|---|
| Basic | Idle (breathing), Walk and Wave (palm to the front), keyframed here |
| Animation library (the default once installed) | 85: the 84 clips of [Quaternius' Universal Animation Library 1 and 2](https://quaternius.com/packs/universalanimationlibrary.html) (the free Standard packs, CC0) copied onto the skeleton, and our Wave. Standing: idle, talking, on the phone, arms folded, nodding yes and shaking no, leaning on a rail. Moving: walk (formal, carrying something), jog, sprint, crouch, push, swim, jumps, climb, roll, slide, zombie walk. Sitting down, sitting and talking, standing up, driving, getting up from lying down. Doing: interact, pick up, drink, open a chest, kneel and fix, farming, chopping wood, throwing, dancing. Fighting: punches, sword, shield, pistol and spell moves, being hit, dying. |

Install the library once:

```bash
python scripts/install_animations.py
```

It downloads both free packs from itch.io (35 MB) and keeps the two files the rig step reads (16 MB) in
`animations/quaternius/`. The library's idle and walk are named Idle and Walk, which the 3D stage plays; the other clips
keep the library's names (its animation viewer, <https://quaternius.com/animviewer.html>, shows them all). They add about
5 MB to the rigged GLB. The clips are copied onto each character's own skeleton, so they fit its proportions; what that
cannot do yet (props in the hands, feet that slide a little) is in [rigging.md](rigging.md).

Blender (tested with 5.2) is found in `C:\Program Files\Blender Foundation`, on the PATH, or from `"blender"` in `config.json`.
Only two-legged characters can be rigged so far. The model works best from an **A-pose** model sheet (arms clear of the body).
How the joints are placed, what the skin weights do and what is known not to work is in [rigging.md](rigging.md).

## A designed body

The optional Body Designer for recipe-built bodies and fitted clothes is available separately.
