# Changelog

What changed in each version of NPC Forge. The version is shown in the About box and by `python -m npcgen --version`.

## 1.1.1 (2026-11-27)

Fixes after 1.1.0, and NPC Forge now waits for the other Fel's Studio apps before it takes the graphics card.

### What changed for you

- **A character sheet first.** A new page starts with **Make a character sheet**: roll a character, change anything and
  **Save** it, all on the CPU with no downloads. **Add a portrait** on the sheet checks what that portrait needs and opens
  **Set up ComfyUI** for it when ComfyUI or its model is missing. Setup's last page no longer offers Set up ComfyUI.
- **One model at a time waits its turn.** Before it draws, NPC Forge waits for Voice Lab and Altera to finish what they are
  doing, then takes the card alone and only then unloads LM Studio's and Ollama's models, so an Altera reply is never cut
  off. **Allow unloading LM Studio and Ollama models**, under the switch, is on by default; turn it off to keep their
  models loaded.
- **Pixel-art walks step properly.** A side-view walk drawn with Qwen-Image-Edit 2511 whose passing step came out too wide
  gets that frame redrawn, up to twice. A redraw is kept only when the legs moved and the figure still matches.

### Fixed

- **Cut-out sprites:** no magenta (or green) fringe round the figure, and the backdrop's own soft shadow, which the models
  sometimes draw between the feet, is cut out too.
- **Pixel-art exports** keep small colours such as a face beside a large robe: the palette is shared out by colour, not by
  pixel count, so a 24-colour export no longer turns skin olive.
- **Lines a character speaks** are in the first person: "My aim is clearing my name", not "her name", and a typed
  occupation reads "the crook", not "the Crook".
- **A walk** whose reference view failed is not drawn without it: that direction and the assembly say why, and **Retry**
  runs them again.
- **The header's tabs** take a row of their own when they do not fit, instead of scrolling.
- A job that had just reported progress could be stopped as stalled. The check and the progress now take turns.
- Reading a face scan while it was being replaced could fail. It is read again.
- Links to Altera use its new address, github.com/Fel101/fels-altera.

### For other programs

API 52. `GET /api/gpu` adds `unload_external_models`, and `PUT /api/gpu/settings` takes one setting per request. docs/api.md
names the export route's parts: `/api/npcs/{npc}/export/{clip}/{preset}.zip`.

## 1.1.0 (2026-11-26)

NPC Forge is now one of the four apps of Fel's Studio, with Voice Slate, Voice Lab and Altera: each is free, runs on your own
PC and works on its own, and together they hand voices and characters to each other.

### What changed for you

- **A new address: port 8766.** NPC Forge opens at http://127.0.0.1:8766, because 8765 is Voice Lab's. The first time
  `start.bat` runs, a `config.json` that saved port 8765 is changed to 8766, and it says so. Change bookmarks, and any game
  client or script that names port 8765; the MCP server finds NPC Forge by itself.
- **The parts made for adults are a separate add-on.** The core no longer has them. Characters made with them stay in your
  library and keep their saved settings; the add-on is available privately ([extensions](docs/extensions.md)).
- **Voices come from Voice Lab.** NPC Forge no longer installs voice engines of its own. In the Voice Designer you pick one
  of Voice Lab's ready voices, describe a new one, or send a recording made with permission, and Voice Lab speaks the lines.
  This needs a Voice Lab with Suite API 1; with an older one, NPC Forge says to update it. Voices and packs made by 1.0 can
  still be read. The `voice-envs` and `voice-models` folders where 1.0 kept its engines are no longer used: the update says
  how large they are, and you can delete them.
- **Character cards.** A saved character exports as a character card (PNG, V3 with V2 inside) or as CHARX, a ZIP with its
  pictures, living portrait, voice and licence, which SillyTavern and other chat apps read. **Send to Altera** puts it in
  Altera's inbox. A voice from a recording goes with the card only when you confirm the permission.
- **What your PC can make.** The **Your PC** box on the Models card says what your graphics card makes: about 16 GB
  makes portraits with Z-Image Turbo, full-body sprites and Hunyuan3D models, with expressions slowly; about 24 GB adds
  Qwen-Image 2512 portraits; 32 GB makes everything, model sheets, walk cycles, Trellis2 and Pixal3D too. A new page, and a
  request that names no engine, starts with the engines for your card. The sizes were measured on one card
  ([hardware](docs/hardware.md)).

