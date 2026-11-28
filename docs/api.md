# The HTTP API

Every route `server.py` serves, written by `scripts/make_api_doc.py` from the server's own route table (run it again after
adding a route; a test fails while this page is out of date). The page itself uses exactly these routes, so the quickest way
to see a request's body is to do the thing in the page with the browser's network panel open.

The rules every request follows:

- The server listens on `http://127.0.0.1:8766` by default. A request whose `Host` is not a loopback name is refused, and so is
  one whose `Origin` header names another site. A script that sends no `Origin` is accepted.
- `POST`, `PUT` and `DELETE` bodies are JSON (`Content-Type: application/json`), at most 2 MB; the two uploads below
  (a model's `.glb`, a scene's frames) are the exceptions and say their own type.
- Errors come back as `{"error": "what went wrong"}` with a 4xx or 5xx status. A queued job and each of its steps carry a
  `code` when they fail ([game-clients.md](game-clients.md) lists them).
- `GET /api/config` → `api_version` is raised whenever a field the page needs is added; `version` is NPC Forge's own version.

How to drive it from a game's own scripts, with the options that matter for a bulk run: [game-clients.md](game-clients.md).

`GET /api/gpu` includes `suite_lock` alongside its picture-job readiness and per-process memory readings.
`GET /api/gpu?lock=1` returns just `suite_lock` without contacting ComfyUI. `suite_lock.holders` lists the apps holding the
lock, with `app`, `name`, `pid`, `what`, `since` and `alone`. `suite_lock.alone` gives the request for exclusive use, with the
same identity and timing fields, or `null`.

**One model at a time.** `GET /api/gpu` also says `one_model_at_a_time` (the switch), `unload_external_models` (whether
the switch also unloads LM Studio's and Ollama's models), `alone` (whether NPC Forge holds the GPU alone for pictures now)
and `switch_applies` (`now`, or `next_job` while a job that started the other way runs). `PUT /api/gpu/settings` with one
of `{"one_model_at_a_time": true|false}` or `{"unload_external_models": true|false}` changes that setting and saves it in
`config.json`'s `gpu` section; a body with both, or neither, is refused with 400. With the switch on, a picture first asks
Voice Lab and Altera to unload (`POST /api/unload`), waits while either says it is busy, takes the suite lock alone and
then, with `unload_external_models` on (the default), unloads LM Studio's and Ollama's models. Off, it holds the lock
shared and unloads nothing of anyone else's. Only pictures are affected: work in Voice Lab and Altera shares the GPU
either way.

**Set up ComfyUI** ([comfyui-setup.md](comfyui-setup.md)). `GET /api/comfy-setup` says where ComfyUI is (`comfy`), the
tier and what each tier ticks (`tiers`), and every part (an engine such as `image:zimage`) with its files, NPC Forge's nodes,
other people's node packs and its licence; `?progress=1` gives just `install`. `POST /api/comfy-setup/install` with
`{"parts": [...], "yes": true}` checks the files already there against their pinned SHA-256, downloads what is missing from
its pinned Hugging Face commit and copies NPC Forge's own nodes; without `"yes": true` it is refused. `POST
/api/comfy-setup/stop` keeps what arrived. `POST /api/comfy-setup/folder` takes `{"folder": path}`, `{"pick": true}` or
`{"folder": null}` for when ComfyUI is not running.

## Character cards

- `GET /api/npcs/<id>/card.png` returns the portrait with Character Card V3 in its `ccv3` chunk and V2 in its `chara` chunk.
- `GET /api/npcs/<id>/card.charx` returns a ZIP with `card.json` and assets listed by `embeded://` URI. Its `extensions.fels`
  block links the sheet, the living-portrait manifest and any Voice Lab persona included in the archive. The manifest's
  frame paths are relative to the archive root. Check the main icon against `living_portrait.portrait_sha256` before use.
  Licence ratings are `commercial_ok`, `commercial_conditions`, `not_cleared` and `non_commercial`.
- A preset uses its model and name. A Cast voice whose origin is established as synthetic can be packaged by a GET.
  Append `?voice=0` to omit voice metadata and any persona.
- Forge's card dialog reads `GET /api/npcs/<id>/card-options`. Its `altera_inbox` enables sending. When the dialog asks for
  recording permission, `POST /api/npcs/<id>/card.charx` sends the user's `permission: true` with the `cast_voice.consent_for`
  value from the options. Set `include_voice: false` to omit the voice. A POST with `send: true` puts the file in Altera's
  inbox and returns `{"sent": true}`. A download POST returns the archive directly.

