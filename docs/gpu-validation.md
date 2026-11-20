# GPU visual validation — 2026-09-22

This is a visual review of real Mage-Flow output, separate from the automated data and API tests. ComfyUI 0.37.0 ran on an AMD Radeon AI PRO R9700 with the configured Mage-Flow models. Draft settings used 20 steps, 768×768 portraits, 576×864 sprites, and 512×768 animation frames. A passing graph means only that an image was produced; the judgments below come from inspecting the images and their transparent cutouts.

The repeatable generator is `python scripts/gpu_validation.py PHASE --out local/gpu-validation-2026-09-22`, where `PHASE` is `matrix`, `taur`, `taur-detail`, `actions`, `turnaround`, `sprite-fixes`, `sprite-regression`, or `guided-run`. It writes samples and prompt metadata to the folder `--out` names (this example is under `local/`, which Git ignores). The reviewed contact sheets are in that directory. Keep the same seed and model files when comparing a future prompt or model change.

## Representative bipedal matrix

The `matrix` phase uses fixed seeds 9142101–9142105. Each case generated one portrait and one independent full-body sprite. The `matrix` phase only saves the pictures; passing a sprite through NPC Forge's keying path and checking it on a neutral background is what the `sprite-regression` phase does.

| Case | Portrait and anatomy | Sprite and cutout |
|---|---|---|
| Modern, semi-real (9142101) | Clear character portrait | Full figure, matching hair and outfit; cutout usable in this sample |
| Cyberpunk, comic ink (9142102) | Clear portrait | Character pose and outfit read, but the model drew city scenery; black scenery survives keying. **Cutout fails.** |
| Anthro wolf, semi-real (9142103) | Wolf muzzle, ears and fur read | Bipedal body, head and tail read; cutout usable in this sample |
| Pirates, pixel art (9142104) | Clear pixel portrait | Full figure and matching outfit; cutout usable in this sample |
| Zombie outbreak, watercolor (9142105) | Clear portrait | Full figure, but background removal erases pale clothing and leg detail. **Cutout fails.** |

A side and back view of the modern sample were also generated from its front sprite with the current canonical-view edit graph (seed 9142700). Both stayed recognizably the same character. This checks one turnaround; other styles and characters still need their own review.

### Focused sprite follow-up

The comic and watercolor failures above are the initial outputs. I redrew those same characters with the same seeds. A stronger empty-backdrop instruction and scenery negatives removed the comic city's stray ink. For watercolor, a blue key preserved the figure and its neutral clothing better than magenta or green; a few detached paint flecks remain. The exact successful trial prompts are now produced by the comic and watercolor style settings in `data/styles.json` and `npcgen/prompts.py`. One corrected sample per style is evidence of improvement, not a guarantee for other outfits or seeds.

### Fresh-seed sprite regression — 2026-09-23

The `sprite-regression` phase drew two new characters per style at seeds 9143101 and 9143102. Both comic sprites used plain magenta backdrops, had complete figures, and made clean transparent cutouts. Both watercolor sprites drew blue studio backdrops despite the flat-background instruction. The previous hue-only keyer reported success but erased large portions of their blue or gray clothing. Restricting the flood fill to pixels near the measured border RGB restored both figures; the earlier blue-key watercolor sample and the new comic samples still cut out cleanly. The neutral-background previews under `local/gpu-validation-2026-09-23` show small blue ground shadows left below the watercolor shoes, so these outputs are improved but still need cleanup or reroll before polished export. A synthetic same-hue clothing test guards the keyer change.

## Taur morphology

Earlier text-only taur prompts varied between human legs, a separate animal head, and a correct lower body. A new side-view body guide was used as the first edit reference. Three Taur and three Dragontaur seeds (9142201–9142203 for each) all retained one head and four animal legs, but the output mostly repainted the simple guide and lacked finished character detail.

A second trial put a generated portrait after that guide as an identity reference. Three Taur and three Dragontaur seeds (9142601–9142603 for each) retained the intended leg count and gained a more detailed face. The animal body remained stiff, and some animal legs picked up human clothing. These are promising structure experiments, not a production-ready sprite path. Taur portraits and expressions remain available; sprite, animation and model-sheet requests remain blocked. Quadruped and serpentine plans also remain unsupported until they have reviewed full-body paths.

## Experimental action clips

The `actions` phase used the modern semi-real sprite, a generated side reference, and the same edit graph/prompt builder used for word-guided action frames. It drew all four right-facing frames of each clip, at seeds 9142400–9142403. This was direct ComfyUI visual QA of the frame path; it did not run full app assembly or test other directions.

| Clip | Visual review |
|---|---|
| Run | **Fail:** the legs do not alternate clearly and the requested airborne frames stay grounded. |
| Attack | A strike reads, but the windup and impact repeat an extended arm. Keep experimental. |
| Hurt | **Fail:** recoil is unclear and one frame has an extra arm. |
| Cast | Gesture reads, but the effect appears before the release and changes across frames. Keep experimental. |
| Death/fall | **Fail:** all frames are already prone and several are cropped; there is no standing-to-fall sequence. |

These findings are stored as `quality_note` in `data/animations.json`, exposed by the API, and shown beside generated clips. A clean automated pose report should not be read as a visual pass for an experimental clip.

A research trial gave run frames drawn pose puppets as a first reference, as the walk cycle does. It produced airborne frames, but the two contact poses still read as the same leading leg and the hairstyle changed. The shipping run clip stays on the existing word-guided path and remains experimental.

On 2026-09-23, a second guided-run trial used different landing and push-off guides and named the source character's short silver haircut. Four frames preserved the haircut and two airborne frames, but the model again collapsed both contact frames and both flight frames to almost identical silhouettes. Contact/flight leg-mask overlap between opposing frames was 0.78/0.74 after alignment. An explicit push-off prompt still repeated the stance and added an extra hand. The guided-run path is therefore still research only. Four-frame side run reports now flag this specific repeated-phase failure when both opposing pairs have high leg-silhouette overlap.

## App path

An isolated NPC Forge process using the current source reported API v8 and completed a draft portrait plus transparent independent sprite job through `/api/generate` with both steps marked done. A Taur sprite request returned HTTP 400 with the morphology explanation. This used a separate temporary library, leaving the user's running API v5 instance alone. Restart the main NPC Forge process to load the current source and its updated UI/API.

## Remaining quality work

- Check the improved comic and watercolor sprite prompts on more settings and outfits. Two fresh comic seeds passed; two fresh watercolor seeds needed the keyer correction and still have small ground shadows.
- Improve the action poses, especially run, hurt and death, then redraw complete clips in multiple directions and on more than one character.
- Turn the taur body-guide experiment into a production-quality full-body path before enabling it. Review portrait and full-body paths separately for any other morphology added later.
