# Making characters

## Three ways to make a character

1. **Roll**: a weighted random person from the tables in `data/` (modern, fantasy, sci-fi, Old West, post-apocalypse,
   cyberpunk, gothic horror, noir detective, superhero, adventurers'-guild, pirate, Nordic saga, zombie-outbreak,
   anthro, witches-and-warlocks, space-program, steampunk, dystopian-state, alien-species and mythical-creature settings,
   632 occupations with outfits). Edit any field and lock the ones you want to keep when you roll again.
2. **Describe**: "a tiny robot chef with a copper teapot head, riveted brass apron and glowing blue eyes". Your words are
   the art prompt. Anything goes: people, robots, creatures. If your words about a person do not say a woman or a man, the
   Who card's Gender leads them ("a female character, ..."), so the picture follows the card; words that say it win, and
   robots and creatures are left alone.
3. **Cast file**: import JSON or CSV. Give any subset of fields (`name`, `age`, `gender`, `job`, `hair`, `outfit`,
   `description`...) and the rest is rolled. Columns we do not recognise travel untouched in `npc.json` under `custom`, so
   your game's own data (schedules, stats, ids) stays attached to the character. An empty cell means "roll it"; write `none`
   in `accessory`, `beard`, `glasses` or `distinguishing` to rule a random one out. A small Old West cast to try is in
   [examples/frontier_town_cast.json](../examples/frontier_town_cast.json).

In the Library, select NPCs and choose **Create from selected** to group them into a cast pack, or use **Import as pack**
to create the NPCs and pack together. Packs can record a roster type (party, guild, town or ship crew), shared context,
member roles and notes, optional distinct-role validation, uniqueness-rule guidance, relationship links, rumors and plot hooks.
**Suggest roles** fills blank roles from the selected roster type. Enable **Require a different role for each member** to
reject duplicate named roles on save, ignoring capitalization. Free-text uniqueness notes remain guidance and do not
change NPC generation or validate profile data. Export a pack to get one ZIP
with `cast-pack.json` plus a folder and `npc.json` for each member; NPC custom fields stay in those individual records.
The existing **Import cast** action still imports ungrouped NPCs. To assign a role in an imported pack without replacing the
NPC's occupation, use `cast_role` (or `member_role`) in the cast data.

```json
{
  "pack": {"name": "Lanternwatch", "template": "guild", "shared_context": "A harbor patrol."},
  "characters": [
    {"name": "Ada Vale", "cast_role": "Captain", "occupation": "Harbor guard", "schedule": "dawn-dusk"},
    {"name": "Bo Chen", "cast_role": "Scout", "occupation": "Dockworker"}
  ],
  "relationships": [
    {"from": "Ada Vale", "to": 2, "type": "mentor", "notes": "Old rivals."}
  ]
}
```

For imported relationship links, `from` and `to` can be exact character names or one-based member numbers. If a name appears
more than once, use its member number. CSV packs use the same cast columns and take the file name as the pack name.

```json
[
  {"name": "Ada Vale", "description": "an elderly orc blacksmith with a bone crown", "shift": "dawn-dusk"},
  {"name": "Bo Chen", "age": 41, "job": "plumber", "gender": "m", "hair": "short black hair"}
]
```

## Choosing fields before you roll: Advanced options

**Advanced options…** in the Who card opens every field of the character sheet: name, age, heritage or race, job, build, skin,
hair colour and style, eyes, facial hair, glasses, features, accessory, expression, outfit, traits, hobbies, quirk, goal, pet
peeve, how they talk, how they come across and the bio. Each list is the setting's own table (`GET /api/choices?setting=...`,
`roll.field_choices`): **Random** first, then the entries, then **Custom…** for your own words. Glasses, facial hair, features
and accessory also have **None**. Lists follow each other: the outfits are the chosen job's (for the chosen gender), the skin
tones and eye colours the chosen heritage's or race's. Traits clash like they do when rolled (shy greys out outgoing).

What you choose is kept per setting and sent as locked fields, to **Roll NPC** and to **Generate batch** alike ("ten pirate
captains in the same coat" works). A padlock on the sheet still wins over it. The Who card's own choices (gender, age, job,
heritage, body plan, hair colour) are the same settings, so the card and the popup always agree.

## Personality: how they talk and how they come across

Besides three traits, a quirk, a goal, a pet peeve and hobbies, every rolled character gets **a way of talking** (`profile.speech`, for
example "dry and understated, with a deadpan aside now and then") and **a way of coming across** (`profile.demeanor`, for example
"reserved at first, warming up slowly once trust is earned"). They are drawn from the tables `speech_styles` and `demeanors` in
`data/common.json` (a setting can replace them, like the other tables): each entry lists the traits it suits (`for`) and the
traits it clashes with (`not`), so the same three traits usually give a consistent person. The Who panel has both fields, each with a
dice button that suggests a new one from the current traits (`POST /api/manner`), and a cast file can give them in columns
named `speech` and `demeanor`. They cost nothing when you only want art: the prompts for the pictures do not use them. Game
engines and other tools that read `npc.json` can use them for dialogue and for how a character behaves.
The pronoun placeholders in the tables (`{poss} own jokes`) are filled in, so `goal` and `quirk` read as they should.

## What you get