## Changes

- **API 52.** `GET /api/gpu` adds `unload_external_models`: whether One model at a time also unloads
  LM Studio's and Ollama's models, on by default. `PUT /api/gpu/settings` takes it as a setting of its own, one
  setting per request. With the switch on, Forge now unloads them only after it holds the GPU alone.
- **API 51.** Connected apps: `GET /api/suite/apps` says where Voice Lab and Altera answer, with their versions, how many
  of the Lab's voices are ready, and whether Altera's inbox is there for Send to Altera.
- **API 50.** Set up ComfyUI: `GET /api/comfy-setup`, and `POST /api/comfy-setup/install`, `/stop` and `/folder`.
- **API 49.** One model at a time is a switch: `PUT /api/gpu/settings`, and `GET /api/gpu`'s `one_model_at_a_time`,
  `alone` and `switch_applies`. A job's step note says what it unloaded to make room. `GET /api/health` adds
  `vram_total_mb` and `your_pc`: the tier of ComfyUI's card, what it makes and the engines it starts with, which a request
  that names no engine now gets (docs/hardware.md).
- **API 48.** Saved characters export as PNG and CHARX, with a consent-aware Voice Lab file and Altera inbox handoff.
- **API 47.** Voice Lab provides new speech through `GET /api/voice/lab` and the character's `/voice/lab` routes.
  Forge's local speech workers, model discovery and Drama studio routes are retired. Old voice records and packs remain readable.
- **API 46.** Optional body build controls are served by their local extension. Existing characters keep their saved settings.
- **API 45.** `GET /api/health` adds a `fels` object with `contract`, `app`, `version` and `api`. Use
  `GET /api/health?identity=1` to read just that object without waiting for the ComfyUI and tool checks.


172 routes.


## Server, settings and the queue

| Method | Path | What it does |
|---|---|---|
| `GET` | `/api/health` | Whether ComfyUI answers, which model files and nodes each feature lacks, the GPU, its tier (`your_pc`) and the voice engines. |
| `GET` | `/api/config` | Everything the page needs to draw itself: settings, styles, engines, qualities, licences, defaults, `version`. |
| `GET` | `/api/choices` | What each character-sheet field can be in a setting, for the Advanced options popup (roll.field_choices). |
| `POST` | `/api/manner` | Suggest how a character talks and comes across, from their traits: {setting, traits: [...]} -> {speech, demeanor}. |
| `GET` | `/api/jobs` | The queue: every job with its steps, status and `code`. |
| `GET` | `/api/gpu` | Picture-job readiness, One model at a time and the suite GPU lock. |
| `PUT` | `/api/gpu/settings` | Save the suite coordination switch or the separate permission to unload external programs. |
| `POST` | `/api/gpu/free-voice-lab` | Ask the current Voice Lab GPU holder to unload after the user requests it in the picture queue. |
| `POST` | `/api/jobs/cancel-all` | Stop every queued and running job. |
| `POST` | `/api/jobs/{job}/cancel` | Stop one job. |
| `POST` | `/api/jobs/{job}/retry` | Run a failed or stopped job again from its first unfinished step. |

## Cast packs

| Method | Path | What it does |
|---|---|---|
| `POST` | `/api/cast-packs/import` | Import a JSON/CSV cast and create a pack. |
| `GET` | `/api/cast-packs` | List cast packs. |
| `POST` | `/api/cast-packs` | Create cast pack. |
| `GET` | `/api/cast-packs/{pack}/export.zip` | Export cast pack. |
| `GET` | `/api/cast-packs/{pack}` | Get cast pack. |
| `PUT` | `/api/cast-packs/{pack}` | Update cast pack. |
| `DELETE` | `/api/cast-packs/{pack}` | Delete cast pack. |

## Characters

