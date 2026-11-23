# Changelog

What changed in each version of NPC Forge. The version is shown in the About box and by `python -m npcgen --version`.

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
