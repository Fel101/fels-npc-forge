# Status: what has and has not been checked

The **Voice Designer**, **Scenes** and **Creatures** tabs are marked **Preview** in the app: each was run end to end on this
machine, but on far fewer characters than Create, Library and Items, and their own docs ([voices](voice-designer-tab.md),
[scenes](scene-studio.md), [creatures](creatures.md)) list what is still rough. Voices come from Voice Lab, which shows each
model's licence before you use it ([voices.md](voices.md)).

Checked by running it (AMD R9700, Standard quality): portraits, sprites and expressions in the first nine styles, including
a one-character audition of five of those (concept art, stylised 3D render, comic ink, anime, chibi); 4-direction, 4-frame
walk cycles (12 frames plus 2 reference views, about 8 minutes) on a semi-realistic and a photorealistic character
(pose-guided) and, before that rework, on a pixel-art one, plus a one-direction walk on a fantasy character in each of
those five styles and, separately, in each of the four newest styles (8-bit pixel art, voxel art, watercolor storybook,
manga); a breathing idle; the exports above; a freeform character; cast import; batch and cancel; the web app in light,
dark and phone widths; model sheets in the painterly, cartoon, pixel-art and photorealistic styles, plus four of the
round-3 styles that reached a model sheet. One portrait was also drawn in each of seven of the eight newest settings
(gothic horror, noir detective, superhero, adventurers' guild, pirates, Nordic saga, zombie outbreak); the anthro
setting got six, one per species archetype (see below). The six settings added after that (witches and warlocks,
space program, steampunk, dystopian state, alien species, mythical creatures) got 21 portraits between them, most of
them for the alien species and mythical races, and the four styles added with them (cel-shaded outline, fairy-tale
illustration, retro sci-fi pulp, dark gothic painting) each got a portrait, a sprite and a one-direction walk
([animation.md](animation.md), round 5). Core automated tests cover the rolling, prompts, cut-out,
library, export, model sheets, walk poses, HTTP API, and browser module loading (they need no GPU).

Known limits, honestly:

- The walk is drawn from a pose puppet (see [animation.md](animation.md) for the full per-style numbers, which
  have had a few surprises). Eight of the seventeen styles -- pixel art, flat cartoon, 8-bit pixel art, watercolor
  storybook, manga, cel-shaded outline, retro sci-fi pulp and dark gothic painting -- lose the puppet and fall back to
  pose words, which give a weaker walk (cel-shaded and pulp still walked cleanly with words; gothic slides). The other
  nine, including voxel art and fairy-tale illustration, keep the puppet. That was verified on a
  semi-realistic, a photorealistic and (one direction only) a fantasy character. The 8-frame walk was tested only with a
  stand-in. Run, attack, hurt, cast and death/fall now exist as experimental clips. A four-frame right-view GPU sample of
  each found motion and anatomy failures, especially in run, hurt and death; see [gpu-validation.md](gpu-validation.md).
- The four newest styles (8-bit pixel art, voxel art, watercolor storybook, manga) were checked on one character, one
  direction only. The eight newest settings (gothic horror, noir detective, superhero, adventurers' guild; pirates,
  Nordic saga, zombie outbreak; and anthro) were checked by the automated tests (every job has an outfit for every
  gender, no duplicate ids, hundreds of seeds rolled) and by drawing at least one portrait each; none has had a walk
  or model sheet drawn yet. Roll a handful more before relying on any of them for a real project.
- The newest settings, measured on one portrait each unless said: in the mythical setting the merfolk tail, fairy
  wings, troll, gorgon (snakes for hair) and high fae came out as worded; the minotaur first came out as a person with
  small horns and cow ears, and after its wording was changed to "a bull's head in place of a human face" it drew a
  full bull's head in 3 of 3 portraits; the harpy got wings on its back as well as arms; merfolk, centaurs and naga have body plans without two legs, so they get
  portraits and expressions but no sprites, walks or sheets (random full-body batches skip them), like the taurs below.
  Among the aliens the grey, insectoid, crystalline, gel and reptilian species read unmistakably; the three-eyed
  species' third eye is small. Space-program suits may carry a plain striped patch that reads a little like a flag.
- The anthro setting marks Taur and Dragontaur as taur body plans. Earlier tests returned bipedal sprites, and new
  ComfyUI prompt trials remained inconsistent: some produced extra human legs or an extra animal head. A drawn body
  guide improved the leg count across six more Taur/Dragontaur samples, but the figures were still stiff and sometimes
  put clothing on animal legs. NPC Forge allows portraits and expressions but blocks new taur sprites, animations, and
  model sheets until a production-quality path is reviewed. Random anthro full-body batches default to bipedal species.
  See [gpu-validation.md](gpu-validation.md) for the new samples and [animation.md](animation.md)
  for the original visual checks. Other anthro species default to bipedal; one wolf anthro sprite was GPU-sampled.
- A September 2026 draft matrix exposed a comic sprite with city scenery and a watercolor sprite that lost detail
  during magenta background removal. Same-seed redraws improved both: comic sprites now ask for an isolated, empty
  backdrop, and watercolor sprites prefer a blue key. These improvements have one reviewed sample each; check new
  outfits and seeds before relying on their transparent output. See [gpu-validation.md](gpu-validation.md).
- The back view is the weakest direction: hair and clothing details can drift between frames. The report flags it;
  `--best-of 3` draws several candidates per frame and keeps the best.
- Editing a shoulders-up portrait into a full body can lose unusual features. In a test a teapot-shaped head became a
  blank white one, which is why freeform characters draw their sprite from the description (`--sprite-mode auto`).
- The semi-realistic style drifts toward rougher, older-looking skin when edited.
- Model sheets: the feet and the back of the hair are the weak spots (each view can be redone alone); the A-pose is
  modest; only 576x1152 panels were tested at length; identity across views is judged by eye. Numbers in
  [model-sheet.md](model-sheet.md).
- Voices in 1.0, before they moved to Voice Lab (September 2026, R9700, Windows/ROCm): all four models were run through NPC Forge's own workers (Qwen design and
  clone, Breeze design and mood direction, Kokoro stock, Chatterbox clone) and the designer was driven in a browser on a
  scratch copy of 11 characters: takes, picking, try a line with a mood, mood previews, stock voices, voiced dialogue, the
  Voice pack and lip sync on a living portrait. A batch of 10 characters (3 Qwen takes each for the 9 without a voice, and
  every one's 12-line dialogue pack: 156 steps) took 7 min 40 s, about 46 s a character, so a library of about 500
  characters would take about 6 hours. A small Whisper model heard 3.3% of the words in those 132 lines wrong, mostly its own
  word splits ("sun sets", "school teacher") and a name. Not yet checked: sharing the GPU with a big picture engine that is
  running at the same moment (the unload order is unit-tested only), the CUDA install path, and how the voices sound to
  anyone but their maker: that is what the Review page is for.
- The job queue is saved in `.npcforge-jobs.json` inside the library. On restart, queued and interrupted jobs resume from
  their first unfinished step; completed steps stay completed. Failed or cancelled jobs can be retried from the Queue.
  Keep one NPC Forge process open per library so only one worker can recover and write its jobs at a time.
