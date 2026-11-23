# Using NPC Forge from a game's own scripts

For a project that queues characters in bulk through the API (one game made 164 this way) rather than through the page. It says what to set once, what the queue now tells
you, what happens when something goes wrong, and what NPC Forge does not do. The API calls themselves are the ones the page makes: [api.md](api.md) lists every route, and the page shows what each one sends.
A script needs no `Origin` header; one that sends an `Origin` naming another site is refused. Every route is listed in [api.md](api.md).
An AI agent (Claude Code, Codex, opencode...) can use the MCP server instead: [agents.md](agents.md).

## The engines a request gets, and how to change them once, in config.json

Since 2026-10-13 a request that names no engine is drawn by **Qwen-Image 2512** (text to image) and edited by **Qwen-Image-Edit 2511**, both Apache-2.0, with **no Mage-Flow fallback**. The built-in Mage-Flow is what every prompt was tuned on, and its README says **research use only**.
A PC that has not the Qwen files, or wants the lighter Mage-Flow back, names its engines once in `config.json` (a request that names none gets these; a request that names one still wins; `GET /api/config` → `defaults.options` says what the server will use, and the page follows it):

```json
{ "options": { "image_model": "mage", "edit_model": "mage", "engine_fallback": true } }
```

A client that names no engine, such as a companion app asking for one portrait with `{"options": {"quality": "standard"}}`, now gets Qwen: about 30 s, and it needs about 36 GB of free RAM and 29 GB of free VRAM to start, so with another model on the GPU it can fail where Mage-Flow used to draw. Such a client can name `"image_model": "mage", "engine_fallback": true` itself.

- **A misspelt or wrong engine is refused**, not replaced: `image_model: "qwen25122"` is a 400 that lists the engines there are (`mage`, `zimage`, `qwen21`, `qwen2512`), and so is naming an
  edit engine (`qwen2511`) as the image engine. It used to become Mage-Flow without a word.
- **`engine_fallback: false`** (the option, per request or in `config.json`; the default since 2026-10-13): a picture whose engine cannot draw it (not enough free RAM or VRAM, a model file missing) **fails the step
  with the reason** instead of Mage-Flow drawing it. A shortage of memory or a GPU that is not empty yet is tried again first (below). With `true`, which is what it did before, the
  picture is drawn by Mage-Flow and its record carries `engine_note` saying why. (The Items tab's own engines do not read this option: Items still fall back. The Scenes tab's stage backgrounds do, since 2026-10-14: they are drawn with the engine the page or request names, Qwen-Image 2512 by default, and fail the same way.)
- **To see what drew a picture**: its record (`assets.portrait`, `assets.sprite`, each `expressions` and sheet view) has `engine` (the label, e.g. `Qwen-Image 2512 + Lightning`;
  no `engine` means Mage-Flow) and `engine_note` when it fell back. The 164 Qwen portraits of that game's run have no `engine_note` at all.
- Which licences: [licences.md](licences.md) (every model, rated) and [NOTICE.md](../NOTICE.md). Qwen Image 2.1 is non-commercial; Qwen-Image 2512 and Qwen-Image-Edit 2511 are Apache-2.0.
  **To check what a game's characters may be used for**: every row of `GET /api/npcs` has `commercial` (`yes`, `conditions`, `unclear`, `no`, or `none` while nothing is made), and
  `GET /api/npcs/<id>/licences` lists the models behind one character, worst first. A run that kept `engine_fallback: false` (the default) and used only the Apache-2.0 engines for its pictures and
  Qwen3-TTS or Kokoro for its voices is rated `yes`; a 3D model from Trellis2 or Pixal3D is rated `conditions` (they read DINOv3).

## Portraits are head and shoulders unless you ask for more

