# What your PC can make

NPC Forge works without a graphics card: characters, their personalities and backstories, dialogue packs, cast packs and
scene scripts need none, and neither does the MCP server for AI agents. Pictures, animation and 3D models are drawn by
models in [ComfyUI](https://github.com/comfyanonymous/ComfyUI), and those need a graphics card.

The **Your PC** box at the top of the Models card (Models & options) reads the card ComfyUI draws on, says which of the
tiers below it is, lists what it makes, and offers the best models for it. A new page starts with them; you can still pick
any model. A request through the API or MCP that names no model gets them too (`config.json`'s `options` still win).

## The tiers

| Card | Makes | Slowly | Starts with |
|---|---|---|---|
| None, or no ComfyUI | Characters with their personalities and backstories, dialogue packs (JSON, Ink, Yarn Spinner), cast packs, scene scripts, and the MCP server for AI agents | | |
| About 16 GB | All of that, portraits with Z-Image Turbo, full-body sprites, and 3D models with Hunyuan3D 2.1 | Expressions with Qwen-Image-Edit 2511 (about 7 times as long) | Z-Image Turbo and Qwen-Image-Edit 2511 |
| About 24 GB | The same, and portraits with Qwen-Image 2512 | Expressions with Qwen-Image-Edit 2511 (about 6.5 times as long) | Qwen-Image 2512 and Qwen-Image-Edit 2511 |
| 32 GB | Everything: model sheets, walk cycles and other animation, and 3D models with Trellis2 and Pixal3D too | | Qwen-Image 2512 and Qwen-Image-Edit 2511 |

Not on 24 or 16 GB: model sheets and walk cycles (at 24 GB a picture crawled past NPC Forge's 420 s limit, which stops
it), and so neither Trellis2 nor Pixal3D, which build a 3D model from the model sheet's four views. Not on 16 GB:
Qwen-Image 2512 portraits, which came out black. The built-in Mage-Flow (its licence is not cleared) ran at its usual
speed on all three. On every tier the pictures need the whole card to run at their usual speed, so the Your PC box
suggests keeping One model at a time on.

## One model at a time

The switch beside the Your PC box (and in the header GPU light's popup). On, before NPC Forge makes a picture it asks
Voice Lab, Altera, LM Studio and Ollama to unload their models, and they wait until it is done. Off, pictures share the
card with them. The Your PC box says whether your card is likely to need it: it compares what a picture needs (the room of
the smallest card it ran on at its usual speed) plus about 8 GB for a small voice model and a chat model with the card. See
the README, "One model on the GPU at a time".

## The measurements

Measured on 2026-11-25 on an AMD Radeon AI PRO R9700 (32 GB) under Windows 11, with ComfyUI 0.37.0 and PyTorch 2.13 for
ROCm, in a PC with 64 GB of RAM.

| What | 32 GB card | 24 GB card | 16 GB card |
|---|---|---|---|
| Portrait, Mage-Flow | 24 s, 12.7 GB, RAM 4.9 GB | not run | 20 s, 12.9 GB, RAM 7.7 GB |
| Portrait, Z-Image Turbo | 22 s, 28.5 GB, RAM 9.1 GB | 32 s, 21.4 GB + 2.5 GB shared, RAM 11.7 GB | 40 s, 15.6 GB + 3.5 GB shared, RAM 12.1 GB |
| Portrait, Qwen-Image 2512 + Lightning | 26 s, 30.8 GB, RAM 8.0 GB | 42 s, 22.3 GB + 4.7 GB shared, RAM 10.6 GB | not run |
| Four expressions, Mage-Flow-Edit | 159 s, 15.3 GB, RAM 5.1 GB | not run | 163 s, 15.5 GB, RAM 7.7 GB |
| Four expressions, Qwen-Image-Edit 2511 + Lightning | 108 s, 30.6 GB + 1.8 GB shared, RAM 7.9 GB | 703 s (steps: 43, 261, 139, 259 s), 22.3 GB + 4.5 GB shared, RAM 10.5 GB | 753 s (steps: 48, 284, 149, 270 s), 16.0 GB + 4.7 GB shared, RAM 10.8 GB |
| Full-body sprite from the portrait, Qwen-Image-Edit 2511 | 30 s, 28.9 GB + 3.1 GB shared, RAM 5.6 GB | 38 s, 22.4 GB + 4.9 GB shared, RAM 8.2 GB | 58 s, 15.8 GB + 4.6 GB shared, RAM 8.6 GB |
| Model sheet, four views, Qwen-Image-Edit 2511 | 102 s, 29.4 GB + 2.4 GB shared, RAM 4.7 GB | stopped after 833 s: a picture took more than Forge's 420 s limit (steps: 34, 426, 36, 34, 301, 4 s) | not run |
| Walk cycle, four frames facing the camera, Qwen-Image-Edit 2511 | 135 s, 30.3 GB + 1.9 GB shared, RAM 5.7 GB | stopped after 528 s: a picture took more than Forge's 420 s limit (steps: 527, 0 s) | not run |
| 3D model, Hunyuan3D 2.1 | 48 s, 8.2 GB, RAM 9.2 GB | not run | not run |
| 3D model, Trellis2 | 229 s, 16.6 GB, RAM 18.2 GB | not run | stopped after 733 s: ComfyUI failed in node 73 (Trellis2PreProcessImage): zero-size array to reduction operation minimum which has no identity (steps: 38, 225, 240, 225, 4 s) |
| 3D model, Pixal3D (medium) | 1320 s, 10.8 GB, RAM 21.0 GB | not run | not run |

- **Not run:** that pipeline was not run at that size. The tiers then go by its peak on the full card, and a 3D model also by
  the pictures it is made from.
- **Qwen-Image 2512 at 16 GB** is left out. On all three tries Windows lent it part of the helper's share (up to 3 GB, for a
  quarter to two thirds of the run), and all three pictures were black; NPC Forge's new check refused the third.
- **Trellis2 at 16 GB** stopped because the model sheet's front view, made first, came out black, so it had nothing to work
  from.
- **The 16 GB sprite** was made from a portrait that had come out black. The work is the same, so its time and memory
  stand.
- Of the four Qwen-Image-Edit expressions at 16 GB, the sad face came out with a strong colour cast.

Each cell is one run: how long the job took, the most graphics memory ComfyUI's process held, and the most RAM it used
(private bytes). "Shared" is memory ComfyUI used through the card from the PC's own memory, shown when over 1 GB: an edit
takes 2 to 3 GB of it even on the full card, and a smaller card more, as Windows moves what does not fit there. That is
slower, and the times say how much. On the full card ComfyUI keeps whatever it may, so a peak there is what it took, not
what it needs: the smaller cards say what it needs.

**How.** `scripts/measure_gpu.py` queues each job through a Forge with a library of its own, starting from an empty card,
and reads ComfyUI's process every half second from Windows' per-process counters, as NPC Forge's own GPU check does. A
smaller card is the same card with a helper process holding the rest of its memory, less 1 GB for the desktop and the
driver. Windows may lend ComfyUI some of the helper's share for a moment; a run counts only when the helper kept at least
90% of it for nine tenths of the run; a run left out says nothing either way. One character (a fantasy
character, semi-realistic style) at Standard quality; a 3D model includes the pictures it is made from when they were not
there yet. Every picture was looked at afterwards, not only the time.

**What the smaller cards mean.** On Windows with ROCm, ComfyUI counts only its own use of the card, so it does not see
what the helper holds: it loads as if the whole card were free, and Windows moves what does not fit into the PC's own
memory, which is slower. That is exactly what happens on this PC when Voice Lab or a chat model hold part of the card
beside the pictures (One model at a time off), so the columns say how pictures fare beside them. A real card of that size
is likely to do better: ComfyUI knows how big it is and loads less. So the tiers are cautious: what a tier makes has been
seen to work.

**Black pictures.** With the card made to look like 16 GB, Qwen-Image 2512 and Qwen-Image-Edit 2511 sometimes finished
without an error and returned a black picture. NPC Forge now refuses a picture of one flat colour from the big engines:
the step fails, says the GPU ran short of memory, and frees ComfyUI's models (`npcgen/engines.py`). Z-Image Turbo's own
page says it fits comfortably on 16 GB consumer cards.

```
python scripts/measure_gpu.py --forge http://127.0.0.1:8775                       # the full card
python scripts/measure_gpu.py --forge http://127.0.0.1:8775 --card-gb 16 --ballast-python <a Python with PyTorch>
python scripts/measure_gpu.py --table local/hardware data/hardware.json           # the table the Your PC box reads
```

Run the Forge it measures with `NPCGEN_MEASURE=1` (it skips the start check whose figures are being measured) and with
One model at a time off.

**What a tier makes.** A pipeline belongs to a tier when it ran on a card of that size; one that was not run at a size
belongs to it when its peak on the full card fits. One that finished but took more than three times as long as on the full
card is listed as slow, with how many times as long. A tier starts with the best engines that run on it at their usual
speed, or slowly when none does. The big engines' start check (enough free memory before a picture starts) uses the
smallest card each finished on, less a little, and the RAM it used there plus 4 GB (`npcgen/hardware.py`).