| Method | Path | What it does |
|---|---|---|
| `POST` | `/api/roll` | Roll a character: body {setting, locks, seed, ...}; nothing is saved. |
| `POST` | `/api/cast` | Create NPCs from a JSON/CSV cast file; optionally queue image generation for all of them. |
| `POST` | `/api/preview` | The prompts a character would send, without drawing anything. |
| `GET` | `/api/npcs` | The library. |
| `GET` | `/api/npcs/{npc}/card-options` | Options for the card dialog: inbox availability and the saved Cast voice's sharing requirements. |
| `GET` | `/api/npcs/{npc}/card.{charx|png}` | Download a CCv3 CHARX or PNG. |
| `POST` | `/api/npcs/{npc}/card.charx` | Download CHARX or send it to Altera (send: true). |
| `GET` | `/api/npcs/{npc}/licences` | Which models made this character's pictures, 3D model and voice, what each is licensed for, and the worst of them as the verdict. |
| `GET` | `/api/trash` | The characters deleted from the library, newest first. |
| `POST` | `/api/trash/{folder}/restore` | Restore npc. |
| `GET` | `/api/trash/{folder}/thumb.png` | Trash thumb. |
| `DELETE` | `/api/trash/{folder}` | Remove one trashed character for good (it cannot be brought back after this). |
| `POST` | `/api/trash/empty` | Remove everything in the trash for good. |
| `POST` | `/api/npcs` | Save a character (a rolled one, or one written by hand). |
| `GET` | `/api/npcs/{npc}` | One character: its sheet and every asset's record. |
| `POST` | `/api/npcs/{npc}/dialogue/generate` | Generate dialogue. |
| `PUT` | `/api/npcs/{npc}/dialogue` | Save dialogue. |
| `GET` | `/api/npcs/{npc}/dialogue/export/{json|ink|yarn}` | Export dialogue. |
| `GET` | `/api/npcs/{npc}/voice/references` | Voice reference history. |
| `POST` | `/api/npcs/{npc}/voice/references/{reference}/select` | Voice reference restore. |
| `POST` | `/api/npcs/{npc}/voice/suggest` | The voice the character sheet suggests: a description, the best stock voices, and what a take reads. |
| `PUT` | `/api/npcs/{npc}/voice/lab` | Use a named Voice Lab voice for new speech while keeping the character's saved WAV files. |
| `POST` | `/api/npcs/{npc}/voice/lab` | Queue a new Voice Lab Cast voice from a description or an uploaded or saved reference recording. |
| `PUT` | `/api/npcs/{npc}/voice` | Voice save. |
| `PUT` | `/api/npcs/{npc}/voice/studio` | Voice studio save. |
| `POST` | `/api/npcs/{npc}/voice/studio/preview` | Voice studio preview. |
| `POST` | `/api/npcs/{npc}/voice/effects/preview` | Voice effects preview. |
| `POST` | `/api/npcs/{npc}/voice/lip-sync/preview` | Voice alignment preview. |
| `GET` | `/api/npcs/{npc}/voice/rigs` | Voice rigs. |
| `GET` | `/api/npcs/{npc}/voice/mesh-rig` | Voice mesh get. |
| `PUT` | `/api/npcs/{npc}/voice/mesh-rig` | Voice mesh save. |
| `PUT` | `/api/npcs/{npc}/voice/rig/{portrait|sprite}` | Voice rig save. |
| `POST` | `/api/npcs/{npc}/voice/rig/{portrait|sprite}/frames` | Voice rig frame. |
| `POST` | `/api/npcs/{npc}/voice/rig/{portrait|sprite}/restore/{hash}` | Voice rig restore. |
| `POST` | `/api/npcs/{npc}/voice/lip-sync` | Voice alignment save. |
| `POST` | `/api/npcs/{npc}/voice/comparisons` | Voice comparison create. |
| `PUT` | `/api/npcs/{npc}/voice/comparisons/{comparison}/candidates/{candidate}` | Voice comparison review. |
| `POST` | `/api/npcs/{npc}/voice/takes` | Queue 1-4 candidate voices (the quick lane: they run before waiting picture jobs, never in the middle of one). |
| `POST` | `/api/npcs/{npc}/voice/takes/{take}/select` | Voice select. |
| `DELETE` | `/api/npcs/{npc}/voice/takes/{take}` | Voice delete take. |
| `POST` | `/api/npcs/{npc}/voice/test` | Say a line (or preview a mood) in the current voice: a quick job, its clip lands in voice/tests/. |
| `POST` | `/api/npcs/{npc}/voice/auditions` | Voice audition create. |
| `PUT` | `/api/npcs/{npc}/voice/auditions/{audition}/cases/{case}` | Voice audition review. |
| `GET` | `/api/npcs/{npc}/voice/auditions/{audition}/pack.zip` | Voice audition pack. |
| `POST` | `/api/npcs/{npc}/voice/lines` | Voice the dialogue lines named in `ids` (every line when none are named) in the saved voice. |
| `GET` | `/api/npcs/{npc}/voice-pack.zip` | The voice and every up-to-date voiced line, with lip-sync tracks and the dialogue files (npcgen/voice/pack.py). |
| `POST` | `/api/npcs/{npc}/voice/upload` | A recording to copy: {audio: WAV data URL, text, consent: true}. |
| `POST` | `/api/npcs/{npc}/voice/copy` | Voice copy. |
| `PUT` | `/api/npcs/{npc}` | Change a character's sheet; what was made from it is marked out of date unless `keep_fresh` names it. |
| `POST` | `/api/npcs/{npc}/fresh` | Say that some pictures and models are still right after an edit, which takes their 'out of date' flag off without making them again: body {"kinds": [...]} (the same kinds as `keep_fresh`). |
| `DELETE` | `/api/npcs/{npc}` | Move a character to the trash. |
| `DELETE` | `/api/npcs/{npc}/assets/{asset}[/{id}]` | Delete asset. |
| `POST` | `/api/npcs/{npc}/assets/{asset}/candidates/{candidate}/select` | Bring back an earlier version of a picture (or a saved variant): the picture it replaces is kept in turn. |
| `DELETE` | `/api/npcs/{npc}/assets/{asset}/candidates/{candidate}` | Remove one earlier version of a picture for good. |
| `POST` | `/api/npcs/{npc}/model-history/{model}/restore` | Bring back an earlier 3D model, with its skeleton and everything else it had: the model in use is kept in its place. |
| `DELETE` | `/api/npcs/{npc}/model-history/{model}` | Remove one earlier 3D model for good. |
| `POST` | `/api/npcs/{npc}/model-upload` | Use a .glb from the owner's computer as this character's 3D model, exactly as it is (the raw bytes, `?name=` says what the file is called). |
| `POST` | `/api/npcs/{npc}/photo` | Attach (or replace) the real photo a portrait will be edited from. |
| `GET` | `/api/npcs/{npc}/files/{path}` | Npc file. |
| `GET` | `/api/export.zip` | Export zip. |
| `GET` | `/api/npcs/{npc}/export/{clip}/{preset}.zip` | A finished animation as an engine-ready ZIP (sheet(s), atlas JSON, engine resource, preview page). |
| `GET` | `/api/npcs/{npc}/sheet.zip` | A finished model sheet as a ZIP: one PNG per view, the stitched sheet and a JSON record. |
| `POST` | `/api/generate` | Queue pictures, animations, 3D and voice for a character: body {npc_id, assets, options}. |
| `GET` | `/api/npcs/{npc}/body-3d` | What the designed-body card needs for this character: whether the Body tab saved a body for it, the base that suits its gender, and what its 3D model was designed from (or null). |
| `POST` | `/api/npcs/{npc}/body-3d/colours` | Put new colours on the garments a designed body wears without making the body again: body {"colours": {"shirt": "#3f6ba8", ...}} (only garments it wears). |
| `GET` | `/api/npcs/{npc}/face` | Whether this character's face has been scanned (to fit glasses to it), what is happening now, and a summary of the scan. |
| `POST` | `/api/npcs/{npc}/face/scan` | Scan this character's face on a thread of its own (about 40 seconds): body {"force": bool, "estimate": bool}. |
| `DELETE` | `/api/npcs/{npc}/face/scan` | Stop this character's face scan. |
| `GET` | `/api/npcs/{npc}/equipped.glb` | The character's model with several items on it, each at what is kept for this character: ?items=sword,shield:hand_l,hat (an item's id, then optionally :slot). |

