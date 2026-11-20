# Equip: a held item or a hat on a character

A sword in the right hand, a shield on the left forearm, a staff, a torch or a bow in a hand, a helmet or a hat on the head. Open a saved item in the
[Items tab](items.md) (it needs its 3D model), pick a character that has a skeleton under **Put it on a character**, and NPC Forge puts the item where it belongs, shows the
two together in 3D, and gives you sliders to nudge it. The character is not changed and nothing is "worn for good" (an outfit is not either): the result is a preview and
a download, as an outfit tried on is. What is kept, if you press **Keep this**, is only the slot and the nudge, for that item on that character.

## What can be equipped

| Kind | Goes | How |
|---|---|---|
| sword, cutlass, dagger, axe, hammer or mace | right hand (or the left) | gripped across the palm, the blade pointing forward and 35 degrees up |
| spear, staff, torch | right hand (or the left) | gripped, standing up, leaning a little forward |
| bow | left hand | held upright by its middle, its front facing forward |
| shield | left forearm | standing up and facing forward, its back face just beyond the hand (measured from how far forward the hand's vertices reach), as if the hand held it from behind |
| helmet | head | the dome on the skull, sized to the head |
| hat, cap, crown | head | the band on the forehead, the crown of the hat above the head, sized to the head |

Not done yet: a crossbow, a pistol and a rifle (they are drawn from the side and the muzzle could be at either end of the model: the program cannot tell which), a lantern, a bottle,
a book and a sack (each needs a grip of its own), gauntlets and all the armour pieces, boots, shoes and bags (they need fitting to a body), and glasses and fitted clothes (they have their
own panels). A kind you add to `data/items.json` works if it is held by a grip (`attach` of a hand, `pivot` of `grip`, `pose` of `upright`) or is worn on the head (`attach` of `Head`,
`pivot` of `base`).

## Where it goes, and how that is worked out

Everything comes from the character's **rest pose**: no GPU, no Blender, about a tenth of a second for a 50 MB file (and once per file: it is remembered while the file stays the
same, so moving a slider answers in about ten milliseconds).

- **Which way it faces** is read from where its hands are (a rig built the other way round is not put on wrong). Characters stand on +Y and face +Z, their right hand on -X.
- **The head** is measured from the skin weights: the vertices that the head joint carries give its top, its height and its width at the widest of the upper slices.
- **The palms** are the middle of the vertices each hand joint carries (a hand with finger bones keeps its palm on the hand joint). If too few vertices belong to a head or a hand, the
  place is guessed from the joints and the panel says so.
