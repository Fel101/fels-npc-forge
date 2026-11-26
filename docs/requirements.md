# Requirements, starting and updating

## Requirements

**Set up ComfyUI** on the Models card installs the model files below that each size of card needs, and NPC Forge's own
ComfyUI nodes, after a yes: [comfyui-setup.md](comfyui-setup.md) has each file's source, size and pinned SHA-256
(Qwen Image 2.1 and the Trellis2 node pack it leaves to you). To do it by hand:

- A running ComfyUI (0.36 or newer) at `http://127.0.0.1:8188`.
- The files of the default engines, Qwen-Image 2512 and Qwen-Image-Edit 2511 with their 4-step Lightning LoRAs (what they need on each size of card: [hardware.md](hardware.md)): `diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors`, `diffusion_models/Qwen-Image-Edit-2511-FP8_e4m3fn.safetensors`, `text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors`, `vae/qwen_image_vae.safetensors`, `loras/Qwen-Image-Lightning-4steps-V2.0.safetensors` and `loras/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors`. Without them a picture fails and names what is missing; to use the lighter Mage-Flow instead, set `"options": {"image_model": "mage", "edit_model": "mage", "engine_fallback": true}` in `config.json` (or pick it in Models).
- For a 16 GB card, Z-Image Turbo ([Tongyi-MAI/Z-Image-Turbo](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo), Apache-2.0, as ComfyUI packages it):
  `diffusion_models/z_image_turbo_bf16.safetensors`, `text_encoders/qwen_3_4b.safetensors`, `vae/ae.safetensors`.
- Optional, the built-in Mage-Flow engine: these files in ComfyUI (from [Comfy-Org/Mage-Flow](https://huggingface.co/Comfy-Org/Mage-Flow)):
  `diffusion_models/mage_flow_int8_convrot.safetensors`, `diffusion_models/mage_flow_edit_int8_convrot.safetensors`,
  `text_encoders/qwen3vl_4b_bf16.safetensors`, `vae/mage_flow_vae_bf16.safetensors`.
  Other variants work if you name them in `config.json`. Every prompt, animation check and model sheet was first tuned on Mage-Flow.
- Optional, for the **Models** card (below): the files of any other engine you want to pick. ComfyUI's `/models` lists are
  checked, and an engine whose files are missing is shown as "not installed".
- For Hunyuan3D v2.1: its ComfyUI nodes and `hunyuan_3d_v2.1.safetensors` in the checkpoint list. For Pixal3D: ComfyUI's
  Pixal3D/TRELLIS2 and mesh-texturing nodes, plus `diffusion_models/pixal3d_multiview_int8_convrot.safetensors`,
  `clip_vision/dino_v3_L_naf_fp32.safetensors`, `vae/trellis_2_shape_vae_bf16.safetensors`,
  `vae/trellis_2_texture_vae_bf16.safetensors` and `background_removal/birefnet.safetensors`. NPC Forge's own two ComfyUI nodes are
  installed with `python scripts/install_pixal_head_node.py <ComfyUI folder> --trellis2`. The Models card checks local
  ComfyUI model lists and required node classes before a generator is selected.
- Python 3.10+ and `pip install -r requirements.txt` (Pillow, numpy and xatlas; the rest is the standard library). Setup.exe
  brings a Python 3.12 of its own and these packages, so it needs neither the internet nor a Python on the computer;
  `setup.bat` uses this computer's Python, or fetches NPC Forge's own (`data/python_runtime.json`, checked by SHA-256) into
  `python\` when there is none.
- Optional **FFmpeg** on `PATH` for Scene maker MP4 export. Scene editing and preview work without it.

Read [NOTICE.md](../NOTICE.md) first: it lists the licence of every model NPC Forge can use, including Microsoft's statement that
Mage-Flow is released for research and not intended for product or service deployment.

## Start

```bash
python server.py --open        # web app at http://127.0.0.1:8766   (or double-click start.bat on Windows)
python -m npcgen -h            # command line
```

Setup.exe adds **Fel's NPC Forge** to the Start menu, which runs `start.bat` in its folder. `start.bat --setup-comfyui` opens the
page at **Set up ComfyUI**, as Setup's last page does. NPC Forge runs while its window is open; close the window, or press
Ctrl+C in it, to stop it.

Everything the page does goes through a local HTTP API, which a game's own scripts can use too: [api.md](api.md) lists
every route, and [game-clients.md](game-clients.md) says how to run a bulk job from a script.

## Updating

*About* > **Check for updates** asks GitHub for a newer release (only when you press it), and **Update now**
shows what will change, closes NPC Forge, updates it and starts it again. Or close NPC Forge and run `update.bat` (`./update.sh`
on Linux or macOS): it shows the plan and asks first. Your library, `config.json`, `local` folder and `.venv` are never touched,
and `update.bat --undo` puts back the version the last update replaced. A `git clone` is moved forward to the release's tag; a
folder unpacked from a ZIP gets the release's files, with a copy of each file it replaces kept in `local\update`.

A newer **Setup.exe** run over an installed NPC Forge, or over a folder unpacked from an older release's ZIP, is an update too:
it says "update from X to Y" first, removes the files the new version no longer has (a copy of each goes to `local\update`),
keeps the ones you changed, and sets up `.venv` from the packages it carries. Setup keeps no copy of the files it replaced, so
`update.bat --undo` cannot undo that update; to go back, uninstall (keeping the library) and install the older version. Setup
will not copy over a `git clone`, or put an older version over a newer one.

An update from 1.0 says that `voice-envs\` and `voice-models\`, where 1.0 kept its own voice engines, are no longer used, now
that voices come from Voice Lab, and how much room they take. Nothing deletes them: delete them yourself when you no longer
need them.

**Uninstalling** (Windows' *Installed apps*, for a Setup.exe install) asks whether to keep your library. Yes keeps the
`library` folder (or the one `config.json` names inside NPC Forge's folder), `local` and `config.json` where they are; no
deletes them too. ComfyUI and its models are not part of NPC Forge and are never touched.

The status line at the top of the page says whether ComfyUI answers and which model files it lacks; `python -m npcgen health`
says the same on the command line.