## Items

| Method | Path | What it does |
|---|---|---|
| `GET` | `/api/items` | List items. |
| `POST` | `/api/items` | Create item. |
| `GET` | `/api/items/{item}` | Get item. |
| `PUT` | `/api/items/{item}` | Update item. |
| `DELETE` | `/api/items/{item}` | Delete item. |
| `DELETE` | `/api/items/{item}/assets/{hero|views|high|model_3d}` | Delete item asset. |
| `POST` | `/api/items/recipe-preview` | The GLB of glasses or of a garment's pattern from a recipe, without saving anything (the editor's live view): body {"family", "kind", "recipe", "faces"}. |
| `POST` | `/api/items/recipe-from-text` | The designer's controls set from a few words (glasses or fitted clothes): body {"text", "family", "kind", "recipe"} -> {"recipe": the whole recipe, "changed": [fields], "understood": [...]}. |
| `POST` | `/api/items/{item}/fit` | Where a pair of glasses goes on a character's face: body {"npc": id, "nudge": {up, forward, tilt, size}}; the saved fit's nudge when none is sent. |
| `PUT` | `/api/items/{item}/fits/{npc}` | Keep a nudge as this pair's fit on a character: body {"nudge": {...}}. |
| `DELETE` | `/api/items/{item}/fits/{npc}` | Forget item fit. |
| `POST` | `/api/items/{item}/dress` | Put fitted clothes on a designed body: body {"npc": id, "force": bool}. |
| `GET` | `/api/items/{item}/dress/{npc}` | Where the fit of this garment to this character is: none, queued, running, done (with what was kept), error or cancelled. |
| `DELETE` | `/api/items/{item}/dress/{npc}` | Stop this fit, whether it is waiting or running. |
| `GET` | `/api/items/{item}/worn/{npc}.glb` | The character's model with the item on it as a GLB. |
| `POST` | `/api/items/{item}/equip` | Where a held item or a hat goes on a character, worked out without building a file: body {"npc": id, "slot": "hand_r" \| "hand_l" \| "head", "nudge": {...}}; slot and nudge default to what is kept for that character, else the kind's usual slot and the neutral nudge. |
| `PUT` | `/api/items/{item}/equips/{equip}` | Keep a slot and nudge as this item's way of being carried or worn by a character: body {"slot", "nudge"}. |
| `DELETE` | `/api/items/{item}/equips/{equip}` | Forget item equip. |
| `POST` | `/api/items/{item}/make` | Queue the steps an item needs: body {"request": {"make", "finish", "redo"}, "options": {...}} (npcgen/items.py). |
| `GET` | `/api/items/{item}/files/{path}` | Item file. |
| `GET` | `/api/items/{item}/export.zip` | One item as a ZIP: its record, pictures and 3D model. |

