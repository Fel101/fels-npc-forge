# Adult features (18+)

Sections of NPC Forge for adults only: the **Adult studio** (make and curate adult-level pictures of a character and perfect it
before it goes into [Altera](https://github.com/Fel101/Altera), the companion app) and the **Models** setting (a character builder
for adult women, for Altera). They ship with NPC Forge as an extension in this folder and stay **off and out of sight** until you
switch them on. NPC Forge itself only has generic hooks (see "Local extensions" in its README); delete this folder and NPC Forge
runs without them.

Hiding them is for tidiness, not security. What keeps them safe are the rules in code ([below](#the-rules-the-same-as-alteras)):
adults only, nothing that reads as a minor, no real people, nothing non-consensual, at every level and whatever is typed.

## Unlock it

Both are **hidden** until you tick a box that is not easy to find:

1. In the left panel of the Create tab, press **About** (bottom).
2. In the lower right of the About box there is a faint **π**. Press it.
3. A small terminal types its way to **ACCESS GRANTED**, then shows **18+ I am 18 or older**. Tick it: the **Models** setting
   (in the Setting card) and the **Adult studio** tab appear at once, and the box shows the adult options (the picture engine for
   each level, the engine for a Models character's main picture, the quality, the optional age-check model, and a button into the
   studio). Untick it and they disappear again (a Models character that is open is closed; saved ones stay, hidden).

The tick is stored in `local/adult_studio.json` (`confirmed_at`), so it survives restarts and updates. Restart NPC Forge once
after updating: extensions are loaded when the server starts. The adult features are not offered to scripts or agents through
NPC Forge's MCP connection; Altera reads what the studio keeps straight from the library folder.

## The Adult studio

1. Start NPC Forge (`start.bat`) and unlock it as above. The **Adult studio** tab appears next to Create and Library.
2. Pick a character. It must be **18 or older** and have a **portrait**; text that describes a minor or a real person is refused.
   Above the list are a search box and the Library's drop-downs (ethnicity, heritage, hair colour, eye colour, age, build, art style,
   setting, whether it has kept pictures, whether it is usable): they only narrow the list, whatever character is open. Above the
   pictures of the open character you can show one level or only the kept (or not kept) ones.
3. **Perfect the look.** Under **Body** choose the bust size, shape, areola size and areola colour, and under **Notes** write
   anything else ("athletic build, defined waist"). Every character starts with options of its own (worked out from its id, the
   build and the skin tone, so they differ from one character to the next), and they are saved when you change one or make the
   first picture, so they never shift under later pictures. Bust size and shape are added to every picture, the areolas only to
   Explicit ones (where they show), and all of it travels to Altera with the import so Altera's own pictures match. Change something,
   redo, compare, repeat: pictures made before a change are marked "old body", and suggestive and explicit ones made before the full-length frame
   existed are marked "old framing"; "Redo outdated" remakes them in one click (a redone picture has to be kept again). To change the face,
   make a new portrait in NPC Forge: pictures made from the old portrait are marked "old portrait".
4. **Make pictures.** Tick scenes (from `scenes.json`, edit it freely) or type your own, then "Make N pictures". Each is an edit
   of the portrait, about 35-50 seconds on your GPU. Suggestive and Explicit pictures are made as **full-length photographs**
   (a taller 832x1216 picture): the portrait is a head-and-shoulders crop and the edit copies its framing, so without that
   they ended at the collarbone and ignored poses such as "seen from behind". Add `"frame": "auto"` to a scene in `scenes.json`
   to keep the portrait's closer crop for it.
5. **Curate.** Pictures are blurred until clicked. **Keep** the good ones, redo or delete the rest.
6. **In Altera:** Settings > Avatar > Choose or import an avatar. Import (or Update) the character, then "Ready-made pictures"
   lists what you kept: import them (each is age-checked again). Altera then sends them instantly, and she is told which ones exist.

## The Models setting

A fifth choice in the **Setting** card (after Old West), for building the avatar you want for Altera. It rolls like the others (Who card:
age group, persona, heritage; lock the fields you like; edit anything) and **Describe** works too ("a tall redhead with green
eyes, freckles, athletic"). Picking it switches the art style to **Photorealistic**.

- Everyone is a **woman aged 18 or older** (the floor was 21 until 2026-09-20). The setting file (`settings/models.json`) states `genders`, `min_age` and its own
  age groups (mostly twenties), and NPC Forge enforces them when rolling, when you edit the age or gender, and when a cast
  file is imported.
- **Heritage** is the setting's own list of 14 (Caucasian, Scandinavian, Eastern European, Mediterranean, Japanese, Korean,
  Chinese, Southeast Asian, Indian / South Asian, Black / African, Latina, Brazilian, Middle Eastern / North African, Mixed),
  each with its own skin, hair and eye tables and its own first names and surnames (so a Japanese roll gets a Japanese name).
  **Hair colour** is a choice in the Who card (blonde, golden, platinum, strawberry, light brown ... black, auburn, copper red,
  red, silver): it wins over whatever the heritage would roll, so a blonde Japanese woman is one click. Both are `heritages`
  and `hair_color_choices` in the setting file; the rest of a roll does not change when you pick a colour (same seed, same
  person, other hair).
- **The portrait wording** is the setting's own too (`style_text.photoreal`): a fashion-editorial photograph of a poised adult
  woman with natural skin texture, professional makeup and soft studio light, and a soft confident half-smile as the default
  expression, instead of the plain "natural skin texture, studio lighting" sentence that made ID-photo faces.
- Everyone is **beautiful by default**. Every roll gets a beauty look in the *Distinguishing features* box (a high-fashion
  supermodel, a fairy-tale princess, Hollywood glamour, a radiant natural beauty...) and glamorous hair (glossy waves, sleek
  lengths, big curls) instead of the plain look the shared tables give. Measured: the same character drawn plain, then with
  each look, changes clearly. Type your own words in that box to steer it ("a fairy-tale princess with green eyes"); the
  fairy-tale wording is deliberately adult ("regal", "refined"): copying a film princess's youthful features would bring back
  the childlike look the rules and the age check refuse. Glamorous jobs (fashion model, actress, singer, presenter, stylist,
  fitness model...) are the likeliest and their outfits are polished; aprons, scrubs and uniforms are rare.
- Everyone is dressed in **ordinary clothes**. The portrait is the reference the studio's pictures are made from (and it stays
  the identity Altera uses), so the tables hold nothing revealing; a test scans every entry.
- Text is checked by `models.py` before a Models character is saved, previewed, rolled or drawn: Altera's hard rules (a minor or
  childlike detail, a real person's likeness, anything non-consensual). The setting's `negative` prompt (the same words the
  studio always adds) keeps youthful faces out.
- Edit `settings/models.json` freely (personas, builds, hair, outfits...). A test fails if a roll trips the rules, or an entry
  sounds young or revealing, so keep "girl", "petite" and the like out.
- Switched off with the box: the setting is not listed, its characters are hidden from the Library and cannot be drawn.

## The rules (the same as Altera's)

`safety.py` holds the same hard rules as Altera's `core/safety.py`: characters under 18, any text about a minor or childlike
detail, real people and anything non-consensual are refused, whatever the level or the scene. With `NPCGEN_ALTERA` set, a test
fails if a rule here is no longer one of Altera's, so bring this file in line whenever Altera's rules change. `scenes.json`
cannot loosen any of it.

Levels are filing labels: `off` (everyday), `romantic`, `suggestive`, `explicit`. Altera only sends a ready-made picture when its
adult mode is at that level or higher, and refuses to import one whose description needs a higher level than it is filed under
(for example "lingerie" filed as romantic). `scenes.json` has 16 scenes for each level (64 in all), and a test checks every one
against Altera's rules, so anything you add there is checked the same way. The explicit scenes are solo poses and nudity: for
anything beyond that, add your own lines to `scenes.json` (a unique id, a label, a level, the text) or type one in "Your own scene"
in the studio. Up to 16 pictures can be made at once; each level has a **Select all / Clear** link.

## The picture engine

Three engines draw the pictures (`engines.py`), chosen in the studio under **Picture engine** (Everyday and Romantic, and
Suggestive and Explicit, are chosen separately; your own choice is kept in `local/adult_studio.json`):

- **Qwen-Image-Edit 2511 + Lightning** (the default for Suggestive and Explicit). Measured against Mage-Flow-Edit on the same
  characters and scenes: real whole bodies, natural poses, detailed rooms, sensible hands and the same face, in about 35 seconds
  a picture. It is a workflow file, `workflows/qwen_edit_2511_lightning.json` (the same file Altera uses), and needs four files in
  ComfyUI, which the studio checks before every picture and names on the page when one is missing:
  `diffusion_models/Qwen-Image-Edit-2511-FP8_e4m3fn.safetensors`, `text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors`,
  `vae/qwen_image_vae.safetensors`, `loras/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` (lightx2v, Apache 2.0).
- **Qwen + realism LoRA** (optional, off unless you pick it): the same workflow plus `prithivMLmods/Qwen-Image-Edit-2511-Ultra-Realistic-Portrait`
  (Apache 2.0; `loras/Qwen-Image-Edit-2511-Ultra-Realistic-Portrait-URP_20.safetensors`, 236 MB) at strength 0.5,
  `workflows/qwen_edit_2511_lightning_realism.json`. Measured on two scenes (a silk robe, a nude figure study) with one character, same seed:
  clearly more photographic skin, fabric and light, and a more natural pose; at 0.7 the frame pulled closer and cropped the feet, at 0.5 it
  did not. Not tried on more characters or scenes, so it is a choice and not the default. As a *portrait refiner* (Mage-Flow portrait ->
  Qwen edit + LoRA) it gives a magazine close-up with real skin texture but changes the light, the backdrop (dark studio) and the
  framing, so it is not used to make reference portraits.
- **Mage-Flow-Edit** (the default for Everyday and Romantic): smaller and faster, weak at whole bodies and hands.

**Qwen runs the careful way.** It is a 20 GB model plus a 9 GB text encoder. On a 32 GB GPU with 62 GB of RAM, left loaded between
pictures, the RAM crept up until Windows paged and the whole PC froze (it happened twice while this was built, once with ComfyUI
started with `--highvram`, which keeps every model in GPU memory: that flag was taken out of Comfy Desktop's launch arguments).
So ComfyUI is flushed (every model unloaded) before and after each Qwen picture, a picture does not start without about 36 GB of
free RAM, and while it runs the time, the VRAM and the free RAM are watched: RAM under 8 GB free, GPU memory full for 30 seconds,
420 seconds or a silent ComfyUI cancels it at once (interrupt + free). Then **that picture is made with Mage-Flow instead**,
marked with an orange "Mage-Flow" badge whose tooltip says why, so nothing is lost. Measured: VRAM peaks at about 29 GB and the
free RAM never fell under 16 GB. Close big programs (or unload the chat model in LM Studio) if it keeps falling back.

Body options land differently per engine: Qwen draws a fuller bust than Mage-Flow for the same word, so go one size down.

### The main picture of a Models character

The portrait every other picture starts from has its own choice in the same card. NPC Forge has a generic hook for it
(`add_portrait_engine`); `engines.make_portrait_engine` answers for the Models setting only and falls back to the built-in
Mage-Flow (with the reason stored on the picture and shown on the job) when files are missing or the guard trips.
Measured on six adult characters (blonde Caucasian, Japanese, Korean, Black, Latina, redhead), the same prompt and seeds,
1024x1024, checked by Altera's own age check:

| Model | Look | Time | Reads as |
|---|---|---|---|
| **Z-Image Turbo (the default)** | the most "model" faces, varied, natural skin, fastest | 16 s (20 GB VRAM, careful mode) | **19-23 with the glossy wording** (see below) |
| Qwen-Image 2512 + Lightning | filmic, natural, adult, varied faces | ~30 s (28 GB VRAM, careful mode) | 27-32, in every case with the larger check model |
| Mage-Flow (the fallback for both) | glossy glamour, faces look alike | ~20 s | 30 |
| Krea 2 Turbo (not offered) | photographic but plain and older, even with fashion wording | 20 s | 24-32 |
| FLUX.2 dev | not tried: 33.8 GB of weights + a 33.9 GB text encoder do not fit this PC | | |

**Z-Image Turbo is the default because it makes the prettiest, most model-like faces (the author's choice, 2026-09-20).** Measured
with the first, glossy wording, its faces read as about 20 to Altera's age check, so a portrait can read under Altera's minimum age and
Altera then refuses to import it (draw it again with another seed, or pick Qwen-Image 2512). Asking it for "mature" overshoots to the
late thirties. The wording now in `models.json` (natural skin, "poised adult woman") looks like women in their mid-to-late twenties
by eye, but its age-check readings were NOT measured. Z-Image runs at cfg 1, which ignores negative prompts, so the youth words of
the setting's negative do nothing there: the adult wording in the positive prompt and the age check are what hold.
Needs `diffusion_models/z_image_turbo_bf16.safetensors`, `text_encoders/qwen_3_4b.safetensors` and `vae/ae.safetensors`; a picture
does not start with under 32 GB of free RAM.
Qwen-Image 2512 uses the same text encoder and VAE as Qwen-Image-Edit. It needs
`diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors` and `loras/Qwen-Image-Lightning-4steps-V2.0.safetensors` besides the
encoder and VAE above (36 GB of free RAM to start). Changing the engine only affects portraits drawn from then on: draw a
character's Portrait again to switch it (the studio pictures made from the old portrait are marked "old portrait").

## The age check (optional here)

Set a vision model's name in the studio (LM Studio, `http://127.0.0.1:1234/v1`) to check every picture as it is made; one that
reads as under 18 is discarded. Blank = pictures are marked "unchecked" here. **Altera checks every picture again on import** and
refuses Suggestive and higher without a passing check, so an unchecked picture can be kept but cannot be used at those levels
unless Altera's check passes.

## What is where

```
<character>/adult/items.json, studio.json, img/<item>.png     what the studio writes, next to the character
                                                               (studio.json: notes, body options, body_text[_explicit] for Altera)
local/adult_studio.json                                        the 18+ tick, quality, age-check model
extensions/adult_studio/                                       this extension:
    studio.py  body.py  engines.py  safety.py  scenes.json     the studio (page, body options, picture engines, rules, 64 scenes)
    workflows/qwen_edit_2511_lightning[_realism].json  web/    the Qwen edit workflows (the first is also Altera's), the page
    workflows/zimage_turbo_portrait.json  qwen_image_2512_portrait.json    the Models main-picture workflows
    settings/models.json  models.py                            the Models setting and its guard
    ui/gate.js  gate.css                                       the π, the box it opens, the 18+ box and the options
```

## Tests

```bash
python -m pytest extensions/adult_studio/tests     # needs no GPU; part of the full run
```

Two of them compare the studio with Altera when `NPCGEN_ALTERA` names Altera's folder (its rules, and that Altera imports the
starter scenes at the level they are filed under); without it they are skipped.

## Limits

- Mage-Flow's own terms say "research purposes only". Measured on adult solo nudity and topless scenes (2026-09-20): it draws
  whole bodies once the picture is full-length, and the bust and areola options visibly change size, shape and colour. What
  still goes wrong: scenes with a phone or a mirror selfie (the outfit of the portrait tends to stay on), hands and feet (Redo
  gives a new seed), and water, foam or blankets, which hide what the scene asks for. Qwen-Image-Edit (above) is much better at
  all of it (about 25 pictures of two adult characters over 11 explicit and 3 suggestive scenes: none came out clothed when the
  scene said nude and the anatomy held; in a few the frame crops the hands or the head) and is the default where it matters. Neither knows sex acts and this studio has no scenes for them. Not tried: the SDXL nude checkpoints (afroditexl,
  Pony) and PhotoMaker for identity; Qwen made them unnecessary.
- The body options and notes are words added to the prompt: they steer the figure, they do not sculpt it, and they are the
  only "consistency" there is. Measured and rejected: giving a nude picture as a second reference (it exaggerated the bust,
  drifted the face and took half as long again), and extra anatomy words in the negative prompt (no visible change).
- The word lists behind the rules are heuristics, and a vision model's age estimate is an estimate: measured on the same
  pictures, the small Gemma E4B gave 20 for a clearly adult Black woman where the larger Qwen 3.8 27B said 27 (and the
  glossy "flawless skin" wording pulled estimates down further). Altera's check uses the chat model unless
  `imagegen.age_check.model` names another; if a character keeps being blocked, point it at a larger vision model.
