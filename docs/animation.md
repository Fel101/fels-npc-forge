# Animation with Mage-Flow-Edit: what we tried and what worked

Goal: turn one character into a game-ready walk cycle (up to four directions) that still looks like the same person in
every frame. Mage-Flow-Edit has no notion of "animation", so each frame is its own edit of a reference image. This page
records the experiments that shaped the pipeline, including the ones that failed, so you can judge (and re-run) them.

The first experiments below ("round 1") come from a **pixel-art test character** (a barista in a green apron) at 576x864, 20
steps, on an AMD Radeon AI PRO R9700. Round 1 produced walks that scored well on the measures and still did not look like
walking, once someone looked at them in the semi-realistic style. **Round 2** (further down) is what went wrong and what
replaced it. All numbers are small samples, not benchmarks.

## Measures

Colour fidelity alone is not enough (a frame can keep its palette and still be a different pose or a different head), so
the pipeline measures three things on the cut-out frames (`npcgen/anim.py`):

| Measure | What it is | Healthy | Flag when |
|---|---|---|---|
| Leg variation | 1 - mean pairwise IoU of the bottom 45% of the silhouette across a clip's frames | about 0.3 for a real walk | below 0.12 (below 0.05 for a puppet-guided walk seen from the front or back; measured static "walks": 0.00 to 0.09) |
| Head stability | mean pairwise IoU of the top 22% of the silhouette, cropped to the head's own width | 0.98 to 1.0 | below 0.85 (a drifting hair shape measured 0.89) |
| Palette coverage | share of a frame's pixels within 40 RGB of the reference palette | above 0.9 | reported, not flagged |

Two measurement bugs are worth knowing about: cropping the head by the *whole figure's* width made arm swing look like
head drift, and a stability score alone rewards frames that are simply copies of the reference. That is why both the leg
and head measures exist.

## Experiment 1: a whole sprite sheet in one image

Mage-Flow supports very wide aspect ratios, so we tried a 2048x512 strip (four cells) and a 1024x1024 2x2 grid, each
edited from a template of four copies of the character, asking for four walk frames facing right.

**Result: does not work.** Two of the four cells stayed front-facing and only nudged their legs (the template pose
dominates), and one had a corrupted eye. Colours were fine (98 to 99% palette coverage), which is exactly why colour
fidelity alone is a poor check. The pipeline therefore generates each frame separately.

## Experiment 2: which references for each frame?

One edit per frame, same seed and prompt style, four frames each. "Front" is the character's standing sprite, "canonical"
is a generated still of the same character in the target view.

| View | References | Leg variation | Head stability | What it looked like |
|---|---|---|---|---|
| side | front only | 0.28 | 0.89 | real walk, but the hair changed (bun, ponytail) between frames |
| side | canonical only | 0.06 | 0.997 | almost identical frames: the reference wins, the pose barely changes |
| side | **canonical + front** | **0.33** | **0.98** | real walk and a stable look: the best of both |
| front | front only | 0.27 | n/a | good walk toward the camera |
| back | front only | 0.23 | n/a | walks, with some clothing-detail drift |
| back | canonical + front | 0.07 | 0.96 | static: all four frames identical |
| back | canonical only, stronger pose wording | 0.09 | 0.99 | crossed legs and colour drift in the shoes |

So the strategy is direction specific (`reference_strategy` in `data/animations.json`): side views use canonical + front,
front and back views use the sprite alone. Re-test if you change the style or the model.

## What the pipeline does

1. Uses the character's full-body sprite as the front view.
2. Generates a canonical side view and back view from it (once; reused). The idle loop and the head-stability measure use them.
3. **Walk:** draws a puppet of each pose (`npcgen/pose.py`) in the character's own colours, and has the model repaint it as the
   character (round 2). Other generated clips (`run`) still use a pose phrase and the round-1 references.
4. Cuts out the backdrop, scales every frame to one figure height (correcting up to x1.6, because generated frames can
   differ in size by 25%), and anchors the feet on one baseline and the torso on one centre line. A walk then gets its
   up-and-down back (see round 2).