- `options.portrait_framing`: omit for the default head-and-shoulders view, set `half` to crop at the waist, or use `full` for the full figure.
- **A description's stance sentences are left out of the portrait** (the sprite keeps them): a clause that begins with *standing*, *stands* or *posed*, or names an A-pose or T-pose, a whole body or full length,
  arms or hands held out, at the sides, crossed, on the hips, or "both hands empty and open". Hands *stained with ink* and a coat *standing open* are kept. For a head-and-shoulders or waist-up
  portrait what is worn below the waist (trousers, skirts, stockings and socks, breeches, waders, bare feet, every kind of shoe and boot, even one that is "missing") is left out as well, as it already
  was for a structured character's outfit: a portrait that names boots draws them at the bottom of the frame, and one that names a skirt or a missing shoe is drawn down to the hips (measured
  2026-10-13 on ten characters whose outfits named such things, pictures that show the hips with the older list and with the wider one: Mage-Flow 6 of 10 and 1 of 10, Qwen-Image 2512 1 and 0; wrists 6 and 2, 6 and 3).
  A gown *with a slit skirt* keeps the gown, a skirt *suit* stays (it is the jacket), and an outfit that is nothing but lower body is used whole. Text with nothing to leave out is used exactly as written.
  That game's descriptions ended with "standing in a relaxed A-pose with the arms held slightly away from the body, both hands
  empty and open", which drew all 164 portraits full length (and their expressions with them); with this change the same description gives a head-and-shoulders prompt.