### Added

- **Setup.exe**, with its own Python: nothing else to install first, and no administrator rights; it installs for your
  Windows account in `%LOCALAPPDATA%\FelsNPCForge`. A newer Setup.exe run over an older copy updates it and keeps your
  characters. Each release also has a ZIP, a `SHA256SUMS` file and a GitHub attestation that they were built from this
  repository by its release workflow; the README says how to check them.
- **Set up ComfyUI** finds your ComfyUI and installs the models for your card (59 GB for 16 GB, 81 GB for 24 GB, 90 GB for
  32 GB), each pinned to its exact file, checked as it arrives and resumed after a stop. Nothing downloads until you
  choose Install. Setup.exe offers it on its last page.
- **One model at a time**, a switch on the Models card and in the GPU light, on by default: before NPC Forge draws, Voice
  Lab, Altera, LM Studio and Ollama unload their models and wait, and the job's note says what was unloaded.
- **Connected apps** in About shows where Voice Lab and Altera answer, how many of the Lab's voices are ready, and
  whether Send to Altera can reach Altera.
- **The AI label in every file.** AI-generated PNGs carry IPTC's `trainedAlgorithmicMedia` source type and the models that
  made them, and GLB files carry the same in `asset.extras.fels` ([licences](docs/licences.md)).
- A [gallery](docs/gallery.md) of one character made on one PC, and a [responsible use](docs/responsible-use.md) page.

### Changed

- The README is short, with the rest in [docs](docs/README.md) pages for people. The Voice Designer, Scenes and Creatures
  tabs are labelled Preview.
- A picture of one flat colour from Qwen-Image 2512 or Qwen-Image-Edit 2511, which they can return when the card runs
  short of memory, fails its step with that reason instead of being kept.
- NPC Forge has a Windows icon, the anvil of Fel's Studio's icon family.

### Fixed

- The living portrait's blink and talking frames showed nothing with Qwen-Image-Edit 2511, which returns each one as the
  middle 992 pixels of the 1,024 pixel portrait; they are now lined up instead of stretched.
- On Windows, saving a character, item or scene could fail while another part of NPC Forge was reading the same file.

### For other programs

API 51 (1.0.0 was API 44). `GET /api/health?identity=1` names the app and its version (45); body build controls come
from an extension (46); speech goes through Voice Lab (47); characters export as PNG cards and CHARX (48); One model at a
time and the Your PC tier (49); Set up ComfyUI (50); Connected apps (51). [API](docs/api.md) lists each change.

## 1.0.0 (2026-11-20)

The first public release.

- **Characters:** roll, describe or import a cast; 20 settings, 632 occupations with outfits, personalities, cast packs.
- **Pictures:** portraits, expressions, full-body sprites and model sheets in 17 art styles, drawn by Qwen-Image 2512 and
  Qwen-Image-Edit 2511 by default, with Mage-Flow, Z-Image Turbo and Qwen Image 2.1 to choose from.
- **Animation:** 4-direction walk cycles and experimental clips, exported for Godot 4, Unity, Aseprite or a plain atlas.
- **3D:** models from the model sheet (Trellis2, Pixal3D, Hunyuan3D), clean-up and painting, a detailed head, skeletons and the
  Quaternius animation library, game-size versions, and models imported from elsewhere.
- **Items:** weapons, armour, hats, shoes, bags and props as 3D models; procedural glasses fitted to a face; held items and
  hats put on a rigged character.
- **Licences:** every model is rated for commercial use, and every character carries the worst rating of what made it.
- **Experimental:** the Voice Designer, Scenes and Creatures tabs.
- **Updates:** About > Check for updates and Update now, and `update.bat` / `update.sh` (with `--check`, `--dry-run` and
  `--undo`), keeping the library, `config.json`, `local` and `.venv`.
- **Agents:** an MCP server (`python -m npcgen.mcp`) so Claude Code, Codex, opencode and other MCP clients can make characters
  and export them for a game.
- The Body Designer (designed bodies and fitted clothes) is an optional add-on and is not part of this release.
