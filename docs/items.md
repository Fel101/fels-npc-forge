# Items: things a character holds, wears or finds

The **Items** tab (between Library and Scenes) makes weapons, armour pieces, helmets and hats, glasses, shoes and boots, bags, props and
garments. **Glasses and fitted clothes are the two families that are not drawn**: a frame a millimetre and a half thick and a lens you can see through cannot be made
by an image-to-3D generator, so glasses are built from measurements instead, at once and with no GPU ([glasses.md](glasses.md)); clothes that must fit a body and move with
it (a shirt, trousers, a skirt, a dress, an open jacket, a bib apron) are the Body Designer's patterns, built from a few settings the same way and fitted to a designed body (the Body Designer add-on, not part of this release). Everything else is made
the way a character's 3D model is ([model-import.md](model-import.md), *Best quality in one click*), only there is no character in it:

1. **A main picture** from the description: a studio shot of the single object on a plain white background.
2. **Four views** (front, left, back, right) drawn from that picture by an Edit model.
3. **A shape**: Trellis2 from the four views (the same call as a character's model).
4. **Two high-angle views** (optional, on for the kinds that have a top to show): the same Edit model draws the thing again from 45 degrees
   above, in front and behind. Only the paint reads them (the shape does not need them).
5. **Paint and game version**: the clean-up paints the shape from the pictures and makes the game version
   ([model-cleanup.md](model-cleanup.md)); the finished model is then scaled to a real size and given a useful origin.

Each step has its own button too (**Redraw** for the pictures, **Redo the shape**, **Paint again**), because the pictures are the part you will want to try again most.

## What can be made

| Family | Kinds |
|---|---|
| Weapons | sword, cutlass, dagger, axe, hammer or mace, spear, staff, bow, crossbow, pistol, rifle |
| Armor pieces | shield, breastplate, pauldrons, gauntlets, greaves, bracers |
| Helmets and hats | helmet, hat, cap, crown |
| Glasses | glasses, sunglasses: twelve styles, built from measurements ([glasses.md](glasses.md)) |
| Shoes and boots | boots, shoes, sandals |
| Bags | backpack, satchel, belt pouch, quiver, sack |
| Props | barrel, crate, treasure chest, lantern, bottle, book, torch, and *Other*: name it yourself |
| Garments | coat or jacket, shirt, dress, trousers, cloak, robe: objects, drawn on an invisible mannequin (not clothes that fit a body: those are the Fitted clothes below) |
| Fitted clothes | sleeveless shirt, fitted trousers, knee-length skirt, sleeveless dress, open jacket, bib apron: built from measurements and tried on a designed body (needs the Body Designer add-on) |

The kinds are a table, `data/items.json`, read fresh on every request: add a kind there (its noun, a default description, how its
picture is staged, its usual size, where its origin goes) and it appears in the tab. Pairs (boots, gauntlets) are drawn as a matching pair
standing side by side; garments are drawn on an invisible mannequin; everything else as one object. A *setting* (the characters'
settings) adds "designed for a pirates setting" to the description, and the *look* is realistic, a stylised game asset, hand-painted
fantasy or voxel art.

## What was measured

2026-10-02 through the app's own steps (`python -m npcgen item make`, a scratch library, the R9700): a pirate cutlass, a Nordic helmet, knee-high
boots and a long wool coat, the main picture from Z-Image Turbo, the four views from Qwen-Image-Edit 2511, the shape from Trellis2 (700,000
faces), the paint and a 30,000-face game version. Seconds per step:

| Step | Cutlass | Helmet | Boots | Coat |
|---|---|---|---|---|
| Main picture | 37 (loads Z-Image) | 24 | 24 | 28 |
| Four views | 120 | 151 | 161 | 122 |
| Shape | 305 | 412 | 299 | 269 |
| Paint and game version | 561 | 410 | 463 | 648 |
| **All** | **17.1 min** | **16.6 min** | **15.8 min** | **17.8 min** |

(The cutlass' paint and the helmet's shape ran while the test suite did, which costs them a minute or two.) The paint step is the long one:
it unwraps and paints the 700,000-face shape, takes the hidden inner skin off (a third to a half of the faces were) and bakes the game
version. How well each shape lines up with the four pictures (the outline match, 1.0 is perfect; a picture that agrees less counts less):

| | front | left | back | right |
|---|---|---|---|---|
| Cutlass | 0.89 | 0.73 | 0.91 | 0.62 |
| Helmet | 0.86 | 0.74 | 0.87 | 0.76 |
| Boots, first run | 0.89 | **0.51** | 0.94 | **0.49** |
| Boots, side views told which way they face | 0.89 | **0.90** | 0.94 | **0.91** |
| Coat | 0.97 | 0.91 | 0.96 | 0.92 |

Then the helmet again, and a crate, through the finished steps (the side views told which way they face, the two high-angle views drawn
and read by the paint; 2026-10-06, the machine busy with other work for the crate's paint):

| | Picture | Four views | Shape | High-angle views | Paint and game version | All | Outline match front / left / back / right / front high / back high |
|---|---|---|---|---|---|---|---|
| Helmet | 47 s | 126 s | 456 s | 90 s | 545 s | 21 min | 0.88 / 0.83 / 0.90 / 0.83 / 0.79 / 0.86 |
| Crate | 62 s | 126 s | 555 s | 83 s | 1,029 s | 31 min | 0.77 / 0.71 / 0.77 / 0.70 / 0.65 / 0.65 |

The helmet's side views matched the shape at 0.74 and 0.76 before the side-view wording and match at 0.83 now.

What the models look like (the pictures are in the app's Items tab):

- **The cutlass and the coat paint cleanly.** The cutlass is the main picture in 3D: the curved blade, the brass cup guard with ball ends, the
  grained grip, the pommel. The coat keeps its lapels, two rows of buttons, pocket flaps and the weave of the wool (the garment is a closed
  shell, drawn on an invisible mannequin).
- **The boots needed the side views told which way they face.** Asked for "its left side" and "its right side" the Edit model drew the same
  side view twice, toes to the right in both (a pair looks alike from both sides), so the left one disagreed with the shape (0.51). A kind
  with a `front` in the table (boots, helmets, bags, garments...) now has its side views asked for as "turned 90 degrees so that its front
  points to the left (right) of the picture", which gave 0.90 and 0.91, a model painted all round and a re-run that was also faster (the
  paint 299 s instead of 463 s).
- **A thing that stands up must not be told that.** The same wording turned the cutlass onto its side, tip to the left. Swords, axes,
  spears, bows, guns and shields have no `front` in the table and keep the plain wording ("seen straight from its left side"), which is what the first
  runs used.
- **A crate, an open frame of slats, is the weak case.** Trellis2 made the slats, but with gaps and torn edges where a slat is thin, the
  paint has white blotches and blocky patches there, and the outline matches are the lowest of all (0.65 to 0.77). Solid things (a blade, a
  coat, boots, a helmet) are good; open slatted or lattice-like things need a better generator than four pictures can drive.
- **The helmet was patchy where no picture looks:** the top of the dome (the four views are all at eye level), the inside and the underside
  of the neck guard came out as blotches, white specks along the crest band and blocky grey patches, because the paint fills what no
  picture sees from the nearest painted surface. With the two high-angle views added the dome is one continuous hammered surface: the same
  shape painted from six pictures instead of four took 443 s against 410 s, and its high-angle pictures matched the shape at 0.75 (front) and
  0.87 (back). The front, sides and back were good before and are unchanged. A picture from the Edit model's idea of "45 degrees up" is a
  guess, so a poor outline match counts less, as for the others.
- **The Edit model matters.** The same pictures drawn by the built-in engines (Mage-Flow for the main picture, Mage-Flow-Edit for the views)
  were not usable: the main picture of the cutlass was a straight dagger, its four views came out almost the same, and the helmet's "back"
  view was the front again (the boots came out right). That is why *Best quality* names Z-Image Turbo and Qwen-Image-Edit 2511.
- **The high-angle views suit things with a top, not long thin ones.** Asked for a view from above and one from 45 degrees up, in front and
  behind, Qwen-Image-Edit 2511 drew usable ones of the helmet and the boots (the dome from above, the boots' openings) and unusable ones of
  the cutlass (it stood it up again or turned it upside down). So the high-angle views are on by default for armour pieces, helmets and
  hats, shoes and boots, bags and props, off for weapons, garments, shields and torches (the table's `high`, a checkbox on the item).

Memory: the views step with Qwen-Image-Edit 2511 takes Windows' commit charge to 60 to 64 of 68.3 GiB by itself (the shape step 45 to 48, the
paint 35); do not run anything else heavy during those two or three minutes, a Blender render beside it reached 64.5.

## Size and origin: "For your game"

The finished model is scaled so that its **longest side is the item's size in metres** (a kind has a usual size: a cutlass 0.85 m, boots
0.45 m; change it on the item and the model is scaled again at once, with no GPU), and its **origin** is where a game wants to hold it:
the middle of the bottom for things that stand on the ground (boots, helmets, props, bags), the middle of the model for a shield or a gun,
and a **grip** (a height up the model, the middle of the vertices there) for a sword, an axe, a spear, a staff, a bow or a torch.
The model stands on +Y and faces +Z, like the characters. Each kind also names the bone of the characters' skeleton (the animation
library's bone names: `hand_r`, `Head`, `foot_l`, `spine_03`...) it is usually parented to. This is a hint on the page, not something
that is written into the file: a game attaches it however it likes. For the held items and hats, the page does the attaching to a character for you and gives the bone and transform: [equip.md](equip.md).

The scaling is one parent node over the scene (`item_fit`), so the mesh, the texture and the generator's own shape (`model_3d_original.glb`)
are not touched, and a new size changes that node instead of stacking another.

## Using it

In the app: **Items → New item**, pick a family and a kind, describe it, **Make this item**. The queue shows the steps; the page shows
the pictures and the 3D model as they arrive, with a viewer, **Download .glb** and **Export .zip** (the record, the pictures and the model).
**Best quality pictures** (on by default) asks for Z-Image Turbo for the main picture and Qwen-Image-Edit 2511 for the views, the pair that
kept every view the same object in the test above; an engine that is not installed is replaced by the built-in one and the step says so.
Switched off, the engines picked under **Models** are used. **Two high-angle views for the paint** (a checkbox on the item, on by default for
what has a top to show) adds the two pictures from above to the job. Changing what an item *is* (its kind, description, setting or look)
marks the pictures and the model out of date (they stay, so you can compare); **Make** then makes what is missing or out of date and what
comes after it, and turning the high-angle views on for an item that was already painted makes them and paints it again from them.

On the command line:

```bash
python -m npcgen item make --family weapon --kind cutlass --setting pirates --describe "curved steel blade, brass cup guard, dark wooden grip"
python -m npcgen item make --id cutlass --redo shape finish --reroll     # another shape, painted again
python -m npcgen item make --family headwear --kind helmet --high-views off   # (on by default for a helmet; off by default for a sword)
python -m npcgen item make --family prop --kind other --name Spyglass --describe "brass, with a leather grip" --no-finish
python -m npcgen item list
python -m npcgen item export cutlass --out exports
```

Items are kept in `<library>/.npcforge-items/<id>/` (`item.json`, `hero.png`, `views/*.png`, `high/*.png`, `model_3d.glb`, `model_3d_original.glb`,
`model_3d_preview.png`, `thumb.png`), a hidden folder the character library never reads, so an item can not turn up as a character and
a character export never carries one.

Glasses are different in the app and the API: no pictures and no job (the page is a designer with a live preview, saving builds the model, and the
picture for the list is drawn in the background a few seconds later), and `python -m npcgen item make --family eyewear ...` builds on the spot; see
[glasses.md](glasses.md). A saved pair can also be fitted to a character's face (**Try them on a character** on the same page, `python -m npcgen item fit`, and
the `face`, `fit`, `fits` and `worn` routes in [glasses.md](glasses.md#on-a-characters-face)): the item then carries a `fits` field, the nudge kept for each character.

**Fitted clothes** (only with the Body Designer add-on, which is not part of this release) work the same way on the page (a designer with a live preview of the pattern, no pictures and no job) and have their own try-on: **Try it on a character** fits the garment (a shirt, trousers, a skirt, a dress, a jacket or an apron: the last two only over a body that wears a shirt and trousers, a skirt takes the trousers off and a dress takes off the shirt and the trousers)
to a character whose 3D model is a designed body, in place of the garment of that kind it was made with, in about half a minute (about a minute for a body with face shapes or spring bones, which is built again to fit it; `python -m npcgen item dress`, and the `dress` routes and
`worn` in [api.md](api.md)). A shirt's length is offered from 0.9 to 1.0, what the Body Designer fits (see the Body Designer add-on's own notes).

The API: `GET/POST /api/items`, `GET/PUT/DELETE /api/items/<id>`, `POST /api/items/<id>/make` (`{"request": {"make", "finish", "redo"},
"options": {...}}`, one job per item at a time), `DELETE /api/items/<id>/assets/(hero|views|high|model_3d)`, `GET /api/items/<id>/files/<file>`
and `GET /api/items/<id>/export.zip`; the kinds come with `/api/config` (`items`, with the glasses' and the clothing's tables) and what is missing with `/api/health` (`items`).

## What it needs

ComfyUI with the Trellis2 nodes and Pixal3D's multi-view model (the ones the character chain uses), Blender for the game version, and
for **Best quality pictures** Z-Image Turbo and Qwen-Image-Edit 2511 ([README](../README.md), Models). `/api/health` says what is missing (`items`); the tab itself does not show it.

## Limits

- **Thin or clear things** (glasses frames, rings, a bottle's glass) are the worst case for an image-to-3D generator: a thin frame comes
  out as an open shell or a solid lens. They need a procedural maker. Glasses have one now ([glasses.md](glasses.md)); rings, a monocle's chain and
  goggles with a strap do not yet, and a bottle of glass is still drawn.
- **The drawn Garments are objects, not fitted clothes.** They are drawn on an invisible mannequin and come out as closed shells; nothing fits one onto a character's body or skins it to a
  skeleton. The Body Designer's six garments, a sleeveless shirt, fitted trousers, a knee-length skirt, a sleeveless knee-length dress, an open jacket and a bib apron, are the **Fitted clothes** family and are fitted to a designed body (the Body Designer add-on); a coat,
  or a dress with sleeves or to the floor, has no fitted version yet.
- The four views are drawn by an edit model, so a detail can differ between them (a buckle on one side only); redraw the views (**Redraw the views**), or the
  shape (**Redo the shape**).
- A symmetrical thing has no front: the kinds that have one say so in the prompt (the toe end, the face opening), a custom one does
  not, so *Other* things may come out turned.
- Where no picture sees (the inside of a helmet, the sole of a boot) the paint takes the colour of the painted surface around it (the average within a few centimetres,
  else the nearest): a Trellis2 shape has no colour of its own to fall back on.
