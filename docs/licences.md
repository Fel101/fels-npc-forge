# Which models may be used for work you sell

NPC Forge draws, edits, voices and builds with models other people made, and each model has its own licence. Some allow commercial use, some forbid it, and one (the built-in Mage-Flow, which was the default until 2026-10-13) says two different things. The program keeps **one list** of them, `data/licences.json`, and shows what it says everywhere you choose a model. This page says what the list holds, how a rating is worked out, how a character gets one, and how to keep it current. None of it is legal advice; the list says so too.

## Where you see it

- **A tag after each model's name** in every drop-down that offers one (Models and options: Image, Edit, 3D generator; the voice model in "What to make"; the voice designer's model, speaker and batch pickers): `Z-Image Turbo · commercial OK`, `Qwen Image 2.1 · non-commercial`.
- **A note under the picker** for the chosen model: a coloured badge, its licence and a line on what that means, and a **Details** button that opens the full entry.
- **Licences of every model**: the button in Models and options and in About. Every entry with its licence, a plain summary, what NPC Forge uses it for, the links to its page and its licence text, and the date it was read. With a character open, its badge opens the same page with a report on that character first.
- **A badge on every character** (next to its style): the rating of what made it. Click it for the report: which models made what, each one's rating, and what could not be told.
- **The Library**: a small coloured mark on each card and a **Commercial use** filter that counts how many characters have each rating. The Licences page also sums up your whole library.

## The four ratings

| Rating | Meaning |
|---|---|
| **Commercial use allowed** (✓) | The licence puts no limit on commercial use beyond keeping its notice (Apache-2.0, MIT, BSD, CC0). |
| **Allowed, with conditions** (!) | Commercial use is allowed, but the licence adds conditions you must read: where it applies, how many users, forbidden uses. |
| **Not cleared** (?) | The licence text and the authors' own statements disagree, or it depends on something the program cannot know. Do not rely on it for work you sell. |
| **Non-commercial only** (✕) | The licence allows research, evaluation or personal use only. Selling work made with it needs a separate licence from its owner. |

## How a model is rated

Each entry in `data/licences.json` has its own `status` (what its page and licence file declare) and may `use` other entries. A model is only as free as the models it uses: its **effective** rating is the worst of its own and theirs. Pixal3D is MIT, but it reads DINOv3 features, and DINOv3 has its own conditions, so Pixal3D (and Trellis2, which loads Pixal3D) shows "with conditions".

An entry is a **creator** (what it makes is part of a character: a picture, a voice, a 3D shape, animation frames) or a **helper** (it only measures or checks: SDPose finds joints, SAM finds the hair, BiRefNet cuts a figure out, Whisper counts wrong words). A helper is shown, but it only changes a character's rating when it is not cleared or non-commercial.

## How a character is rated

`licences.check(npc)` reads what the character's records say made each thing:

