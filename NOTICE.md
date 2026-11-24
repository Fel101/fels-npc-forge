# Third-party notices and responsible use

NPC Forge is MIT licensed (see `LICENSE`). It does **not** bundle or redistribute any model weights or any ComfyUI code.
It talks to a ComfyUI server you run yourself, over HTTP.

## Which models may be used for work you sell

The program keeps one list of every model it can use, with its licence, a rating, a plain summary and the links: `data/licences.json`.
The page shows it in three places: a tag after each model's name in every drop-down and a note under it (Models and options, the voice
designer), the button **Licences of every model** (Models and options, About) and a **commercial-use badge** on every character, with
a Library filter. `docs/licences.md` says how a rating is worked out, how a character gets one and how to keep the list current.

The short answer, as the models' own pages declared on 2026-10-13 (the sections below have the sources):

- **Free for commercial work:** Z-Image Turbo, Qwen-Image 2512 and Qwen-Image-Edit 2511 (Apache-2.0); the Qwen3-TTS, Kokoro, Chatterbox
  and VoxCPM2 voices; RealESRGAN; the Quaternius animation clips (CC0).
- **Commercial use allowed, with conditions you must read:** Hunyuan3D v2.1 (not licensed in the EU, UK and South Korea), and anything
  that reads DINOv3 (Pixal3D, Trellis2) or runs SAM (no military or weapons uses); the DramaBox voice (LTX-2 Community License: a paid
  licence from USD 10 million of annual revenue) and the Fish Audio Drama 3 voice (Fish Audio Community License 1.0: only while your
  annual revenue is under USD 500,000, and never as a hosted voice service).