- **Hats are sized to the head, held items are not.** A sword is a metre long whoever holds it. A helmet or hat is scaled so that the part of it that goes round a head (measured from its own
  geometry: the middle of its widths at four heights, so a hat's brim does not count) is 5 to 15 per cent wider than the head. A helmet's dome is put on the crown with its
  guards hanging as far as they hang; a hat's band is put 27 per cent of the head's height below the crown.
- **Held items go through the palm** with the item's origin (its grip, which the Items tab puts at the height of the handle) on the palm. The hand is open in the rest pose, so the
  grip passes through it; the animation clips' hand poses are the engine's business.

Every placement has a **nudge**: up, forward, side (millimetres), turn (about the item's own long axis, or about the vertical for a hat) and tilt (degrees), and size (a multiplier).
The page shows them as sliders; the API takes them in metres (`up`, `forward`, `side`, 0.2 at most either way), degrees (`turn`, `tilt`) and a multiplier (`size`, 0.4 to 2.5).

## What you get

- **The character holding it, as a GLB** (`GET /api/items/<id>/worn/<character>.glb`, or the page's *Download the character with it*): the character's own file with the item put
  inside it under the bone that carries it (`hand_r`, `hand_l` or `head`), so it follows every animation clip. The character's skeleton, skin, animations and nodes are not touched.
  A whole loadout comes as one file: `GET /api/npcs/<character>/equipped.glb?items=<sword id>,<shield id>:hand_l,<hat id>` (an item's id, then `:slot` to choose; one thing in a slot).
- **The attach data**, for a game that would rather attach the item itself: the `X-Equip` header of that download, the API's answer below, or the page's *Download the attach data*. It is
  the bone's name and the transform of the **item's origin in that bone's own space** (translation in metres, rotation as a quaternion x y z w, scale), so the item model itself (its
  `.glb` from the Items tab, with its origin at the grip or the base) is hung from the bone at exactly that transform.
- **In Godot 4.7.2** (checked) the merged file imports with each item under a `BoneAttachment3D` for its bone (`Skeleton3D > hand_r:BoneAttachment3D > item_fit > sword`), and the
  item moves with the bone while a clip plays (the Walk clip, 37 per cent in: the sword and shield moved 0.3 and 0.22 m, the hat on the head 4 cm). In Blender and in three.js the
  item is a child of the bone. Unreal and engines that drop nodes parented to a bone want the attach data instead.

## The API

| Request | Answer |
|---|---|
| `POST /api/items/<id>/equip` `{npc, slot?, nudge?}` | where it goes, without building a file: `{slot, bone, attach: {bone, translation, rotation, scale}, scale, nudge, saved, notes, model}`. Slot and nudge default to what is kept for that character, else the kind's usual slot and no nudge. |
| `PUT /api/items/<id>/equips/<character>` `{slot, nudge}` | keep that slot and nudge for that character; returns the item (`equips`) |
| `DELETE /api/items/<id>/equips/<character>` | forget it (404 when nothing is kept) |
| `GET /api/items/<id>/worn/<character>.glb?slot=&up=&forward=&side=&turn=&tilt=&size=` | the character holding the item, `X-Equip` header with the bone and transform |
| `GET /api/npcs/<character>/equipped.glb?items=<id>[:slot],...` | the character holding several items, each at what is kept for it |

`/api/config` says which kinds can be equipped (`items.families[].kinds[].equip`: `{carry, slot, slots}`, or null). A character without a skeleton answers 400 ("has no skeleton: make its
skeleton first"), the loadout route 409; two items for one slot, a slot the kind cannot go in and a nudge out of range are 400 with the reason. From the command line:

```
python -m npcgen item equip <character> <item>[=hand_l] [<item> ...] [--up M --forward M --side M --turn DEG --tilt DEG --size X] [--save] [--out DIR]
```

It prints the bone and the transform of each item and, with `--out`, writes `<character>-equipped.glb`.

## What was checked

- Three rigged characters (a designed body, and two made from pictures, one with a big head and one with a small one), a cutlass and a helmet made by the Items tab: a cutlass in the right hand
  pointing forward and up on all three, a helmet on the head and sized to it on all three; seen in Blender's renders, in the rest pose and in a clip's crouching frame (the items follow).
  A helmet made without the high-angle views has no top, and the scalp shows through it: that is the model, not the placement.
- The page, in a browser, against a scratch server: pick a character, the item appears at the bone, the sliders move it (about a tenth of a second), *Keep this* keeps it and it comes back
  when the page is reloaded and the character picked again.
- Godot 4.7.2, above. `tests/test_equip.py` and `tests/test_equip_items.py` (made-up skinned characters that can face either way, and plain procedural items).

## Limits

- It is the **rest pose** only: nothing knows about a clip's own hand poses, and an item can go through a hand, a leg or the shield through the thigh. Games hide that with the
  hand pose and the clip, and the nudge is there for the rest.
- A **big shield** hangs in front of the hip in the rest pose, because the arm hangs beside the body.
- **Hats** are sized by the width of their crown or dome; a hat with a tall pointed crown, a helmet with a big crest and a thing made from a picture that has no top will need a nudge.
- A **held item's grip** is where the Items tab put its origin (a fifth of the way up a sword, say): a model whose handle is somewhere else needs a nudge, or its `grip_at` changed in the
  table.
- The **hands of a stylised character** can be much bigger or smaller than the grip of a model of real size; use *Size*.
