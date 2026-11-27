# Models: which engine draws and which one edits

The **Models** card picks two engines (`npcgen/engines.py`). **Image** draws from text: the portrait, and the full-body sprite
when it is drawn independently. **Edit** changes a picture: the portrait from your photo, the matched sprite, expressions, the
model sheet and every animation frame (up to three reference pictures at once).

| Image | Edit | Notes, measured on a 32 GB card |
|---|---|---|
| Mage-Flow (built in) | Mage-Flow-Edit (built in) | What every prompt, animation check and sheet was tuned on; light on memory. Its licence is not cleared (below), so it is not the default. |
| Z-Image Turbo | | About 16 s, natural varied faces, but pulls hard toward famous characters (below). |
| Qwen Image 2.1 | Qwen Image 2.1 Edit | About 30 s a portrait; the edit is slow (about 100 s a picture). **Non-commercial licence** (NOTICE.md). |
| Qwen-Image 2512 + Lightning (default) | Qwen-Image-Edit 2511 + Lightning (default) | About 30 s; the edit gives real whole bodies and natural poses (in a walk it keeps the character identical but moves the legs little: check the clip). Apache-2.0. |

The big engines are about 20 GB plus an 8-9 GB text encoder, and left loaded they once froze this PC. So they run the careful
way: ComfyUI unloads everything whenever the weights change, a big picture waits for enough free RAM and VRAM before it starts,
the same engine stays loaded between pictures only while 16 GB of RAM stay free, a watchdog stops a picture that runs the PC
short of memory or stalls, and the models are unloaded when the queue is empty. A picture a big engine cannot make fails, and the job says why
(try it again when the GPU is free); with **Let Mage-Flow draw when the chosen model cannot** switched on (`engine_fallback`, off by default) it is drawn
with Mage-Flow instead, and the job and the picture say so.

They run at cfg 1, so **they ignore negative prompts**: only the positive wording steers them. Two things that showed on
Z-Image and are fixed in the tables: a superhero's "hero's costume with a cape and a chest symbol" came out as Superman's
costume (the outfits now name their own colours and a plain chest), and chibi's "two heads tall" drew a second head.
One is not fixed, because it is Z-Image's own: a long-haired pirate captain (a woman, in the test) came out as Jack Sparrow in
17 of 17 draws, whatever the wording; Mage-Flow and both Qwen engines drew her right. Its note in the Models card says so.
The CLI takes `--image-model`, `--edit-model` and `--engine-fallback` / `--no-engine-fallback`; what they are when not given comes from `config.json`'s `options`, else the Qwen pair with no fallback.

**May you sell what a model makes?** Every model has its own licence, and the pickers say so: a tag after each name (`Z-Image Turbo ·
commercial OK`, `Qwen Image 2.1 · non-commercial`), a note under the chosen model with its licence and a **Details** button, and the
**Licences of every model** button (Models and options, About) with each licence's summary, links and the date it was read. Every
character carries a badge for the worst model that made anything of it, and the Library can filter by it. In short: Z-Image Turbo,
Qwen-Image 2512 and Qwen-Image-Edit 2511 are Apache-2.0 (fine for commercial work); Qwen Image 2.1 and Breeze TTS 2 are
non-commercial; the built-in Mage-Flow is **not cleared** (MIT, but Microsoft says research only); Hunyuan3D, Pixal3D and Trellis2
carry conditions. [licences.md](licences.md) has the list, how ratings are worked out, the `config.json` setting that makes
the Apache-2.0 pair the default (it is the default now), and how to keep the list current. It is what each model's page says, not legal advice.

## One model on the GPU at a time

**One model at a time** is a switch on the Models card (Models & options) and in the
header GPU light's popup, on by default. Before NPC Forge makes a picture, it waits
for Voice Lab and Altera to finish their work and release the GPU. While an app is
busy the queue shows what it is doing, and you can cancel. If you also turn on
**Allow unloading LM Studio and Ollama models**, Forge takes the GPU lock alone
before requesting those unloads. The permission starts off and is saved as
`gpu.unload_external_models`. Forge checks ComfyUI and Windows' per-process memory
before loading the model. The first time Forge unloads
another app's model, the job says so. Only pictures are affected: Voice Lab and a
chat in Altera share the GPU either way. The header GPU light shows who holds the
lock ("NPC Forge, alone: pictures").

Turn it off if your card has room for everything at once; the Models card's **Your
PC** box says whether yours is likely to need it ([hardware.md](hardware.md)).
The switch is saved as `gpu.one_model_at_a_time` in `config.json`, and a change
applies from the next job. With it off, if a picture needs more memory while Voice Lab is using it, the queue offers
**Ask Voice Lab to free the GPU**. Forge frees its ComfyUI models after the queue
has been idle for `gpu.unload_idle_seconds` (120 by default) or before it sends
speech to Voice Lab.
