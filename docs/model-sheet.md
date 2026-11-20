# Model sheets with Mage-Flow-Edit: what we tried and what worked

Goal: turn one character into a **turnaround** (portrait, front, both sides and back, all on white and at the same scale)
that still looks like the same person in every view, the kind of sheet a 3D artist models from or an image-to-3D tool
takes as input. Mage-Flow-Edit has no notion of "turn this person around", so every full-body view is its own edit of the
character's full-body sprite (the portrait view is an edit of the portrait when the character has one), asked for in plain words ("a true side profile view facing right").

All images below were drawn at 576x1152 per panel, 30 steps, on an AMD Radeon AI PRO R9700, at about 20 to 35 seconds a
view. The samples are **small** (one to three characters, one or two seeds), not benchmarks, and we say so where it
matters. The code is `npcgen/sheet.py`; the wording is in `data/sheet.json`.

## Where this came from

The layout (a close-up plus four full-length views, one edit each, stitched) comes from a ComfyUI workflow that fed each
edit a second image: a **grey mannequin turnaround** as a pose and framing guide. We measured whether the guide earns its
place.

## Experiment 1: is a pose guide needed?

Same character reference, same seed, same prompt except that the guide variant added "image 2 is only a pose and framing
reference showing a grey mannequin: copy its pose, scale and camera angle, but do not copy the mannequin". Two characters
(a painted field worker in a sun hat, and a photographic shopkeeper), side and back views.

| | Without a guide | With the mannequin guide |
|---|---|---|
| Side view turns the right way | yes, both characters | yes, both characters |
| Figure height (share of the panel) | 0.90 to 0.93 | 0.86 to 0.93 |
| Back view of the hat wearer | keeps the hat | **loses the hat** (the mannequin's bald head leaked) |
| Back view of the shopkeeper | fine | fine, slightly smaller in the frame |
| Needs a licensed, neutral, body-matched guide image | no | yes (and a female-shaped guide pushes its body onto male or child characters) |

**Result: no guide.** The model already keeps the reference's scale and position (figures came out 86 to 93% of the panel
high unaided), and the guide added a way to go wrong. Skipping it also means nothing has to be shipped or licensed.

## Experiment 2: does the art style survive?

The sheet prompts end with "keep the exact same *<style>* art style, line style and colour palette". Three characters in
three of the styles (painterly, cartoon and pixel art), front, side and back, guidance 3.5 against 5.0 on the side view.

**Result:** the look, outfit and proportions held in every view, including the outlines and flat shading of the pixel-art
and cartoon characters. Guidance 3.5 and 5.0 gave visually the same side view, so the project's edit guidance (3.5, chosen earlier to
stop styles drifting) is used. In the first experiment, whose prompt had no style sentence, the edits of a *painted* reference came back looking like photographs.

## Experiment 3: the back of the head

The back view is the one place the model has to invent something (the hair from behind). Two wordings failed:

| Wording of the back view | Field worker (long black hair, sun hat) | Shopkeeper (low bun) |
|---|---|---|
| "Show the same hairstyle and hair colour: a bun, braid or ponytail stays as it is" | **drew a bun and braid, lost the hat** (1 seed; a cartoon-style character did the same) | (not run) |
| Nothing extra | hat and long dark hair kept (2 of 2 seeds) | **bun lost**, a loose bob instead (2 of 2 seeds) |
| "Keep the same hat or other headwear. Seen from behind, the hair is *<the character's own hair from the data>*" | hat and black hair kept (2 of 2) | bun kept (2 of 2) |

Giving the model *examples* planted them: it drew a bun on characters who had none. Stating the character's real hair
from the data sheet worked on both. This is what ships (`people_extra` in `data/sheet.json`). A regression test checks that
no example hairstyles appear in the prompt.

## Experiment 4: an A-pose

3D artists want the arms clear of the body. "In a neutral A-pose with the arms slightly away from the body" changed
nothing (the model copies the reference's pose). "The arms held straight and angled about 30 degrees away from the body, so
there is a clear gap between each arm and the torso" gave a visible A-pose with the face and clothing intact. The default
pose is an A-pose; "Relaxed" keeps the arms at the sides.

## Experiment 5: the sprite is the weak link

A sheet copies its sprite, so whatever the sprite invented, every view repeats. The first full run (a photographic
shopkeeper, outfit "a cream blouse, a faded olive ankle-length skirt, a dark apron and worn brown boots") came out in
**sandals**, and an older workman whose outfit said "dark work trousers" came out in blue jeans. The cause was upstream: the
sprite is edited from a *head-and-shoulders portrait*, so nothing below the chest is visible, and the edit prompt never
mentioned the outfit, so the model invented the skirt length and the shoes.

The sprite edit now restates the complete outfit ("the complete outfit, everything below the shoulders included, is: ...")
and what the character carries. On the shopkeeper's own portrait, three seeds each (`npcgen/prompts.py`):

| Sprite edit prompt | Shoes |
|---|---|
| Without the outfit | flat sandals or flat black shoes (3 of 3 seeds) |
| With the outfit | **brown boots** (3 of 3 seeds) |

The skirt length only partly followed ("ankle-length" came out at mid-calf on one seed), so treat the outfit text as a strong
hint, not a guarantee, and re-roll the sprite if the lower body matters.

## Alignment and the report

- Backgrounds came back **pure white** (standard deviation under 1 level), so a view needs no cutting out.
- Each full-body view is scaled and shifted so the figure is 90% of the panel high, centred, standing on the same line. The
  whole panel is transformed and the margin filled with the picture's own backdrop colour, so an off-white background
  cannot show a seam. In practice the correction is small (scale 0.98 to 1.00 in the runs above).
- The report compares each view's colours with the sprite's palette. Good views scored **0.76 to 0.99**. The measure is
  **coarse**: a view that lost its hat scored the same (0.91) as one that kept it. It only flags wholesale colour changes
  (below 0.55, such as a blouse turning green), so read it as an alarm, not a grade.

## Known limits

- Feet and the back of the hair are the weak spots. Every view has its own seed and its own refresh button, so a bad one is
  redone alone (`--sheet back` on the command line).
- Only 576x1152 panels were tested at length; Draft (384x768) and High (768x1536) are configured but lightly tested.
- The A-pose is modest (a clear gap, not a true 30 degrees), and hands are not always perfect.
- Identity across views is judged by eye; no face-similarity metric is used.
- A sheet is only as good as the sprite it comes from (Experiment 5). A sprite made from a portrait keeps the face; one drawn
  from a description alone will differ from the portrait.
- Random extras are rolled unless you rule them out: an Old West shopkeeper was once given a hunting knife by the roll. In a cast
  file, write `none` in `accessory`, `beard`, `glasses` or `distinguishing`.
