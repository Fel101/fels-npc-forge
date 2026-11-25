# Set up ComfyUI

NPC Forge draws its pictures and 3D models in [ComfyUI](https://www.comfy.org/download). **Set up ComfyUI** puts in the
model files each size of graphics card needs, so nobody has to copy them into ComfyUI's folders by hand. Characters,
personalities, dialogue packs, cast packs and the MCP server need none of this.

Open it from the **Your PC** box on the Models card (**Set up ComfyUI…**), or from the banner the page shows while ComfyUI is
not answering.

## Where ComfyUI is

- **ComfyUI is running on this computer** (at `comfy_url`, `http://127.0.0.1:8188` unless `config.json` says otherwise): it
  says where its folders are (`GET /internal/folder_paths`), the folders of `extra_model_paths.yaml` included. A file is
  there when it is in any folder ComfyUI lists for its kind. A new file goes into the first listed folder that has the kind's
  own name (`models/diffusion_models`, not the older `models/unet` ComfyUI lists before it), so it lands where this README and
  ComfyUI's own pages say. ComfyUI sees a new model file at once; there is nothing to restart.
- **ComfyUI is not running:** choose its folder with **Choose the ComfyUI folder…** (this computer's own folder picker), or
  type it under *Details*. It is the folder with ComfyUI's `models` and `custom_nodes` folders in it; for the portable ZIP,
  the folder it was unpacked into works too. The choice is kept in `config.json` as `"comfy_folder"`, and files go into its
  `models` folders. A running ComfyUI always wins over the chosen folder.
- **ComfyUI runs on another computer:** nothing is put there from here. Use the table below on that computer.
- **No ComfyUI yet:** the dialog links to [comfy.org](https://www.comfy.org/download) (ComfyUI Desktop or the portable ZIP).
  NPC Forge does not install ComfyUI itself.

The folder picker opens on the computer NPC Forge runs on, so it is not offered when NPC Forge asks for a password (the LAN
mode): type the folder instead.

## What each size of card needs

Picking a size ticks what that card makes, from the measurements in [hardware.md](hardware.md); the card ComfyUI reports
is picked to start with. Anything can be ticked or unticked.

| Card | Parts | Files | Download |
|---|---|---|---|
| About 16 GB | Z-Image Turbo, Qwen-Image-Edit 2511, Hunyuan3D 2.1 | 8 | 59.0 GB |
| About 24 GB | and Qwen-Image 2512 | 10 | 81.1 GB |
| 32 GB | and Pixal3D, Trellis2 | 15 | 90.4 GB |

Qwen-Image 2512 and Qwen-Image-Edit 2511 share their text encoder and VAE, which are downloaded once.

| Part | Files (ComfyUI folder, size, where from) | Size |
|---|---|---|
| Portraits with Z-Image Turbo | `diffusion_models/z_image_turbo_bf16.safetensors` (12.3 GB, [Comfy-Org/z_image_turbo](https://huggingface.co/Comfy-Org/z_image_turbo))<br>`text_encoders/qwen_3_4b.safetensors` (8.0 GB, same)<br>`vae/ae.safetensors` (335 MB, same) | 20.7 GB |
| Portraits with Qwen-Image 2512 | `diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors` (20.4 GB, [Comfy-Org/Qwen-Image_ComfyUI](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI))<br>`text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors` (9.4 GB, same)<br>`vae/qwen_image_vae.safetensors` (254 MB, same)<br>`loras/Qwen-Image-Lightning-4steps-V2.0.safetensors` (1.7 GB, [lightx2v/Qwen-Image-Lightning](https://huggingface.co/lightx2v/Qwen-Image-Lightning)) | 31.8 GB |
| Expressions, sprites, model sheets and animation with Qwen-Image-Edit 2511 | `diffusion_models/Qwen-Image-Edit-2511-FP8_e4m3fn.safetensors` (20.4 GB, [1038lab/Qwen-Image-Edit-2511-FP8](https://huggingface.co/1038lab/Qwen-Image-Edit-2511-FP8))<br>`text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors` and `vae/qwen_image_vae.safetensors` (as above)<br>`loras/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` (850 MB, [lightx2v/Qwen-Image-Edit-2511-Lightning](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning)) | 30.9 GB |
| 3D models with Hunyuan3D 2.1 | `checkpoints/hunyuan_3d_v2.1.safetensors` (7.4 GB, [Comfy-Org/hunyuan3D_2.1_repackaged](https://huggingface.co/Comfy-Org/hunyuan3D_2.1_repackaged)) | 7.4 GB |
| 3D models with Pixal3D | `diffusion_models/pixal3d_multiview_int8_convrot.safetensors` (5.6 GB, [Comfy-Org/Pixal3D](https://huggingface.co/Comfy-Org/Pixal3D))<br>`clip_vision/dino_v3_L_naf_fp32.safetensors` (1.2 GB, same)<br>`vae/trellis_2_shape_vae_bf16.safetensors` (1.1 GB, same)<br>`vae/trellis_2_texture_vae_bf16.safetensors` (948 MB, same)<br>`background_removal/birefnet.safetensors` (444 MB, [Comfy-Org/BiRefNet](https://huggingface.co/Comfy-Org/BiRefNet))<br>and NPC Forge's own head-texture node | 9.3 GB |
| 3D models with Trellis2 | `background_removal/birefnet.safetensors` (as above), NPC Forge's own shape-decode node, and the [ComfyUI-Trellis2](https://github.com/visualbruno/ComfyUI-Trellis2) node pack with its Pixal3D models, which NPC Forge does not install | 444 MB |
| Mage-Flow (not picked for any size) | `diffusion_models/mage_flow_int8_convrot.safetensors` and `mage_flow_edit_int8_convrot.safetensors` (4.2 GB each), `text_encoders/qwen3vl_4b_bf16.safetensors` (8.9 GB), `vae/mage_flow_vae_bf16.safetensors` (345 MB), all [Comfy-Org/Mage-Flow](https://huggingface.co/Comfy-Org/Mage-Flow) | 17.5 GB |

Each part shows its licence as the [licence list](licences.md) rates it. Microsoft released Mage-Flow for research, not for
products or services, so it is offered but never ticked for you. Qwen Image 2.1 (non-commercial) and the 3D clean-up's extra
models (SDPose, SAM 3.1, the upscalers) are not in the helper: their own cards say what is missing and where it comes from.

The Qwen-Image 2512 model the measurements in [hardware.md](hardware.md) used was another uploader's fp8 copy of the same
weights (20.48 GB); the helper installs Comfy-Org's, which ComfyUI's own templates use (20.43 GB). A file of the right name
but another size is used as it is (below), so a PC that already has the other copy keeps it.

## What is there, and what Install does

A file counts as **there** by its name and size. Then:

- **A different copy** (the right name, another size): NPC Forge uses it as it is and never replaces it.
- **Install** checks the SHA-256 of each file already there before it counts it. A file of the pinned size whose hash does
  not match is damaged: the pinned file is downloaded beside it and replaces it only once it has arrived whole.
- **A missing file** is downloaded from the Hugging Face commit NPC Forge pins for it (`data/comfy_models.json`), checked
  against its size and SHA-256, and only then put in place. One that does not match is deleted and the install says so.
- **Nothing is downloaded without a yes:** Install shows what it will fetch, how much and where, and asks first.
- **Progress and Stop:** the dialog shows each file, how much has arrived, the speed and the time left. Stop (or closing
  NPC Forge) keeps what arrived beside the file, as `<name>.npcforge-part` (ComfyUI does not list it), and the next Install
  carries on from there. A download that breaks off is taken up again a few times on its own.
- **Room:** Install says before it starts when the drive has too little free space, and leaves 1 GB spare.
- **NPC Forge's own ComfyUI nodes** (for Pixal3D and Trellis2) are copied into `custom_nodes`, as
  `scripts/install_pixal_head_node.py` does; ComfyUI loads them when it next starts, and the dialog says so. A folder of that
  name holding another node is left alone. Other people's node packs are named with their page, never installed.

`HF_ENDPOINT`, as Hugging Face's own tools read it, points the downloads at a mirror.

## For scripts

| Route | What it does |
|---|---|
| `GET /api/comfy-setup` | Where ComfyUI is, the tier, what each tier ticks, every part with its files, nodes and licence, and the install in progress (`?progress=1`: just the install) |
| `POST /api/comfy-setup/install` | `{"parts": ["image:zimage", ...], "yes": true}`: starts the install. Refused without `"yes": true` |
| `POST /api/comfy-setup/stop` | Stops it; what arrived is kept |
| `POST /api/comfy-setup/folder` | `{"folder": "D:/ComfyUI"}`, `{"pick": true}` (this computer's folder picker) or `{"folder": null}` |

## Changing a pin

`python scripts/pin_comfy_models.py` writes `data/comfy_models.json` from each repository's current commit, reading Hugging
Face's file lists (no model is downloaded). `--check` checks that every pinned commit still serves the same size and
SHA-256. A test checks that every file a part loads is pinned.
