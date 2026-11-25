# Customising

- **Tables** in `data/` are re-read on every roll: `common.json` (names, hair, builds, traits), `settings/*.json`
  (occupations, outfits, hobbies; add a file for a new setting, which may also replace the shared hair, facial hair,
  glasses and distinguishing-feature tables, as the Old West, post-apocalypse, cyberpunk, noir detective, pirate and
  Nordic saga settings do to keep each one's hair and marks in period; the zombie-outbreak setting goes further and also
  replaces `builds`, `traits` and the bio sentence templates, for a decayed look and a past-tense bio), `styles.json`,
  `expressions.json`, `animations.json` (clips, poses, which references each
  direction uses), `sheet.json` (the model-sheet views, poses and wording). A setting file may replace more of the shared
  tables (`builds`, `age_groups`, `traits`, `bio_templates`, and the hair, glasses and feature tables) and may state
  rules of its own:
  `genders` (only these are rolled or accepted), `min_age` (nobody younger is rolled or accepted; the zombie setting uses
  this itself, setting-wide, so it never rolls a child or teen), `negative` (added to
  every text-to-image negative prompt), `dye_chance` (how often hair under 40 is drawn from `dyed_hair_colors` instead of
  a natural colour; default about 6%, cyberpunk turns it up to 35%) and `default_style` (the art style picked when you
  switch to the setting). It may also carry its own `heritages` (the Heritage list, in the format of `common.json`, with
  its own names), `hair_color_choices` (the colours the **Hair colour** choice in the Who card offers; without it, every
  colour the heritage tables can roll), `style_text` (words for the portrait prompt in place of a style's own sentence,
  per style: `{"photoreal": "..."}`) and `default_expression`. In a setting with `races` (fantasy, adventurers' guild,
  Nordic saga, anthro, alien species, mythical creatures), a race may state its own `hair_colors` the same way a heritage does (an elf rolls silvers and
  platinum blondes instead of the human tables), and may also state its own `skin` (a list to choose from instead of
  the heritage tables), `builds` (a list of the shared build labels it is limited to) and `features` (physical traits
  added to every character of that race, before its rolled distinguishing mark). The anthro setting's races all state `skin`, `hair_colors` and `features`, and five of them (rabbit, avian, bear, taur, dragontaur) `builds` too;
  its `therian` (therianthrope) race states only `features`, so it keeps a plain human skin tone, hair
  colour and build -- the therianthrope is still mostly human, unlike the other nine species. A race may also state its
  own `eyes` (a list; the prompt adds "eyes", so a three-eyed alien writes `"three golden"`), and a race that is not
  human at all may state fixed `hair` text (a grey alien's `"a smooth hairless head"`, a gorgon's crown of snakes) and
  `facial_hair` (`""` = never a beard). Fixed hair gives way when you pick a hair colour or style yourself.
  Picking a hair colour replaces the rolled colour and nothing else: the same seed gives the same person with other hair.
  Built-in settings always list before any that an extension adds, whatever `order` each one states. The **About**
  button at the bottom of the left panel shows what NPC Forge is and the licence note.
- **Art styles** in `styles.json`: `style` and `negative` go into the Mage-Flow prompt, `edit_name` names the style when
  editing from the portrait, `backdrop` is the portrait background when transparency is off, `hard_edges` turns off edge
  softening for cut-outs (flat-shaded styles want this), `monochrome: true` turns every finished picture of the style to
  shades of grey after the cut-out (manga: a character described in colour still came out coloured on every engine, whatever
  the words said; the raw files keep their colour), `use_heritage` adds "of X descent" to the prompt, and
  `guided_walk: false` makes the walk fall back to pose words instead of the drawn pose puppet (the two flat-shaded
  styles, pixel art and flat cartoon, need this: the model hands the puppet back unchanged instead of repainting it. The
  five newer styles were checked on one character each and all kept the puppet, including the two others that also use
  flat cel shading (comic, chibi); if a new flat-shaded style you add does the same, give it `guided_walk: false` too).
- **Config**: an optional `config.json`, for example
  `{"comfy_url": "http://127.0.0.1:8188", "port": 8766, "edit_cfg": 3.5, "models": {"unet_t2i": "mage_flow_bf16.safetensors"}}`.
  Environment variables `NPCGEN_COMFY_URL`, `NPCGEN_PORT`, `NPCGEN_LIBRARY` and `NPCGEN_AUTH_TOKEN` also work.
  On a normal start, a saved port of 8765 moves to 8766 because Voice Lab uses 8765. To keep using 8765 on purpose, set
  `NPCGEN_PORT=8765` or start with `python server.py --port 8765`.
- **Prompts**: open *Prompts sent to the picture model* on a character to see exactly what is sent. The wording lives in
  `npcgen/prompts.py`, `npcgen/anim.py` and `data/sheet.json`.
- **Extensions**: a folder `extensions/<name>/` (shipped with NPC Forge)
  or `local/extensions/<name>/` (your own) whose `__init__.py` defines `register(host)` is loaded when the server starts; a local
  one with the name of a shipped one is skipped. `host` gives it the library, the ComfyUI client and the job queue, and lets it add:
  API routes (`host.route`); a page of its own (`host.add_page`, served at `/ext/<id>/` with a link in the tab bar; pass
  `visible=` a function to be able to switch the page off again); a script and stylesheet for the main page (`host.add_ui`,
  which finds `window.NPCForge` with `reload()` and `aboutFooter()`, the empty strip at the bottom of the About box);
  more settings (`host.add_settings(folder, visible=...)`, the same files as `data/settings/`; the characters of a setting
  that is switched off are hidden with it); and a check that runs before any character is saved, rolled or drawn
  (`host.add_guard(fn)`: return a reason to refuse it, and a check that crashes refuses too); and its own way to draw
  portraits (`host.add_portrait_engine(fn)`: `fn(npc=, setting=, prompt=, size=, seed=, comfy=, cancel=)` returns `None` when
  the portrait is not its own, `{"png": bytes, "engine": "name"}` when it drew one, or raises `host.PortraitFallback(reason)`
  when it cannot run right now, so the built-in engine draws it and the reason is kept with the picture and shown on the
  job). `local/` is git-ignored, so an extension stays on your machine. A broken extension is skipped with a message and never stops the server. More in [extensions.md](extensions.md).
