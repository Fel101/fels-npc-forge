# Creatures for any game

The **Creatures** tab makes the non-humanoid creatures a game needs: quadrupeds, serpents, flyers, six-legged things and props. Each creature
belongs to one game (its **Game** field), and it is made in steps that each wait for the one before:

1. **Concept picture** (Qwen-Image 2512, Apache-2.0). Look at it, then press **Approve this picture**. The 3D model is made only from an approved picture.
2. **3D model** (TRELLIS.2-4B, made from that one picture; MIT, with DINOv3 features rated "conditions").
3. **Game model**: scaled to its length, at most 20,000 triangles, one matte colour, and a rig where its body plan has one (serpentines: 12 bones and five clips). The other body plans are static models until their rigs are built.

Quadrupeds are concept pictures only, for now: one picture gives a deformed 3D model, so their 3D and game-model steps are refused until a four-view route or a proportion fix is built.

Each creature is rated by the engines that made it (**Licence** on its page, from `data/licences.json`). **Download the pack** gives a game a
ZIP of `model.glb`, `concept.png` and `manifest.json` (metres, +Y up, +Z forward, the bones and clips, the engines and seeds, and the rating).
A pack is refused until the creature has a game, a game model and a cleared licence, and the page says which. On the command line:
`python scripts/export_creature_pack.py <bestiary key or creature id> <folder>`; `--install` also copies the pack into the game's Pack folder (set on the Projects panel). The body plans are in `data/creature_plans.json`, and the
bestiary list the tab starts from is `data/briefs/<game>.json`, one file per game. The library keeps each creature in `library/.npcforge-creatures/<id>/`.