## 3D import

| Method | Path | What it does |
|---|---|---|
| `GET` | `/api/import-3d/sources` | The GLB files ComfyUI can see (its output and input folders), newest first, for the Import a 3D model list. |
| `GET` | `/api/import-3d/inspect` | What is inside one of those files, read from its first bytes: faces, whether it has colour, whether it stands up, and whether it would be closed and simplified by default. |

## Creatures

| Method | Path | What it does |
|---|---|---|
| `GET` | `/api/creatures` | List creatures. |
| `POST` | `/api/creatures` | Create creature. |
| `GET` | `/api/creatures/{creature}` | Get creature. |
| `PUT` | `/api/creatures/{creature}` | Update creature. |
| `DELETE` | `/api/creatures/{creature}` | Delete creature. |
| `GET` | `/api/creature-projects` | The games creatures are made for (npcgen/creature_projects.py), and the engines a pack can be installed for. |
| `GET` | `/api/creature-projects/{game}` | Get creature project. |
| `POST` | `/api/creature-projects` | A new game, from the Projects panel. |
| `PUT` | `/api/creature-projects/{game}` | Create a game, or change one: its triangle budget, style, licence rule and clip names. |
| `POST` | `/api/creatures/{creature}/make` | Queue the steps: body {"request": {"steps": ["concept", "3d", "prep"], "redo": [...]}, "options": {}}. |
| `POST` | `/api/creatures/{creature}/concept/choose` | Make one picture of the last grid the concept picture (and approve it): body {"seed": n}. |
| `POST` | `/api/creatures/{creature}/approve` | Approve the concept picture, so its 3D model can be made. |
| `GET` | `/api/creatures/{creature}/files/{path}` | Creature file. |
| `GET` | `/api/creatures/{creature}/pack.zip` | The creature's pack (npcgen/creaturepack.py): the game model, its concept picture and a manifest, as one ZIP. |

## Voices