5. Writes a report and flags directions whose legs barely move, whose head changes shape, whose frames missed their pose, or
   whose feet never come together and apart.
6. On export, downscales and (for small pixel-art frames) snaps to **one palette shared by the whole clip**. Snapping each
   frame to its own palette gave frames that differed by a shade or two, which shimmers when played.

`left` is a mirror of `right` by default (halving the cost), like most 4-direction sprite sheets.

## Round 2: a walk that did not walk, and a breath that bounced

Both were reported by looking at the result, and both passed the round-1 measures.

### The breath

The idle loop stretched the whole figure by 2.5% and then went through `align()`, which rescales every frame to one figure
height. That erased the vertical motion completely and left the width: in a real idle the figure was 441 px high in every
frame and its width went 138, 140, 138, 138, 140, 144, 145, 144 px. The whole body, legs included, got fatter and thinner
at 1.5 Hz, which looks like a bounce. Now only the upper body moves (head and shoulders rise about 1% of the height, the
ribcage widens about 1.4%, the hips and legs never move), the loop is built on the already-aligned still so nothing rescales
it, and its length is fixed at 1.8 s whatever the frame count.

### The walk, in words

Four side-view walk frames from a semi-realistic character were the same wide stride four times. Words cannot draw the
"passing" pose: the strongest wording tried ("one leg straight and vertical carrying the weight, the other knee bent with its
foot lifted off the ground, the feet close together") still gave a stride with the feet apart. A walk without a passing pose
is a figure sliding along in one stance. The round-1 measure (leg variation, "about 0.3 for a real walk") could not tell:
those frames scored 0.16 to 0.35 because the legs differed a little from frame to frame.

### Steering the pose with a drawing

Semi-realistic test character, 576x864, 25 steps, the same seed unless stated. The puppet is a small 3D skeleton drawn as
capsules (`pose.paint`).

| First image | Second image(s) | Result |
|---|---|---|
| none (words only) | canonical side view + sprite | contact pose fine; the passing pose is a wide stride again |
| grey mannequin | sprite | the pose is followed exactly, but the grey limbs are painted onto the character |
| sprite | grey mannequin | mostly ignored; ghost mannequin limbs |
| grey mannequin | canonical + sprite | the model returns the mannequin |
| **puppet in the character's colours** | **sprite** | **poses followed (toe-off, mid-step, stride); the same character across a cycle within one seed** |
| puppet with the real head pasted on | sprite | merged legs, an oversized head |
| puppet | canonical + sprite | the model copies the puppet as drawn |

The colours matter: what leaks from a puppet in the character's own colours is harmless. The experiments looked good, and the
first runs through the real pipeline then found five more problems. Each one is why some code exists:

| Problem | What was measured | The fix |
|---|---|---|
| A draw comes out **zoomed in**: the figure fills the picture and the feet come together | 2 of 3 seeds were fine; the third filled 93 to 94% of the height and was cut off | "the whole figure, from the top of the head to both shoes, fits inside the picture" in the prompt, and a check that nothing is cut off |
| The **front view is the standing sprite four times** | the legs differed from the sprite by 0.02 to 0.05 (a real step: 0.27 to 0.40). A front pose barely differs from a standing front view, and the sprite is the second image | exaggerated front and back poses (a long step, a raised knee) seen from a 24 degree camera, "caught in mid-step ... the pose changes from the standing picture" for the front view only, and a check that the legs really differ from the standing view |
| A side-view frame **faces the camera** | it was the only frame the check on the feet passed with the legs far apart; its chest was 1.47x as wide as the character's own side view (good draws 0.85 to 1.07x) | a check on the chest width against the character's standing view |
| The **look changes with the seed** | at the job's seed the stride frames came out facing the camera and the passing frames had auburn hair; at another seed all four frames were one black-haired woman. Frames redrawn with another seed looked like a different outfit | one seed for the whole clip. It is chosen on two probe frames, the first stride and the first passing step (a seed that draws the stride well may draw the passing step as a stride again, measured): a seed is kept only if both come out right, up to four seeds are tried, and the other frames use it. Any tone difference left is then pulled toward the character's standing view (a Lab mean and spread match, clamped) |
| The **hair colour drifted** | with a skin-coloured puppet head, black hair came out auburn in half the frames | the puppet's head is drawn with the hair colour read from the character's data (a hat hides it in the picture) and a face. Not the whole cause: the seed is the larger one |
| A stride puppet was **cut off** at the bottom of the picture | the front and back cameras push the leading foot below the edge | the puppet keeps its lowest foot on the floor line |

Tried and dropped: the canonical side view as a third image (either before or after the sprite) made the model paint the puppet's
capsules and its red-brown head onto the figure, for every frame.

Every draw is checked (`guided_score`): the whole figure inside the picture (70 to 96.5% of the height, nothing cut off); it faces
the way the puppet does; in a side view the feet are about as far apart as the puppet's (at least 0.75x as far when the puppet's feet are at least 0.30 of the figure's height apart, and not more
than 0.16 of the figure's height further); in a front or back view the legs differ from the standing view (at least 0.12). A
draw that fails is drawn again with another seed, up to three draws in all, and the best is kept. The first version of the feet
check was too strict and cost a lot of GPU time: frames judged fine by eye were 0.12 to 0.135 off its 0.10 tolerance (a stride
of 0.42 to 0.60 of the height for a puppet's 0.48, "passing" feet 0.27 apart for 0.14), while the real failure, a stride drawn
with the feet together, was 0.20 off. It also wanted a figure 72 to 91% high, but a front view keeps the sprite's own framing and
is 82 to 95% high with the pose right.

What the walk does now: 4 or 8 frames per direction, the sprite as the only identity reference, one seed per clip chosen on the
probe frames (see above), a later frame that fails redrawn with other seeds (up to three draws, the best kept), every frame's
colours matched to the standing view, one step of about 0.8 s (5 fps for 4 frames, 10 for 8), and an up-and-down of the body
(lowest as a foot lands, highest as the legs pass, about 1% of the height) put back after the frames are aligned, because
aligning gives every frame the same height and so flattens it.

The report has new checks. **Stride contrast** (side views) is the widest step over the narrowest across the cycle: a real walk
alternates a long stride with feet together. The original text-driven walks measured 1.08 to 1.22 (a slide); the pose-guided
walks that read as walking measured 1.49 to 2.56; it is flagged below 1.35. **Frames that missed their pose** are listed by number.

### Which styles get the puppet

The original theory here was "the puppet works when the model has to repaint it, which is the painterly and photographic
styles; a flat-shaded style just returns the puppet as it is, because flat capsules already look like pixel art or a vector
cartoon." Round 3 (below) shows that theory is wrong, or at least incomplete: three more flat/cel-shaded styles were added
(comic ink, anime, chibi) expecting them to need the words fallback too, and all three kept the puppet cleanly, better than
the two painterly newcomers did. What actually predicts failure is still unclear; pixel art and flat cartoon are the only
two measured failures so far, and both happen to be the two styles with the hardest, thinnest outlines and the fewest
shades per surface. Treat this as "check every new style on a GPU," not as a rule to extrapolate from.

Round 4 (also below) reinforces the same lesson from the opposite direction: two painterly/line-art styles (watercolor,
manga) were expected to keep the puppet the way concept art, comic ink and the others did, and both lost it instead
(frames missing their drawn pose even after the built-in retries), while a new blocky style (voxel) kept the puppet
reasonably well despite looking, on paper, like the same repaint risk pixel art and flat cartoon turned out to be.

| Style | Character | What happened |
|---|---|---|
| Semi-realistic | a landscaper in a hat | works after the fixes above; no flags |
| Photorealistic | an Old West cowhand (hat, plaid shirt, chaps, spurs) | works, every draw passed on the first seeds; stride contrast 3.8; no flags |
| Pixel art | a barista | the stride frames came back as the stick figure; blurring the puppet (3 or 7 px) gave a front-facing standing figure or a distorted one, 12 draws |
| Flat cartoon | a merchant with a pack | the stride frames were the raw puppet again; the passing steps and the front view were repainted well |
| Concept art (round 3) | a fantasy merchant with a pack | works, weakest of round 3: stride contrast 1.47 (barely above the 1.35 slide line), one frame needed a re-roll, some head/hair drift |
| Stylised 3D render (round 3) | the same merchant | works, similar to concept art: stride contrast 1.44, one frame needed a re-roll; still a clearly readable stride by eye |
| Comic ink (round 3) | the same merchant | works well: stride contrast 3.68, one head/hair-drift flag |
| Anime (round 3) | the same merchant | works cleanly: stride contrast 3.40, no flags |
| Chibi (round 3) | the same merchant | works cleanest of the nine styles: stride contrast 6.58, no flags |
| 8-bit pixel art (round 4), words | the same merchant | clean: leg variation 0.39, head 1.00, no flags -- not tested against the puppet |
| Voxel art (round 4), words | the same merchant | reads as a slide: leg variation 0.13, stride contrast 1.0, flagged |
| Voxel art (round 4), puppet | the same merchant | kept the puppet: leg variation 0.53, no slide flag, one head/hair-stability flag (0.69) -- chosen over the words fallback |
| Watercolor storybook (round 4), puppet | the same merchant | lost the puppet: all 4 frames missed their drawn pose despite 4 seed tries and up to 3 draws each; falls back to words |
| Manga, monochrome (round 4), puppet | the same merchant | lost the puppet: 2 of 4 frames missed their pose, plus a head/hair-stability flag (0.78); falls back to words |
| Cel-shaded outline (round 5), puppet | a fantasy merchant with a pack | lost it: the stride frames were the raw puppet with a painted head |
| Cel-shaded outline (round 5), words | the same merchant | clean: stride contrast 1.93, head 1.00, no flags |
| Fairy-tale illustration (round 5), puppet | the same merchant | kept the puppet: stride contrast 1.65, no flags; the body stays three-quarter-on |
| Retro sci-fi pulp (round 5), puppet | the same merchant | lost it: front-facing figure with puppet sticks left beside the legs, 2 frames missed their pose |
| Retro sci-fi pulp (round 5), words | the same merchant | clean: stride contrast 2.35, head 1.00, no flags |
| Dark gothic painting (round 5), puppet | the same merchant | ignored it: the same front-facing standing figure in all 4 frames |
| Dark gothic painting (round 5), words | the same merchant | reads as a slide (stride contrast 1.13) but side-on and steady |

So `data/styles.json` has `"guided_walk": false` for pixel art, flat cartoon, 8-bit pixel art, watercolor storybook,
manga, cel-shaded outline, retro sci-fi pulp and dark gothic painting (eight of the seventeen styles), and the walk uses pose words there (the round-1 method; it still gets the fixed
frame rate, the step bob and the stride-contrast flag, which reads a slide as 1.1 to 1.2). Every other style, including
the five added in round 3, voxel art from round 4 and fairy-tale illustration from round 5, keeps the puppet. Each style above was measured on one character
and one direction; check any new style you add the same way before trusting either setting.

### A numeric fault that looked like an image fault

Re-assembling the same stored frames twice gave different pictures: bands of noisy pixels across the legs, in about 1 run in 10.
The cause was numpy's matrix multiply (`pixels @ matrix`) on a 130,000-row array, which returned a different answer for the same
input about 1 run in 50 on the machine this was built on (numpy 2.0.1, Windows). Colour conversions are now written out by hand
(`anim._apply`), and a test scans the package so that the operator cannot come back. The lesson for image code: run the same
step twice and compare the bytes before trusting a picture.

## A real run, round 1 (pixel art, pose words only)

Standard quality, pixel art, four directions x four frames, one candidate per frame, on the R9700:

| Step | Time |
|---|---|
| Portrait | 28 s |
| Full-body sprite (edited from the portrait) | 38 s |
| Two reference views | 15 s each |
| Down / right / up frames (4 each) | 65 s / 91 s / 55 s |
| Assemble | 18 s |
| **Total** | **about 5.4 minutes** |

| Direction | Leg variation | Head stability | Palette (worst frame) |
|---|---|---|---|
| down | 0.21 | 0.995 | 0.96 |
| right (and its mirror, left) | 0.35 | 0.994 | 0.95 |
| up | 0.43 | **0.84 (flagged)** | 0.94 |

The back view is the weakest direction: the head shape and clothing details vary between frames, and the tool says so.
Drawing more candidates per frame (`--best-of 3`) is the first thing to try.

## A real run, round 2 (photorealistic, pose-guided)

Standard quality, photorealistic Old West cowhand, four directions x four frames, one candidate per frame, on the R9700:

| Step | Time |
|---|---|
| Two reference views (right, up) | 23 s + 16 s |
| Front, 4 frames (the probe frames needed 4 seeds) | 188 s |
| Side, 4 frames (the first seed was accepted) | 125 s |
| Back, 4 frames | 125 s |
| Assemble | 19 s |
| **Walk, all directions** | **about 8 minutes** |

The semi-realistic character took 132 s, 222 s (four seeds tried) and 124 s for the same three directions. A direction takes
two to four and a half minutes depending on how many seeds its probe frames need, against about a minute and a half each with
words alone, which is what the seeds and the retries buy.

| Direction | Leg variation | Head stability | Palette (worst frame) | Stride contrast |
|---|---|---|---|---|
| front | 0.09 | 0.99 | 0.97 | |
| side (and its mirror) | 0.55 | 0.88 | 0.98 | **3.8** |
| back | 0.23 | 0.89 | 1.00 | |

No flags. The frames are one woman throughout: the hat, the plaid shirt and the chaps stay; the back view is the least steady (in
one frame the hair changed from long and tied back to a bob).

## A real run, round 3 (five new styles, one direction each)

Standard quality, a fantasy merchant with a pack, right-facing walk only (4 frames, one candidate), same roll and same base
seed for every style, on the R9700:

| Style | Walk (seed search + 4 frames) | Seeds tried on the probes | Stride contrast | Flags |
|---|---|---|---|---|
| Concept art | 214 s | 1 | 1.47 | frame 2 needed a re-roll; some head/hair drift |
| Stylised 3D render | 212 s | 4 | 1.44 | frame 2 needed a re-roll |
| Comic ink | 122 s | 1 | 3.68 | some head/hair drift |
| Anime | 142 s | 2 | 3.40 | none |
| Chibi | 250 s | 4 | 6.58 | none |

Time does not track quality here: chibi took the longest (four seeds tried before one passed both probe poses) and came out
the cleanest by every measure; comic passed its first seed and still logged a flag. All five kept the pose puppet (see
"Which styles get the puppet" above) and, looked at by eye, all five read as a walk, including the two with the lower
stride-contrast numbers. This was one character, one direction, one draw per frame: the front and back views, the other
three directions, and more characters are still unchecked for these five styles.

## A real run, round 4 (four new styles, one direction each)

Standard quality, the same fantasy merchant as round 3 (Osric Blackwood, a traveling merchant, re-rolled once and then
reused across every style so the character itself stays constant), right-facing walk only (4 frames, one candidate),
same seed (12345) for every style, on the R9700.

A script bug caught mid-round is worth recording alongside the numbers: reusing a character across styles with `--ids`
does **not** regenerate its sprite unless `--sprite` is passed explicitly (`Jobs.plan` in `npcgen/jobs.py` adds a
sprite step when one is asked for, or (for an animation, a model sheet or a 3D model) when the NPC has no sprite yet or its sprite is marked stale, whatever style it is currently set to). The first pass through this round
silently edited voxel's, watercolor's and manga's walks from the *8-bit pixel art* sprite left over from the first style
tested, instead of a sprite in their own style. The tell was palette coverage falling off a cliff style by style (0.95,
then 0.63, then 0.15) as each style's edit prompt fought a progressively more mismatched reference image -- nothing to
do with the styles themselves. Every number below is from the redone run, with `--sprite` forced fresh before each walk.

| Style | Method | Walk (seed search + 4 frames) | Flags |
|---|---|---|---|
| 8-bit pixel art | words (`guided_walk: false`) | 121 s | none; leg variation 0.39, head stability 1.00, palette 0.96 |
| Voxel art | words | 124 s | feet never come together and apart (stride contrast 1.0): reads as a slide |
| Voxel art | puppet | 186 s (2 seeds tried on the probes) | head/hair stability 0.69 (flagged); leg variation 0.53, no slide flag |
| Watercolor storybook | puppet | 291 s (4 seed tries, up to 3 draws a frame) | all 4 frames missed their drawn pose even after retries |
| Manga (monochrome) | puppet | 264 s (4 seed tries, up to 3 draws a frame) | frames 1 and 3 missed their pose; head/hair stability 0.78 (flagged) |

Voxel's two rows side by side are the actual finding of round 4. The puppet gives a real stride (leg variation 0.53, no
slide flag) at the cost of a hair-stability flag -- the same trade comic ink made in round 3 and still shipped with. The
words fallback gives a rock-steady head (1.00) but a walk that does not read as walking at all (stride contrast 1.0,
explicitly flagged as a slide). Between "the hair shifts a little between frames" and "the character does not appear to
move," the puppet is the better default, so voxel keeps `guided_walk` at its default (true) rather than getting the
`false` override pixel art and flat cartoon have. Watercolor and manga did not get that same choice to make: their
puppet runs failed outright (the pose checker rejected most or all frames even after its own retries), so both fall back
to words like pixel art and flat cartoon do -- untested head-to-head against a second, words-only run, since the puppet
path was already disqualified on its own.

Four setting portraits (gothic horror, noir detective, superhero, adventurers' guild) were also drawn through the real
pipeline, one character each in the semi-realistic style: a 73-year-old town sheriff, a private investigator, a bartender
and a guild receptionist, all rolled from their setting's own tables and rendered without error.

## A real run, round 5 (four more styles, one direction each)

Standard quality, one fantasy traveling merchant (Fenna Ironside) reused across every style with `--sprite` forced on
each switch (the round-4 lesson), right-facing walk only (4 frames, one candidate), image seed 12345, on the R9700 with
Mage-Flow. Each style was drawn with the puppet first; the three that lost it were then drawn again with pose words.

| Style | Method | Walk | Flags, and what the frames actually show |
|---|---|---|---|
| Cel-shaded outline | puppet | 174 s | head/hair stability 0.68 flagged. Frames 0 and 2 are the puppet handed back: bare coloured capsules with only the head painted on |
| Cel-shaded outline | words (`guided_walk: false`) | 124 s | none; stride contrast 1.93, head stability 1.00. Clean side view, same character in every frame, small real steps |
| Fairy-tale illustration | puppet | 130 s | none; stride contrast 1.65, head stability 0.91. Real strides, but the body stays three-quarter-on and the boots become shoes in the stride frames |
| Retro sci-fi pulp | puppet | 230 s | frames 1 and 3 missed their pose. Frames 0 and 2 are a front-facing figure with left-over puppet sticks painted beside her legs |
| Retro sci-fi pulp | words | 124 s | none; stride contrast 2.35, head stability 1.00. Clean side view with real steps |
| Dark gothic painting | puppet | 229 s | legs barely move (0.11), frames 1 and 3 missed their pose, slide (1.1). All four frames are the same front-facing figure standing still |
| Dark gothic painting | words | 124 s | slide (stride contrast 1.13): side-on and steady, but the same stride in every frame. It also paints a floor shadow despite "no shadow" |

So fairy-tale keeps the puppet and the other three use words. For cel-shaded outline and pulp the words walk is the best
result of the round; gothic's is the weak kind pixel art and flat cartoon already have (a slide), but it still beats its
puppet run, which never turned sideways at all.

**A gap in the frame checks, found here:** the pose checker passed cel-shaded's two handed-back puppet frames and pulp's
two stick-leaking frames as "ok" (right size, facing the right way, feet where the puppet's were), because a handed-back
puppet *is* in the drawn pose. The only number that showed it was palette coverage (0.27 for cel-shaded, against 0.92 for
the same character's words walk). Palette coverage is not flagged today: before it becomes a flag it needs a threshold
measured across more styles and characters, so for now look at the frames of any new style, as this doc keeps saying.

## Not yet verified

- The 8-frame walk through the real model (its plumbing is tested with a stand-in; the poses and prompts are not).
- The experimental `run` clip beyond one four-frame right-facing draft GPU sample. That sample did not alternate legs
  clearly or produce the requested airborne poses; see [gpu-validation.md](gpu-validation.md).
- The pose-guided walk on more than three characters (one semi-realistic, one photorealistic, one fantasy), and on faces,
  hair and clothing the model finds hard: hair from behind changes between frames on several styles.
- The round-3 styles (concept art, stylised 3D render, comic ink, anime, chibi) on the front and back directions, and on
  more than one character: round 3 only drew the right-facing (side) walk.
- A flat-shaded style with a walk that reads as one: the words method flags a slide for pixel art, flat cartoon and voxel
  art's words fallback (stride contrast 1.0 to 1.2). 8-bit pixel art's words fallback did not slide (leg variation 0.39,
  no flag) -- so even within "uses words," which styles actually slide is not predictable either.
- Experimental run, attack, hurt, casting and fall/death clips are available in `data/animations.json`. One four-frame
  right-facing draft GPU sample of each was visually reviewed in September 2026; all showed quality gaps, and run,
  hurt and death/fall failed their intended motion. Other characters, directions and styles remain unverified.
  See [gpu-validation.md](gpu-validation.md).
- Idle is procedural (a breathing loop from the still), not generated. Checked on a real character (the head rises 5 px in 441 and
  the feet never move) but not judged in motion.
- The post-apocalypse and cyberpunk settings, and the fantasy and sci-fi tables' new entries: rolled and validated by the
  automated tests (outfits for every gender, no duplicate ids, over 200 seeds each), but no character from either has been
  drawn through the real pipeline yet.
- The round-4 styles (8-bit pixel art, voxel art, watercolor storybook, manga) on the front and back directions, and on
  more than one character: round 4, like round 3, only drew the right-facing (side) walk. Watercolor's and manga's words
  fallback specifically has never been drawn at all -- only their (failed) puppet path was tested, so it is merely assumed
  to behave like the other words-fallback styles rather than measured.
- The four round-4 settings (gothic horror, noir detective, superhero, adventurers' guild): rolled and validated by the
  automated tests (outfits for every gender, no duplicate ids, hundreds of seeds each) and one portrait per setting drawn
  through the real pipeline, but no sprite, walk or model sheet from any of them yet.
- The three newest settings (pirates, Nordic saga, zombie outbreak) add no new style and no new `guided_walk` question --
  every occupation draws with a style that was already verified above -- so they only needed the same rolling checks
  (outfits for every gender, no duplicate ids, hundreds of seeds each) plus one portrait per setting: Josefina Sable, a
  32-year-old pirate first mate (photorealistic, 32.8s); Halla Hardanger, an 85-year-old Nordic saga jarl (semi-realistic,
  13.7s); and Matteo Hartmann, a 40-year-old zombie-outbreak military officer (semi-realistic). All three rendered
  cleanly on the first try: no deformities, and the zombie setting's decay wording (necrotic skin, wounds, torn insignia)
  came through as an unsettling but coherent face, not a broken one. No sprite, walk or model sheet from any of them yet.
  The zombie setting also adds its own `bio_templates` (past tense: "was", not "is") and its own `traits` pool, chosen to
  exclude every word `common.json`'s `trait_looks` turns into a living facial expression, so `default_expression`
  ("a slack-jawed, vacant stare") always wins instead of a stray "warm, friendly smile" on a corpse -- verified by reading
  actual generated bios for five seeds, not just by the passing assertions (see the pattern noted throughout this doc:
  judge text and images by looking at real output, not only by a metric).
- The anthro setting (ten species: seven bipedal anthros, a therianthrope, and two four-legged taurs) also adds no new
  style, so the same "no guided_walk question" logic applies -- but its own body-plan question needed more than one
  portrait to answer, since a head-and-shoulders crop cannot show whether a species below the shoulders actually
  rendered. Six portraits (semi-realistic): a wolf anthro carpenter, an avian anthro hunter, a reptile anthro farmer, a
  therianthrope artist, a taur blacksmith and a dragontaur trapper. All six were coherent with no deformities -- the
  wolf and avian anthros in particular kept a fully muzzled/beaked face with fur- or feather-covered arms exactly as
  worded, and the dragontaur (obsidian scaled skin, small horns, one scaled arm, a finned tail) was the most visually
  striking result of the whole pass. Two measured misses, both real and worth knowing:
  - The reptile anthro and the therianthrope species, whose most distinctive traits (a reptilian snout; clawed hands)
    either compete with the portrait's occupation prop (a straw hat, here) or fall below the shoulders entirely (hands
    are never in a head-and-shoulders crop), came back reading closer to a plain human with an animal detail at the
    edge of frame than to the wolf/avian's unmistakably animal face. Not broken, just a weaker read than the
    face-forward species -- one data point each, worth another seed or two before relying on them.
  - The full-body sprite is where the taur body plan itself was tested, and it does not hold up: both the taur and the
    dragontaur sprite came back as an ordinary two-legged person (in the taur's case, literally wearing the boots its
    blacksmith outfit names) rather than a four-legged animal lower body. Root cause, read from the code rather than
    guessed: the default sprite mode edits the portrait, and that edit prompt (`prompts.sprite_edit`) says only "the
    same woman/man", never the race or its features -- the four-legged description exists only in the portrait
    prompt, which is already gone by the sprite step. Tried `--sprite-mode independent` (which builds the sprite
    prompt from `describe()`, so it DOES carry the race features and the full outfit text) on the same taur character
    and seed: a real improvement -- visible fur down the legs and a fluffy tail between them, a more muzzled face --
    but still two legs, not four. Likely a limit of the underlying model's anatomy prior more than of the prompt: it
    is a well-known general weakness of diffusion image models to draw a centaur-like body as "a human with extra fur
    on normal legs" rather than actually restructure the leg count. Portraits are unaffected (they never show the
    lower body), so this is specifically a full-body/sprite/model-sheet limitation of the two taur species, not a
    setting-breaking one -- documented here rather than worked around, since a real per-species outfit or sprite-mode
    override would be a genuinely separate feature, not a one-line fix. (Since then a taur gets its own sprite prompt, `prompts._taur_sprite`, which describes the four-legged body: see the follow-up below.)

### Taur morphology follow-up (2026-09)

The app now records morphology explicitly and blocks taur full-body jobs by default. I also ran direct ComfyUI draft
tests (576x864, 20 steps, semi-real style) against a taur-specific prompt that separates the human torso from the
quadruped body and removes the generic "extra limbs" negative. The prompt can produce the intended shape, but the
result is not reliable across seeds: some samples still have human legs or replace the human face with an animal head.
Dragontaur samples also varied between one human head and an additional dragon head. The new prompt is available to
the prompt preview, but sprites, animations, and model sheets remain blocked until repeated GPU samples preserve the
human head and four-legged lower body. Portraits and expressions remain available.

## Reproduce

```bash
python -m npcgen generate --style pixel --name "Maya Okafor" --gender female --age 34 --occupation barista \
    --portrait --sprite --animations walk --dirs down left right up --frames 4 --quality standard
python -m npcgen export maya --clip walk --preset grid4 --out out/grid4
python -m npcgen reassemble maya --clip walk        # redo alignment and the report locally, no GPU
```
