# Files and layout

## Files it writes

```
library/<name-id>/
  npc.json                 data sheet, custom data, and the prompt and seed of every image
  portrait.png  sprite.png  (+ *_raw.png, the originals before the backdrop was cut out)
  model_3d.glb  model_3d_preview.png
  model_3d_original.glb    the 3D model exactly as the generator made it (or as imported), kept when it is cleaned up
  model_3d_head/           the upscaled head close-ups a sharper face was painted from
  model_3d_rigged.glb  model_3d_rigged.png   the skeleton, skin weights and animation clips (optional)
  head_3d.glb  head_3d.png  head_3d_original.glb   a detailed head, its render and the generator's own model (optional)
  head_3d/                 its four close-ups: front.png side_left.png back.png side_right.png; framed/ holds what Pixal3D saw
  expressions/happy.png ...
  animations/views/        the canonical side and back views
  animations/walk/         raw frames, and aligned frames <direction>_<n>.png
  sheet/                   portrait.png front.png side_right.png side_left.png back.png, sheet.png; raw/ holds the originals

library/.npcforge-items/<name-id>/     one folder per item (the Items tab)
  item.json                what it is and how each part was made
  hero.png  views/front.png side_left.png back.png side_right.png
  model_3d.glb  model_3d_original.glb  model_3d_preview.png   the game model scaled to its size; the generator's own shape
```

## Layout

```
server.py              web server (standard library)        web/    the UI (plain JS, no build step)
npcgen/roll.py         weighted rolling, locks, freeform     npcgen/cast.py      cast file import
npcgen/prompts.py      every prompt                          npcgen/anim.py      animation: measure, align, generate
npcgen/comfy.py        ComfyUI client and graph builders     npcgen/export.py    engine presets
npcgen/keying.py       backdrop removal                      npcgen/jobs.py      the one-GPU job worker
npcgen/sheet.py        model sheets: prompts, alignment      npcgen/head3d.py    the detailed head: close-ups, framing
npcgen/headgraft.py    the head's face laid over the body's    npcgen/items.py     items: kinds, prompts, steps, scaling
npcgen/itemstore.py    items on disk                         npcgen/itemmaker.py the steps of an item's job
npcgen/glasses.py      glasses built from measurements        npcgen/procmesh.py  sweeps, lens slabs and a GLB writer
npcgen/itemposter.py   the picture of a built item (Blender)  data/glasses.json   the glasses styles, swatches and ranges
npcgen/facescan.py     a character's face, found once (SDPose) npcgen/facefit.py   glasses fitted to a face (numpy only)
npcgen/glbmerge.py     a pair put under a character's head   npcgen/bodymaker.py a designed body as a character's 3D model
npcgen/bodymatch.py    a sheet's words as a body's looks     npcgen/underwear.py plain underwear on a designed body
npcgen/outfit.py       a sheet's outfit as fitted garments   data/outfit.json    the garment words and colours
npcgen/clothing.py     the Items tab's fitted clothes        data/clothing.json  their swatches and words
npcgen/dressing.py     a garment fitted to a body, kept      npcgen/glbprune.py  a garment taken out of a GLB
npcgen/marks.py        a sheet's marks as skin marks         data/marks.json     the mark and place words
npcgen/cli.py          python -m npcgen                      tests/              pytest suite
```

The release kit (CONTRIBUTING.md, "Making a release"):

```
installer/FelsNPCForge.iss         Setup.exe (Inno Setup)    scripts/build_installer.py   Setup.exe, the release ZIP, SHA256SUMS
scripts/setup_core.py              .venv and its packages    scripts/get_python.ps1       NPC Forge's own Python (data/python_runtime.json)
scripts/update.py                  update.bat, --undo, Setup's --after-setup             scripts/uninstall_forge.py   what uninstalling removes
scripts/release_notes.py           a release's notes         scripts/check_release_files.py   a draft's files, before publishing
scripts/check_setup_update.ps1     Setup.exe over the release before it                  scripts/make_icon.py         web/icon.ico from web/icon.svg
```

Setup.exe and `setup.bat` add `python\` (NPC Forge's own Python, when it has one) and `.venv\`; a build writes to `dist\`. Updates
never touch either.

```bash
python -m pytest        # automated suite; GPU visual review is documented separately
```

The checks to run before a change, and how the code is laid out in more detail: [CONTRIBUTING.md](../CONTRIBUTING.md).