- every picture records the label of its engine (`assets.portrait.engine`, the sprite, each expression, each model-sheet view, and the animation frames' `engines`). **No label means Mage-Flow drew it**, and so does a picture with an `engine_note`: that note means the chosen engine could not draw it and Mage-Flow did instead;
- the 3D model records its generator (`engine_id`), whether it was imported or is a designed body, the upscaler a sharper face used, and the helpers behind the clean-up steps that ran;
- the voice, its reference and every spoken line record their engine.

The character is rated by the **worst** of the models that made anything of it, and the report lists them worst first. Two things are said openly rather than guessed: something the program cannot tell (animation frames made before the engine was recorded, an engine this list does not know) is listed under the report as unknown and keeps the character from being rated better than **Not cleared**; a character with nothing made yet has no rating at all.

The rating is part of each Library row. It is worked out again whenever the character is saved (so it follows a new picture or model the moment it is made) and for every character when `data/licences.json` is edited (no restart), so the filter and the badge never disagree with the report.

## The models and the answer to the usual question

| | Rating | Why |
|---|---|---|
| Z-Image Turbo, Qwen-Image 2512 (+ Lightning), Qwen-Image-Edit 2511 (+ Lightning) | commercial use allowed | Apache-2.0 |
| **Qwen Image 2.1** and Qwen Image 2.1 Edit | **non-commercial only** | Qwen Research License |
| Mage-Flow and Mage-Flow-Edit (built in; not the default since 2026-10-13) | **not cleared** | the licence file is MIT; Microsoft's README says research only, not for products or services |
| Hunyuan3D v2.1 | with conditions | not licensed in the EU, UK and South Korea; a very large product needs Tencent's approval |
| Pixal3D, Trellis2 | with conditions | MIT, but they read DINOv3 (Meta's licence: no military or weapons uses) |
| A 3D model you import | not cleared | it keeps whatever licence it came with |
| Qwen3-TTS, Kokoro, Chatterbox, VoxCPM2 | commercial use allowed | Apache-2.0 / MIT |
| **Breeze TTS 2** | **non-commercial only** | BreezeBlue Research and Non-Commercial License |
| RealESRGAN (the default upscaler) | commercial use allowed | BSD-3-Clause |
| **4x-UltraSharp** | **non-commercial only** | CC BY-NC-SA 4.0 |
| The animation library and the Body Designer's body data | commercial use allowed | CC0 |

**Is Qwen fine as the default for commercial work?** Yes, and since 2026-10-13 it is the default. Qwen-Image 2512 draws and Qwen-Image-Edit 2511 edits (both Apache-2.0), with no quiet Mage-Flow fallback: a picture that Qwen cannot draw (memory short, a file missing) fails and says why, instead of Mage-Flow drawing it and the character being rated by Mage-Flow too. Qwen Image **2.1** is a different model with a non-commercial licence: do not pick it for work you sell. Mage-Flow is the one that is not cleared, and it is still used in two places:

- when you pick it in Models (the picker says so), or switch on **Let Mage-Flow draw when the chosen model cannot** (`engine_fallback`);
- by anything that names it, such as `--image-model mage`.

The Scenes tab's 3D stage backgrounds follow the same choice as every other picture (Qwen-Image 2512 by default, since 2026-10-14; they used to be always Mage-Flow), and a scene's picture is marked with the engine that drew it.

A PC that has not the Qwen files, or wants the lighter Mage-Flow back for everything, says so once in `config.json` (the setting is described in [game-clients.md](game-clients.md)); the page then follows it as well, because the server publishes its defaults to the page:

```json
{ "options": { "image_model": "mage", "edit_model": "mage", "engine_fallback": true } }
```

A browser that saved the old defaults (Mage-Flow for both, fallback on) is moved to the new ones once, the first time it loads the page; a model chosen on purpose is kept.

## Keeping the list current

Licences change and models are added. The list is the only place to edit:

1. Add or change the entry in `data/licences.json`: `id`, `kind` (one of the groups the file names), `role` (`creates` or `helper`), `name`, `licence` (the name as the page writes it), `status`, `short` (what it means in a few words, **without** repeating the licence name: the page shows `licence · short`), `summary` (the plain version), `used_for`, `links`, `licence_url`, `checked` (the date you read it), and `selectors` (what in the program reaches it: `image:<id>`, `edit:<id>`, `three_d:<id>`, `voice:<id>`, `upscaler:<file>`, `feature:<name>`). Put `uses: [ids]` on an entry that depends on another.
2. Read the licence text itself, not a summary of it, and write only what it says. If the page and the licence file disagree, the status is `unclear` and the summary says how.
3. Run `tests/test_licences.py`. It fails when a model a picker offers (an image or edit engine, a 3D generator, a voice engine) has no entry, when an entry is incomplete, and when the voice engines' own commercial flag and the list disagree.

## What it does not cover

It is what each model's own page or licence file declares, read on the date shown. It does not cover the law of your country: whether pictures an AI made can be protected by copyright, what a lookalike of someone else's character can cost you (Z-Image drew Jack Sparrow every time for a long-haired pirate captain, so look at its pictures for lookalikes), or the photo of a real person you give the photo feature (only use photos you have the right to use, of people who agreed). ComfyUI and Blender are GPL-3.0 programs NPC Forge runs; their licences cover the programs, not what they make.

## For scripts

- `GET /api/config` has `licences`: `{checked, disclaimer, statuses, kinds, notes, models: [...], by_selector}` with each model's `effective` rating.
- `GET /api/npcs/<id>/licences` is the report on one character: `{verdict, label, models: [{id, name, kind, role, licence, status, effective, short, used_for}], unknown, checked}`.
- Every row of `GET /api/npcs` has `commercial`: `yes`, `conditions`, `unclear`, `no` or `none` (nothing made yet).
