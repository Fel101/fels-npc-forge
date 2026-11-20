# Pixal3D face texture detail

NPC Forge keeps Pixal3D's four full-body views for shape and global appearance. By default, all close-up texture views come from same-scale crops of the turnaround images. The optional high-resolution portrait face source replaces only the front close-up; left, back, and right still use turnaround crops. The close-up projections replace features only inside the head cameras' visible frusta. Crop masks use the existing local BiRefNet model. No remote service is involved.

Pixal3D has separate Low, Medium, and High detail presets in the Models panel. They change shape sampling, mesh density, and baked map resolution. Medium keeps the current 1536 shape upsample and 2K textures; recent runs on this PC took about 14 and 26 minutes. The displayed runtimes are estimates, and High can exceed an hour and use enough VRAM to fail on an out-of-memory condition.

The high-resolution portrait face source is experimental. Clara's side-by-side trial finished successfully in 25 minutes, but its face texture shifted warmer and lost some of the green complexion and cheek detail. The turnaround crop remains the recommended default; compare individual characters before choosing the portrait option.

The local ComfyUI extension is tracked at `npcgen/comfy_extensions/npcforge_pixal_head_texture.py`. Install or refresh it into the ComfyUI you run (the folder that contains `custom_nodes`; the path below is an example) with:

```powershell
python scripts/install_pixal_head_node.py "C:\path\to\ComfyUI"
```

Restart ComfyUI once after installing the extension. NPC Forge reports the Pixal3D generator unavailable until ComfyUI exposes `NPCForgePixal3DHeadTextureRefine` through `/object_info`.

The same script installs NPC Forge's other ComfyUI node, for the Trellis2 four-picture generator, when given `--trellis2` (see [model-import.md](model-import.md)).

## Memory

The node reads only the texture stage's two 1024-pixel feature maps (DINOv3 patches and their NAF upsampling), one close-up at a time on the GPU (about 2 GiB of VRAM, no host memory). An earlier version asked ComfyUI for the whole conditioning of the four close-ups: three NAF maps of 1024 channels for four views in host memory, about 12 GiB, on top of the full-body conditioning that is still alive (also about 12 GiB). On a 64 GB PC that took Windows' commit charge from 50 to 65.5 of 68.3 GiB and the first full-body run on the AMD Trellis2 instance had to be stopped. With the lean node the peak was about 52 GiB. A ComfyUI whose helper functions differ gets the old method automatically (the result is the same, the memory is not).

## Measured on the AMD Trellis2 ComfyUI instance (2026-10-01)

ComfyUI 0.37.0, PyTorch 2.13 with ROCm 7.15, Radeon AI PRO R9700 (32 GB), one character, the same seed, through the job code:

| | Medium | High |
|---|---|---|
| Time | 22.2 min (26.5 min on an older instance) | 30.4 min |
| Generated model | 697,098 faces, 40 MB, three 2048 maps | 981,821 faces, 79 MB, three 4096 maps |
| Pieces after welding vertices | 407 (402 under 0.1% of the faces) | 1,158 (1,147) |
| Peak memory commit of 68.3 GiB | about 52 | about 56 |

High's shape is finer (eyelids, fingers, less lumpy hair at the back) but its generated texture was worse on this character (a green tint on the hair, a pale patch on the coat's back, dark blotches), so it only pays off when the model is repainted from the pictures (the clean-up step does that). The log reaches the UV unwrap after about 13 minutes at Medium and 20 at High. A large share of the run is the NAF conditioning (the first pass alone took about 10 minutes at Medium), which runs on this instance's pure PyTorch neighbourhood-attention fallback (its README says a native NATTEN build may be faster).