- **A head-and-shoulders prompt also says where the frame ends**: *Framed from the top of the head to just below the collarbones; nothing below the chest is in the picture.* It comes after the
  look and before the backdrop (`half` and `full` are as they were). The opening words alone still drew elbows, wrists and now and then the hips. Measured 2026-10-13 on six characters with each
  picture engine (body points found with SDPose; six is a small sample, read it as a direction), the old prompt against the wider outfit filter plus this sentence, pictures that show an elbow, a wrist, the hips:
  Mage-Flow 4, 1, 1 of 6 became 2, 0, 0; Qwen-Image 2512 2, 1, 0 became 0, 1, 0; Qwen Image 2.1 5, 4, 4 became 3, 0, 1; Z-Image Turbo 4, 2, 2 became 2, 2, 1. Both shoulders stayed in every picture and the
  person was drawn a little larger (the face grew from 17-27% of the picture's height to 25-30%). It is **not a guarantee**: what a character holds or wears can still pull a hand into the frame.
- The pose belongs in the 3D request, not in the description: `assets.sheet = {"views": [...], "pose": "a_pose"}`.

## Change one picture without marking everything stale

`PUT /api/npcs/<id>` marks every picture and model made from the character stale, because a changed description changes what they were made from. Stale is a flag: the files are still there and still
usable, and making an asset again clears its flag. If you know an edit does not touch some of them, say so: `{... "keep_fresh": ["sprite", "sheet", "model_3d", "animations"]}` (the kinds are
`portrait`, `sprite`, `sheet`, `living`, `model_3d`, `rig_3d`, `head_3d`, `expressions`, `animation_views`, `animations`). Something already marked stale that is still right is cleared with
`POST /api/npcs/<id>/fresh` `{"kinds": ["sprite", "sheet"]}` (the files are not touched). To make one asset again, ask for just that: `POST /api/generate`
`{"npc_id": ..., "assets": {"portrait": true, "expressions": ["happy"]}, "options": {"reroll": true}}` queues those and nothing else, and the framing option
means the description does not have to be edited at all.

## What the queue tells you

`GET /api/jobs`:

- each **step** has `started_at` and `last_progress_at` (the last sign of life: ComfyUI answered, Blender was still running, the GPU was being emptied...), `seconds` when it ends, `attempts` when it was
  tried more than once, and `detail` while it waits for something (`waiting for ComfyUI: another job is using it`, `waiting for free memory`, `waiting for the GPU to be free; trying again (1 of 2)`);
- each **job** has `started_at` and `finished_at`.
- (API 44) a step that failed has a **`code`** beside its `error`, and so has a job that failed or was cancelled (its first failed step's code, or `cancelled`). The `error` texts are as they
  were, written for a person; read the code instead of matching words. A retried job loses its codes.

| `code` | Why | What to do |
|---|---|---|
| `gpu_busy` | Another program holds GPU memory on the card ComfyUI draws on. The step also has **`holders`** `[{"name": "llama-server.exe", "mb": 25190}]` (each over 1.5 GB) and **`device`** (that card's name). | Unload that program's model, or close it, then retry the job. |
| `gpu_comfy_held` | ComfyUI kept its memory after it was told to unload everything: a model loaded in ComfyUI by hand, or, before a voice step, ComfyUI's dynamic VRAM cache (below). `device` as above. | Unload it in ComfyUI or restart ComfyUI, then retry. |
| `comfy_busy` | ComfyUI stayed busy with a job that is not NPC Forge's, or another program held ComfyUI for 30 minutes. | Let it finish or stop it in ComfyUI, then retry. |
| `comfy_down` | ComfyUI could not be reached. | Start ComfyUI, then retry. |
| `no_model` | ComfyUI has not got a model file the step needs. | Install it, or name another engine. |
| `low_memory` | A big picture engine found too little free RAM or VRAM, after its tries. | Free some, or name a lighter engine. |
| `stalled` | No sign of life for 45 minutes: the step was stopped (or given up on, when it would not stop). | Retry; if it happens again, look at what ComfyUI or Blender is doing. |
| `cancelled` | (the job's code only) The job was cancelled by a person. A job that a failed step stopped is `error` and carries that step's code. | |
| `error` | Anything else: the `error` text says what. | |

A step that was **not run** because an earlier step stopped the job is `cancelled` with a **`reason`**: `not run: another program holds the GPU`, `not run: ComfyUI did not give its GPU memory back`,
`not run: ComfyUI was busy with something else`, `not run: ComfyUI could not be reached`, or `not run: an earlier step stopped the job`. "Assemble the living portrait" when no expression (or no portrait)
was made is `cancelled` with `reason` *no expression was made: (the first failed expression's error)*, instead of an error of its own.

What it does when something goes wrong:

| What | What happens |
|---|---|
| Another program holds ComfyUI | A step waits at most **30 minutes**, then fails naming the holder (*It is held by process 1234 (npc-worker in server.py) since 18:12:04.*), `comfy_busy`, and the job stops. |
| Another program holds GPU memory on ComfyUI's card (`gpu_busy`) | After the gate's 90 s the step fails **without trying again** and **the job stops**: the rest of its steps are not run (`reason` above). Before API 44 every step waited and failed the same way: ten expressions took an hour. |
| ComfyUI keeps its memory (`gpu_comfy_held`) or ComfyUI is busy (`comfy_busy`) | The step is **tried again twice, 30 seconds apart**; if it still fails, the job stops the same way. |
| A big engine finds too little RAM or VRAM (`low_memory`) | The step is **tried again twice, 30 seconds apart**, then fails with the reason, and the job goes on with its other steps. With `engine_fallback: false` this is what replaces a silent Mage-Flow picture. |
| ComfyUI cannot be reached (`comfy_down`) | The step fails and the job stops. |
| A step makes no sign of life for **45 minutes** | It is asked to stop and fails with *Nothing happened in this step for 45 minutes* (`stalled`). |
| A step is cancelled and does not stop | After **2 minutes** the job is given up on (cancelled) and a new worker takes the queue: it no longer takes a restart. |
| The server restarts | Queued jobs and **finished steps are kept** (`library/.npcforge-jobs.json` is written at every step); the step that was running starts again from its beginning. |

The numbers are `config.json` → `jobs` (`stall_minutes`, `cancel_grace_seconds`, `transient_retries`, `retry_pause_seconds`). A failed job is retried with `POST /api/jobs/<id>/retry`.
There is no event stream: ask `GET /api/jobs` as often as you like (it is the server's own list, nothing is computed), and a job's `version` changes whenever it does.

### Ask before you queue: `GET /api/gpu` (API 44)

The GPU as the next job will find it, read the way the gate reads it but **without unloading or waiting for anything** (it answers in about a second):

```json
{"ready": false, "code": "gpu_busy", "message": "llama-server.exe holds 24.6 GB of the AMD Radeon AI PRO R9700: unload its model or close it first (one model on the GPU at a time).",
 "device": "AMD Radeon AI PRO R9700", "integrated": false, "total_mb": 32624, "free_mb": 32085, "comfy_mb": 230,
 "forge_loaded": null, "holders": [{"name": "llama-server.exe", "mb": 25190}], "other_gpus": ["AMD Radeon 780M Graphics"], "warning": null, "one_model_at_a_time": true}
```

(an example: LM Studio's model server holding the card)

- `ready` with `code` `null`: a picture can start now. `ready` is also true while NPC Forge is busy with its own job (`message` says a new one waits its turn).
- `code` is `gpu_busy` (`holders` says who: a name and MB each, the biggest first), `gpu_comfy_held`, `comfy_busy` or `comfy_down`, as in the job list.
- `device` is the card ComfyUI draws on and `other_gpus` the PC's other cards. `free_mb` and `total_mb` are ComfyUI's own reading of that card: on an AMD card under Windows it has been seen not to
  count other programs (0.8 GB "in use" while a voice worker held 6.8 GB), which is why `holders` comes from Windows' own counters. `comfy_mb` is what ComfyUI's process holds there by those counters, `forge_loaded` what NPC Forge
  last loaded (`null`: nothing).
- `warning` (also added to `message`) when ComfyUI draws on a GPU built into the processor while the PC has a separate card: pictures are many times slower there (on this PC 174-228 s a portrait
  on the Radeon 780M against 35 s on the R9700). ComfyUI's `--cuda-device` start option picks the card.
- It is about pictures (ComfyUI). A voice step can still be refused where this says ready (below).

**Only the card ComfyUI draws on counts** (API 44). On a PC with two GPUs, a program on the other card is not in the way and is no longer named; before, a 24.6 GB model on either card refused every picture.

**About the memory that "ComfyUI still holds"**: ComfyUI 0.4x keeps the last model's weights in a *dynamic VRAM cache* after it is told to unload (its `comfy-aimdo` package; measured 2026-11-11 on the R9700:
4.6 GB, for as long as it was watched, while ComfyUI's own count said 0.5 GB). ComfyUI gives that memory to its own next model, so since API 44 a picture goes ahead on ComfyUI's own count;
before, every switch to another model was refused after 90 s (a portrait, then its expressions: all refused). A voice worker is another program and cannot use that memory, so a voice step after a picture is
still refused (`gpu_comfy_held`, the message says why): restart ComfyUI, or start it with `--disable-dynamic-vram`. A ComfyUI without dynamic VRAM still has to give its memory back
(down to 2 GB, `gpu.idle_vram_mb`) before another model starts, as it did in October (measured then: 0.6 GB straight after an unload, 2 MB fourteen seconds later).

## Roll a character from a description

`POST /api/roll` `{"setting": ..., "freeform": "a tall woman with grey eyes...", "locked": {"profile.first_name": "Maya"}}` rolls only the data sheet (name, age, gender) of a described character; the
picture is drawn from the description. Since API 44:

- **No surname**: `"locked": {"profile.last_name": ""}` keeps the last name empty (a full name locked as the first name, "Wren Ashby", no longer gets a rolled surname after it). Names are written
  without a trailing space.
- **The gender comes from the description** when its words give one (`woman`, `she`, `man`, `he`, `nonbinary`...: the first such word wins; hyphenated words such as *she-wolf* do not count) and none is
  locked. A locked `profile.gender` still wins; a description with no such word gets a rolled one, as before.
- **The age is not presented as fact**: the description does not give one, so it is an adult age at random unless locked (`profile.age`) or picked (`constraints.age_group`), and
  `profile.age_source` says which: `rolled`, `chosen` or `locked`. A described character is never given a child's or a teen's age at random. An age changed by hand (the page, or `PUT`) drops
  `age_source`.
- **`"adult": true`** asks for a grown adult: no child or teen age is rolled, picked or locked (a 400 says so), the character keeps `"adult": true`, a later edit cannot make it younger than 18,
  and a described character's picture prompt starts *a grown adult woman*, *a grown adult man* or *a grown adult* (from the gender), so a client does not need to add those words itself.

## Tag what you make, and the trash

- Give each character `custom: {"project": "ember-hollow"}` (and `"test": true` for a scratch one) when you roll or create it: `GET /api/npcs?project=ember-hollow&test=0` lists one project's
  characters without the tests (`test=1` keeps only the tests). Without a filter the list is as it was.
- **Deleting a character moves it to a trash folder** (`library/.trash`) instead of removing it: `GET /api/trash` lists what was deleted, `POST /api/trash/<id>/restore` brings it back whole (its
  pictures and models too; cast-pack membership is not restored). It is removed for good after 30 days (`config.json` → `library.trash_days`; 0 keeps everything). Each row carries `folder`, `thumb`
  (whether it has a small picture: `GET /api/trash/<folder>/thumb.png`), `age`, `occupation`, `setting`, `style` and `has` (what it had). `DELETE /api/trash/<folder>` removes one for good and
  `POST /api/trash/empty` removes them all (the app's Trash view uses these).
- **A picture that is drawn again keeps the one it replaces** (the last six per picture, `library.picture_history`; 0 keeps none): `npc.json` → `assets.candidates["portrait" | "sprite" | "expressions/<id>" | "sheet/<view>"]`
  lists them (`id`, `file`, `thumb`, `seed`, `created`, `replaced`). `POST /api/npcs/<id>/assets/<picture>/candidates/<candidate id>/select` brings one back (the picture in use is kept in its place) and
  `DELETE` on the same path removes one for good; both answer 409 while a job for the character is queued or running. Game exports leave them out.
- **A 3D model that is replaced or deleted is kept too** (the last two per character, `library.model_history`; 0 keeps none), with its skeleton and the files it owned (`model_3d*` in the character's folder):
  `assets.model_history` lists them (`id`, `replaced`, `model`, `rig`, `names`, `bytes`) and `model_history/<id>/` holds the files. `POST /api/npcs/<id>/model-history/<id>/restore` brings one back (the model in
  use is kept in its place) and `DELETE /api/npcs/<id>/model-history/<id>` removes one for good; both answer 409 while a job for the character is queued or running. A `.glb` can be made a character's model
  exactly as it is with `POST /api/npcs/<id>/model-upload?name=<file name>`: the bytes of the file as the body, content type `application/x-npcforge-glb`, up to 320 MB (it answers the character, plus `upload`:
  `faces`, `textured`, `warnings`; 400 with the reason for a file that cannot be used). See [model-import.md](model-import.md).

## What NPC Forge does not do

- **Creatures that are not bipedal** (hounds, stags, serpents) are not rigged or animated. Make the model somewhere else and bring it in: [model-import.md](model-import.md).
- **A model whose arms touch its body cannot be rigged**: the joints are found from the gap under each arm. The step says so (*needs an A-pose*) and the earlier steps are kept; ask for the model sheet in
  an A-pose (`sheet.pose`) and make the 3D model again.
- **A carried object can be drawn as something else.** A "leather pen-case on the belt" came out as a sheathed blade. The image models draw the most usual thing that fits the words; say what it is not
  ("a small flat leather pen case, not a weapon") and check the picture. There is no automatic check of what is carried.
- **Held items and hats on a character** (a sword in the hand, a hat on the head) are a feature of the Items tab: [equip.md](equip.md).
