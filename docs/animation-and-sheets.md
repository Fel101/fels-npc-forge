# Animation, export and model sheets

## Animation and export

```bash
python -m npcgen generate --style pixel --name "Maya Okafor" --gender female --age 34 --occupation barista \
    --portrait --sprite --animations walk --dirs down left right up --frames 4
python -m npcgen export maya --clip walk --preset grid4 --out out/hero        # sheet + JSON + preview.html
python -m npcgen export maya --clip walk --preset godot --frame 48 --dirs right --res-path res://npcs --out out/godot
python -m npcgen export maya --clip walk --preset unity --out out/unity
python -m npcgen export maya --clip walk --preset aseprite --out out/aseprite
```

| Preset | Output |
|---|---|
| `atlas` | One PNG grid (rows = directions), a JSON atlas and a `preview.html` that plays every animation |
| `grid4` | The common 4x4 hero sheet: rows down / left / right / up, four 256px frames each |
| `godot` | A PNG strip per animation and a Godot 4 `SpriteFrames` `.tres` (AtlasTexture regions) |
| `unity` | A PNG grid and Unity-friendly JSON sprite rectangles, pivots, animation order, timing and provenance |
| `aseprite` | A PNG grid and Aseprite JSON hash frames, direction tags and frame durations |

Small pixel-art frames are snapped to **one palette shared by the whole clip**, so colours never shimmer between frames.
Every export carries a provenance block (generator, model, seeds). `python -m npcgen reassemble <character>` redoes alignment and the
report from the stored raw frames without touching the GPU.

Each clip gets a quality report: how much the legs move, how stable the head is, how well the palette holds. Directions
that fail are flagged, not hidden.

## Model sheets

A turnaround for modelling from, or for feeding an image-to-3D tool: five panels on white (portrait, front, side facing right,
side facing left, back) at one height on one baseline, plus the stitched sheet.

```bash
python -m npcgen generate --setting western --style photoreal --name "Mabel Whitaker" --gender female --age 46 \
    --occupation storekeeper --portrait --sprite --sheet                       # all five views
python -m npcgen generate --ids mabel-whitaker-ab12cd --sheet back --pose relaxed    # redo only the back view
python -m npcgen export-sheet mabel --out out/mabel                           # each view as a PNG, the sheet, a JSON record
```

In the web app tick **Model sheet**, or use the card on a character; every view has its own refresh button, because the back
of the hair and the feet are where a view most often goes wrong. The default pose is an **A-pose** (arms clear of the body,
what 3D artists ask for); "Relaxed" keeps the arms at the sides. The sheet is drawn from the sprite, which is generated
first if the character has none. It works in every art style; **Photorealistic** is the one made for it.