- **Not cleared:** Mage-Flow, built in and no longer the default since 2026-10-13 (the licence file says MIT, Microsoft's README says research only), and a 3D model you
  import (it keeps whatever licence it came with).
- **Non-commercial only:** Qwen Image 2.1 and its edit model, Breeze TTS 2, the 4x-UltraSharp upscaler.

A character is rated by the worst model that made anything of it. None of this is legal advice.

## Mage-Flow (the model)

- **What:** Mage-Flow and Mage-Flow-Edit, from Microsoft Research. Code and models: <https://github.com/microsoft/Mage>.
  ComfyUI-ready files: <https://huggingface.co/Comfy-Org/Mage-Flow> (a repackaging of `microsoft/Mage-Flow`).
- **Licence:** the Microsoft Mage repository is MIT (Copyright Microsoft, 2026) and its licence table lists Mage-Flow as MIT
  (Mage-VL is Apache-2.0, Mage-ViT is MIT). The Comfy-Org repackage is tagged MIT. The LICENSE file adds no clauses beyond
  standard MIT.
- **Stated intended use:** the Mage README says: *"These models are released for research purposes only and are not intended
  for product or service deployment."* and *"the models may generate outputs that are inaccurate, biased, or inappropriate
  under certain prompts"*, and asks downstream users to apply *"additional safeguards, such as content moderation,
  validation, and compliance checks, before broader use."*
- **What that means here:** the licence text itself is MIT, which permits commercial use, while Microsoft's README states
  that the models are not intended for product or service deployment. Those two statements sit in tension and we are not
  lawyers. If you plan to ship generated art in a commercial product, read the sources above yourself and decide.
  The repository does not say who owns the generated outputs, so it does not settle that question either.
- **What this project does about it:** it keeps a human in the loop (you review every image), records provenance
  (model, seeds, prompts) in each `npc.json` and in each animation export, and never ships weights.

Please cite the model when you publish results:

```bibtex
@article{zhang2026mage,
  title   = {Mage-Flow: An Efficient Native-Resolution Foundation Model for Image Generation and Editing},
  author  = {Zhang, Xinjie and Zhang, Peng and others},
  journal = {arXiv preprint arXiv:2607.19064},
  year    = {2026}
}
```

## Other picture engines (optional, the Models card)

NPC Forge can also ask ComfyUI to draw with these, if you have their files (see `npcgen/engines.py`). As with Mage-Flow, no
weights are bundled. Licences as each model's own Hugging Face page declared them on 2026-09-23; read them yourself before you
ship anything.

| Engine | Model page | Licence |
|---|---|---|
| Z-Image Turbo | <https://huggingface.co/Tongyi-MAI/Z-Image-Turbo> | Apache-2.0 |
| Qwen-Image 2512 | <https://huggingface.co/Qwen/Qwen-Image-2512> | Apache-2.0 |
| Qwen-Image-Edit 2511 | <https://huggingface.co/Qwen/Qwen-Image-Edit-2511> | Apache-2.0 |
| Lightning 4-step LoRAs | <https://huggingface.co/lightx2v/Qwen-Image-Lightning>, <https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning> | Apache-2.0 |
| **Qwen Image 2.1** (image and edit) | <https://huggingface.co/Qwen/Qwen-Image-2.1> | **Qwen Research License: non-commercial use only** |
| Ultra-Realistic Portrait LoRA (the Adult studio's optional realism engine) | <https://huggingface.co/prithivMLmods/Qwen-Image-Edit-2511-Ultra-Realistic-Portrait> | Apache-2.0 (page checked 2026-11-20) |

Qwen Image 2.1's licence grants use *"FOR NON-COMMERCIAL PURPOSES ONLY"* ("research or evaluation purposes") and asks for a
separate commercial licence from Alibaba's Tongyi Lab otherwise. Do not use it for art you intend to sell or ship in a
commercial game. The Models card says so next to both Qwen Image 2.1 entries.

## Voice models (optional, the voice designer)

NPC Forge can give characters voices with these text-to-speech models, each installed in its own Python environment by
Voice Lab (see [Voices](docs/voices.md)). Forge includes voice authoring and export code. Licences as each model's page
declared them in September 2026; read them yourself before you ship anything.

| Model | Where | Licence |
|---|---|---|
| Qwen3-TTS 1.7B (VoiceDesign, Base, CustomVoice) | <https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign>, <https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base> | Apache-2.0 |
| faster-qwen3-tts (the fast runtime for it) | <https://pypi.org/project/faster-qwen3-tts/> | MIT |
| Kokoro 82M | <https://huggingface.co/hexgrad/Kokoro-82M> | Apache-2.0 |
| Chatterbox Turbo and Chatterbox (original) | <https://github.com/resemble-ai/chatterbox> | MIT; every clip carries Resemble's inaudible Perth watermark |
| VoxCPM2 | <https://huggingface.co/openbmb/VoxCPM2> | Apache-2.0 |
| faster-whisper tiny (only counts wrong words in a take; nothing it makes is kept) | <https://huggingface.co/Systran/faster-whisper-tiny> | MIT |
| **Breeze TTS 2** | weights: <https://huggingface.co/drbaph/Breeze-TTS-2-comfyui> (mirror of BreezeBlue's release) | **BreezeBlue Research and Non-Commercial License** |
| DramaBox | <https://huggingface.co/ResembleAI/Dramabox>, <https://github.com/resemble-ai/DramaBox> | LTX-2 Community License (a paid licence from USD 10 million of annual revenue); its Gemma text encoder has Google's Gemma terms |
| Fish Audio Drama 3 | <https://huggingface.co/fishaudio/drama-3> | Fish Audio Community License 1.0: commercial use only while your annual revenue is under USD 500,000, never as a hosted voice service; **Built with Fish Audio** |

Breeze TTS 2's weights are for non-commercial use: do not use its voices in a game you sell. The voice designer marks every
voice a non-commercial model made or speaks, and its Voice pack README says so. Its inference code came from the Apache-2.0
ComfyUI node Saganaki22/ComfyUI-Breeze-TTS-2, whose repository was taken down in September 2026 (the ComfyUI registry flags
its last version); NPC Forge does not include or download it (`--breeze-code` points the installer at a copy you have).

The Drama 3 engine is **Built with Fish Audio**. NPC Forge does not include or download Fish Audio's model or code; if you install
it, its own licence file says: "These Fish Audio Materials are licensed under the Fish Audio Community License, Version 1.0.
Copyright © 39 AI, INC. All Rights Reserved."

**Voice cloning, responsibly.** The designer can copy a voice from a recording you upload. Only do that with your own voice or
with the permission of the person speaking: copying someone's voice without consent can hurt them, and in many places it is
illegal. The upload asks you to confirm this and the voice records that you did. An adult character's voice is always described
as an adult's, and a description that makes an adult sound like a child is refused.

## ComfyUI

ComfyUI is GPL-3.0 (<https://github.com/comfyanonymous/ComfyUI>). NPC Forge runs as a separate process and only calls
ComfyUI's HTTP API, so ComfyUI's licence does not apply to this code. Install ComfyUI yourself.

## Python dependencies

- Pillow (HPND / MIT-CMU style licence) and numpy (BSD-3-Clause). Both are installed from PyPI and are not vendored.
- xatlas (MIT; the xatlas-python bindings and the xatlas library are both MIT), for the sharper face's head texture
  layout. Installed from PyPI.

## Upscale models (sharper face)

The sharper face upscales head close-ups with an upscale model you install in ComfyUI yourself; none is bundled.
RealESRGAN_x4plus (the default) is BSD-3-Clause. **4x-UltraSharp** is CC BY-NC-SA 4.0: **non-commercial use only**. Pick one with
`"face_upscaler"` in config.json; the 3D card shows which one a model was made with.

## Pose keypoints (skeletons)

Placing a skeleton's joints, finding the hands for the clean-up's Clean hands option and the faces for its Attach the detailed head
option, uses **SDPose** (whole body,
133 points), run by ComfyUI's own SDPoseKeypointExtractor node
from `sdpose_wholebody_fp16.safetensors` (<https://huggingface.co/Comfy-Org/SDPose>, repackaged from
<https://huggingface.co/teemosliang/SDPose-Wholebody>), which you install in ComfyUI yourself; it is not bundled. SDPose is
under the **MIT licence**. Its network was started from Stable Diffusion 2's weights, which are under the CreativeML
Open RAIL++-M licence: commercial use is allowed, with that licence's use restrictions. Without it, skeletons are placed
from the model's outline, as before, and the medium skeleton is not offered.

## Animation clips (skeletons)

The optional animation library is Quaternius' **Universal Animation Library 1 and 2**, the free Standard packs
(<https://quaternius.com/packs/universalanimationlibrary.html>), which `scripts/install_animations.py` downloads from
<https://quaternius.itch.io/universal-animation-library> and <https://quaternius.itch.io/universal-animation-library-2>;
they are not bundled. The packs are under **CC0 1.0** (a public domain dedication): free to use, change and sell, with no
credit required (it is given here anyway, with thanks). A rigged model carries the clips copied onto its own skeleton.

## Hair masks (match the hair)

Matching the hair finds hair with **SAM 3.1** (Meta), run by ComfyUI's own SAM3_Detect node from
`sam3.1_multiplex_fp16.safetensors` (<https://huggingface.co/Comfy-Org/sam3.1>), which you install in ComfyUI yourself; it
is not bundled. It is under the **SAM Licence** (<https://huggingface.co/Comfy-Org/sam3.1/blob/main/LICENSE>): commercial use
is allowed; redistributing the weights needs the licence with them, and it rules out military, weapons and similar uses.

## 3D generators (optional, the Models card)

NPC Forge calls these through ComfyUI and bundles none of them: you install them in ComfyUI yourself. Licences as each model's own
page or licence file declared them on 2026-10-01; read them yourself before you ship anything made with them.

| Generator | Where | Licence |
|---|---|---|
| **Hunyuan3D v2.1** | <https://huggingface.co/tencent/Hunyuan3D-2.1> | **Tencent Hunyuan Community License.** Its text says it does not apply in the European Union, the United Kingdom and South Korea, and that a product or service with more than 1 million monthly active users needs Tencent's written approval first |
| **Pixal3D** (also what the Trellis2 generator loads) | <https://huggingface.co/TencentARC/Pixal3D> | MIT (Copyright 2026 Tencent) |
| **Trellis2 node pack** and **TRELLIS.2-4B** | <https://github.com/visualbruno/ComfyUI-Trellis2>, <https://huggingface.co/microsoft/TRELLIS.2-4B> | MIT |
| **DINOv3** features, used by Pixal3D and Trellis2 | <https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m> | Meta's **DINOv3 License** (<https://ai.meta.com/resources/models-and-libraries/dinov3-license/>) |
| **BiRefNet**, which cuts out the pictures | <https://huggingface.co/ZhengPeng7/BiRefNet> | MIT |

The Trellis2 generator also uses two small ComfyUI nodes of NPC Forge's own (`npcgen/comfy_extensions/`, MIT like the rest of this code;
`scripts/install_pixal_head_node.py` installs them). A 3D model you import (`docs/model-import.md`) keeps whatever licence it had.

## Sample images

The art-style samples in `web/styles/` (one invented character drawn in every style, shown by the style picker) were made
with **Z-Image Turbo** (Apache-2.0) through NPC Forge's own portrait prompts, not with Mage-Flow. Any other sample images in
this repository were generated with Mage-Flow; they are examples of the tool's output and carry the uncertainty described
above. Do not treat any of them as licensed artwork.

## 3D preview

The interactive GLB preview uses Google's `<model-viewer>` (Apache-2.0) and the libraries it bundles (three.js, Draco, Basis
Universal, meshoptimizer, lit and others), vendored in `web/vendor/model-viewer/` with their licence files and
`THIRD_PARTY_NOTICES.txt`.

## 3D scenes

The animated scene stage uses three.js 0.183.0 (MIT), including its GLTFLoader and geometry/skeleton helpers. The local
copies are in `web/vendor/three/` with `LICENSE.txt`. Scene background images are drawn with the picture engine chosen in
Models (Qwen-Image 2512, Apache-2.0, unless another is picked or the Mage-Flow fallback is on); the licence of the model that
drew a picture still applies to it, and the scene's record names that engine.