| Asset | How it is made |
|---|---|
| Data sheet | Name, age, job, looks, personality (three traits, **how they talk**, **how they come across**, quirk, goal, pet peeve, hobbies), bio: `npc.json` (plus your `custom` data) |
| Dialogue pack | Optional local templates for greetings, idle, trade, quest and farewell lines; edit in the app and export as JSON, Ink or Yarn Spinner |
| Portrait | Text-to-image from the description |
| Expressions | Edited from the portrait, changing only the face (15 available) |
| Living portrait | For companion and chat apps: the portrait with only the face blended in from ten expressions, blink and mid-word included, lined up so nothing else moves (below) |
| Full-body sprite | Edited from the portrait for ordinary characters, drawn from the description for freeform ones, on a flat colour and cut out to transparent |
| 3D model | Optional local Hunyuan3D v2.1 shape generation or Pixal3D multi-view textured PBR generation through ComfyUI; saves a GLB and render preview with the character. Optionally, the model can then be cleaned up, painted with the turnaround pictures' own pixels and made into a 100k-face game version (below) |
| Detailed head | Optional: a separate, much finer 3D model of the head alone, from four close-ups drawn from the portrait and Pixal3D multi-view: real eyelids, nostrils and lips where the whole-body model has a mask; its face can be attached to the body (below) |
| Skeleton and animations | Optional: a humanoid skeleton, skin weights and animation clips (Idle, Walk and Wave, or 85 from Quaternius' free animation library) added to the 3D model by Blender on the CPU (below) |
| Animation | Walk: one edit per frame that repaints a drawn pose as the character; breathing idle; aligned, measured, exported (below) |
| Model sheet | Portrait, front, both sides and back on white, one edit each from the sprite; aligned to one height (below) |

**A picture that is drawn again is not lost.** When you regenerate a portrait, a full-body sprite, an expression or a model-sheet view, the one it replaces stays under the new one as an **Earlier version** (the last six of each, `config.json` → `library.picture_history`; 0 turns it off). Under each picture the page lists them with small pictures, each with **Restore** (it becomes the picture in use, and the one it replaces is kept in turn, so you can go back and forth) and **Delete** (removes that version for good). A picture you delete can be brought back the same way, and so can an expression you deleted. Restoring a portrait or sprite marks what was made from the other one (the sprite, expressions, sheet, 3D model) as outdated, the same as drawing it again does. Earlier versions stay in the local library and are left out of game exports.

In the app's **Options**, **Save portrait and sprite rerolls as candidates** goes one step further: a new reroll is saved beside the picture in use instead of replacing it, so you compare first and choose; a saved candidate is listed with the earlier versions and is never dropped to make room.

## Character cards

Open a saved character with a portrait and select **Export character card**. **Download PNG** packages the portrait
and character text for chat apps that read Character Card V3 or V2. **Download CHARX** also carries the character sheet,
current expressions and sprite when available, and living-portrait frames that match the portrait. Both formats record
which models made the included assets and their commercial-use rating.

A preset travels by name and model. For a designed Cast voice, CHARX asks Voice Lab to make an audition and package the
voice. Start the Voice Lab library that holds the voice, or clear the voice checkbox to export without it. Check the
sharing box only if you have the person's permission to include their recording.

**Send to Altera** appears after Altera sets up its inbox. It places the CHARX there for review and import in Altera.
Scripts can use the [card API](api.md#characters) or the [MCP export tool](agents.md#the-tools).

## Living portrait

The **Living portrait** card on a character makes the frames a companion app needs to bring a portrait to life: `blink` (eyes
closed), `talking` (mouth open mid-word), and happy, sad, surprised, laughing, smug, embarrassed, thinking and flirty (the
entries marked `"living"` in `data/expressions.json`). Only the missing or outdated expressions are drawn, then an
assembly step (`npcgen/living.py`) writes `living/<frame>.png` and `living/living.json`.

An expression edit redraws the whole picture slightly (skin grain, fabric, hair) and sometimes a few pixels to one side, so
swapping raw expressions flickers. Each living frame is instead the portrait itself with only the changed part of the face
blended in: the expression is lined up with the portrait (phase correlation), the face is found where all the expressions
changed the picture (weighted towards the upper middle), and only what changed inside it is kept, feathered. Everything else
is pixel-identical across frames. Measured on three characters, one in a satin dress that an edit redraws more than it moves
the eyes: blink and mouth come through, clothes and background stay identical. A first version that looked for the biggest
change anywhere followed the dress instead.

`living.json` records the sha256 of the portrait the frames were made from: an app should use them only while that matches.
A new portrait marks the living portrait outdated. `POST /api/generate` with `"assets": {"living": true}` makes or updates it;
`DELETE /api/npcs/<id>/assets/living` removes the frames (the expressions stay).

## Finding characters in the Library

The Library tab has a search box (it matches the name, job, heritage, hair, eye colour and art-style words, all of them at once)
and drop-downs for setting, art style, gender, ethnicity, heritage or race, hair colour, eye colour, age, build, which images a
character has, whether it has a voice, and whether what made it may be used commercially ([licences.md](licences.md)).
Each drop-down lists only what is in your library, with how many characters it would show given your other
choices, so a choice never leads to an empty page. "Ethnicity" is a broad family worked out from the heritage (Korean and
Japanese are East Asian); "Heritage" is the exact label, and a fantasy race such as Half-orc appears there. The hair colour is
worked out from the hair description ("dark brown wavy hair" is Brown). The logic is in `web/facets.js`, which extension pages
can load too.

**The Trash.** Deleting characters (from the Library, or the delete button on a character) moves them to the **Trash** button at
the end of the Library's top bar. It lists each with its picture, when it was deleted and how many days it has left: **Restore**
brings one back with everything it had (pictures, 3D model, voice; not its place in cast packs), **Delete forever** removes one for
good, and **Empty the trash** removes them all. Characters are removed for good after 30 days (`config.json` → `library.trash_days`;
0 keeps them until you empty the trash).