| Method | Path | What it does |
|---|---|---|
| `GET` | `/api/voice/engines` | Voice authoring options and compatibility metadata for saved records. |
| `GET` | `/api/voice/library` | Voice library list. |
| `POST` | `/api/voice/library` | Voice library save. |
| `GET` | `/api/voice/library/{voice}` | Voice library get. |
| `PUT` | `/api/voice/library/{voice}` | Voice library update. |
| `GET` | `/api/voice/library/versions/{version}/audio.wav` | Voice library audio. |
| `POST` | `/api/voice/library/{voice}/recordings` | Voice library recording. |
| `POST` | `/api/voice/library/versions/{version}/approval` | Voice library approve. |
| `POST` | `/api/voice/library/versions/{version}/derive` | Voice library derive. |
| `POST` | `/api/voice/library/versions/{version}/transcript-draft` | A suggested transcript for the region (default: the whole reference). |
| `POST` | `/api/voice/library/versions/{version}/denoise` | A new unapproved draft with background noise reduced. |
| `POST` | `/api/voice/library/versions/{version}/assign` | Voice library assign. |
| `POST` | `/api/voice/batch` | Voice many characters: {npc_ids, engine, takes 1-4, lines: bool, redo: bool}. |
| `GET` | `/api/voice/review` | Characters whose voice waits for you (batch takes, first take in use), with their takes. |
| `GET` | `/api/voice/lab` | Find Voice Lab and list the named preset and Cast voices it offers. |

## Scenes

| Method | Path | What it does |
|---|---|---|
| `GET` | `/api/scenes/capabilities` | Scene capabilities. |
| `GET` | `/api/scenes` | List scenes. |
| `POST` | `/api/scenes` | Create scene. |
| `POST` | `/api/scenes/preview` | Preview scene. |
| `POST` | `/api/scenes/suggest` | Suggest scene. |
| `GET` | `/api/scenes/{scene}` | Get scene. |
| `POST` | `/api/scenes/{scene}/duplicate` | Duplicate scene. |
| `POST` | `/api/scenes/{scene}/media` | Upload scene sound. |
| `POST` | `/api/scenes/{scene}/takes/{take}/select` | Select scene take. |
| `PUT` | `/api/scenes/{scene}` | Update scene. |
| `DELETE` | `/api/scenes/{scene}` | Delete scene. |
| `GET` | `/api/scenes/{scene}/timeline` | Scene timeline. |
| `POST` | `/api/scenes/{scene}/render` | Render scene. |
| `POST` | `/api/scenes/{scene}/voice` | Voice scene. |
| `POST` | `/api/scenes/{scene}/background` | Generate scene background. |
| `POST` | `/api/scenes/{scene}/frames` | Upload scene frames. |
| `GET` | `/api/scenes/jobs/{job}` | Scene job. |
| `POST` | `/api/scenes/jobs/{job}/cancel` | Cancel scene job. |
| `GET` | `/api/scenes/{scene}/files/{path}` | Scene file. |
| `GET` | `/api/scenes/{scene}/files/{path}` | Scene audio file. |
| `GET` | `/api/scenes/{scene}/files/{path}` | Scene background file. |
| `GET` | `/api/scenes/{scene}/files/{path}` | Scene studio media file. |
| `GET` | `/api/scenes/{scene}/export.zip` | Export scene. |

## Other

| Method | Path | What it does |
|---|---|---|
| `GET` | `/api/suite/apps` | Connected apps (suite contract 14): Voice Lab and Altera where they answer, with their versions and addresses, or why they were not found; for Voice Lab, how many of its voices are ready; and whether Send to Altera has an inbox to use. |
| `GET` | `/api/comfy-setup` | Where ComfyUI is, what each tier needs, what is there already and the install in progress. |
| `POST` | `/api/comfy-setup/folder` | The ComfyUI folder Set up ComfyUI uses when ComfyUI is not running: {"folder": "D:/ComfyUI"}, {"pick": true} to choose it with this computer's own folder picker (refused when NPC Forge needs a password, as the picker would open on the server's screen), or {"folder": null} to forget it. |
| `POST` | `/api/comfy-setup/install` | Install the parts in {"parts": ["image:zimage", ...], "yes": true}: check the files already there against their pinned SHA-256, download what is missing from its pinned Hugging Face commit, and copy NPC Forge's own nodes into custom_nodes. |
| `POST` | `/api/comfy-setup/stop` | Stop the install. |
| `GET` | `/api/update` | This version, the last answer from GitHub (kept across restarts) and what the last update left to say. |
| `POST` | `/api/update/check` | Ask GitHub now whether there is a newer release: {"current", "latest", "newer", "message", "release", "checked_at"}. |
| `POST` | `/api/update/plan` | What Update now would do, as update.bat shows it: {"ok", "newer", "message", "from", "to", "release", "steps", "notes", "method"} or {"ok": false, "problem"}. |
| `POST` | `/api/update/start` | Update now: start the updater in a window of its own and stop this server a moment later (after this answer). |
| `DELETE` | `/api/update/notice` | Update notice seen. |
