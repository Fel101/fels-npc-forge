# NPC Forge for AI agents (MCP)

Claude Code, Codex, opencode and any other client that speaks the [Model Context Protocol](https://modelcontextprotocol.io)
can make characters with NPC Forge: "make me eight townsfolk for a fantasy village, with portraits and sprites, and export
them for Godot". NPC Forge ships a small MCP server, `npcgen/mcp.py` (Python's standard library only), that the client starts
itself.

**NPC Forge must be running** (`start.bat`, and ComfyUI). The MCP server is only a go-between: every tool is a call to NPC
Forge's [HTTP API](api.md), so an agent's work goes through the same queue and the same one-model-at-a-time GPU guard as the
page, and you can watch it in the page's queue.

## Adding it to a client

Replace `C:\path\to\npc-forge` with the folder NPC Forge is in. Any Python 3.10 or newer will do (the server needs no packages);
on Linux or macOS use `python3` and a `/` path.

**Claude Code** (one command, then `/mcp` shows it):

```bash
claude mcp add npc-forge -- python "C:\path\to\npc-forge\npcgen\mcp.py"
```

or, shared with a project, a `.mcp.json` in that project (the same block works in Claude Desktop's `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "npc-forge": { "command": "python", "args": ["C:\\path\\to\\npc-forge\\npcgen\\mcp.py"] }
  }
}
```

**Codex** (`~/.codex/config.toml`):

```toml
[mcp_servers.npc-forge]
command = "python"
args = ['C:\path\to\npc-forge\npcgen\mcp.py']
```

**opencode** (`opencode.json`):

```json
{
  "mcp": {
    "npc-forge": { "type": "local", "command": ["python", "C:\\path\\to\\npc-forge\\npcgen\\mcp.py"], "enabled": true }
  }
}
```

**Any other MCP client:** a local (stdio) server whose command is `python` and whose argument is the path to `npcgen/mcp.py`.

The MCP server checks the suite address file first, then port 8766, then port 8765 for older copies of Forge. It checks the
app's identity at each address. To try a specific address first, add `--url http://127.0.0.1:8775` after the path or set
`NPCFORGE_URL`. If Forge asks for a password, set the same `NPCGEN_AUTH_TOKEN` for the MCP server in the client's settings.

## The tools

| Tool | What it does |
|---|---|
| `forge_status` | Is NPC Forge ready: its version, whether ComfyUI answers, which model files each feature lacks, the queue. |
| `forge_options` | The settings, art styles, genders, age groups, expressions, qualities, engines and export formats it knows. |
| `make_characters` | 1-50 characters from a setting (narrowed by gender, age group, occupation, heritage, hair) or from a description, and their pictures queued: portrait, sprite, expressions, model sheet, walk cycle, 3D. |
| `generate_assets` | More for characters that exist: another expression set, a model sheet, a walk cycle, a 3D model. |
| `wait_for_jobs` | Waits for the named jobs (or the whole queue), up to 15 minutes a call; says why any failed, with NPC Forge's reason code. |
| `list_characters` | The library: id, name, setting, occupation, age, what each has, and what its models allow for commercial use. |
| `get_character` | One character's sheet (profile, personality, looks, bio, dialogue) and the address of every picture and model file. |
| `export_characters` | Writes ZIPs to a folder: everything, the model sheet, a finished animation for Godot 4, Unity, Aseprite or a plain atlas, or dialogue as JSON, Ink or Yarn. |
| `make_item` | A game-ready 3D weapon, piece of armour, hat, shoes, bag or prop from a description (about 15-20 minutes). |
| `cancel_jobs` | Stops the named jobs, or all of them. |

Pictures take about 30 seconds each and run one at a time, so a batch of eight characters with a portrait, a sprite and four
expressions is about half an hour; 3D takes 5-30 minutes a character. The agent is told this, and `wait_for_jobs` can be called
again when a batch takes longer than one call waits.

What a picture is drawn with, and what that allows, is on each character (`list_characters` says `commercial_use`;
[licences.md](licences.md) explains). Children and teenagers are rolled only when an age group asks for them, as on the page.

## What it does not do

- **No adult content.** The [adult features](../extensions/adult_studio/README.md) (the Adult studio and the Models setting) are
  not offered through MCP, whether or not their 18+ box is ticked: an agent should not be able to skip a person's own
  confirmation. Their setting, their characters and their routes are refused.
- **It does not start NPC Forge or ComfyUI**, and it does not change NPC Forge's settings. `forge_status` says what is missing.
- **It does not delete anything.** Characters are removed in the Library tab.

## Without MCP

A script can call the [HTTP API](api.md) directly ([game-clients.md](game-clients.md) has the details for bulk work), and
`python -m npcgen --help` lists the command line, which works without the server.
