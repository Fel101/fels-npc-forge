"""ComfyUI node that adds head-resolution Pixal3D projection features to NPC Forge textures."""
import inspect
import math

import torch
import comfy.model_management
from comfy_api.latest import ComfyExtension, io
from comfy.ldm.trellis2.model import (
    _coords_to_proj_world,
    _project_points_to_image,
    _sample_features,
)
from comfy_extras import nodes_trellis2
from typing_extensions import override


def _replace_projection(conditioning, projection):
    """Copy conditioning metadata and replace only its already-computed sparse projection tensor."""
    out = []
    for entry in conditioning:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2 and isinstance(entry[1], dict):
            extra = entry[1].copy()
            extra["trellis2_proj_feats"] = projection
            out.append([entry[0], extra])
        else:
            out.append(entry)
    return out


def _rgba_foreground(image, mask):
    if image.ndim == 3:
        image = image.unsqueeze(0)
    if image.shape[-1] == 4:
        image = image[..., :3]
    if mask.ndim == 2:
        mask = mask.unsqueeze(0)
    if mask.ndim == 4:
        mask = mask[..., 0]
    if image.shape[:3] != mask.shape:
        raise ValueError("A Pixal3D head crop and its background-removal mask have different dimensions")
    return torch.cat([image, mask.to(image.dtype).unsqueeze(-1)], dim=-1)


TEXTURE_RESOLUTION = 1024      # the picture size of the texture stage's feature maps (nodes_trellis2's "tex_1024")


def _lean_path_available():
    """True when this ComfyUI has the two helpers nodes_trellis2 builds its conditioning from, with the arguments used below."""
    try:
        return (list(inspect.signature(nodes_trellis2._dinov3_encode).parameters)[:4] == ["model", "image_bchw", "image_size", "want_patches"]
                and list(inspect.signature(nodes_trellis2._naf_upsample).parameters)
                == ["naf_model", "lr_feat", "composites", "image_size", "naf_target", "out_device", "compute_device"])
    except (AttributeError, ValueError, TypeError):
        return False


def _view_features(clip_vision_model, image):
    """The texture stage's two feature maps for ONE head picture: DINOv3's patch grid at 1024 and its NAF upsampling to 1024 x 1024,
    both on the compute device.

    nodes_trellis2._build_pixal3d_conditioning makes them for every view at once, plus two more NAF maps (512 x 512) that a head never
    reads, all in host memory: about 12 GiB for four views, held at the same time as the full-body conditioning (also about 12 GiB)
    and the PC's other memory. On a 64 GB PC that reached Windows' commit limit and the run had to be stopped (2026-10-01). One view
    at a time takes about 2 GiB of VRAM and no host memory, and gives the same numbers: it makes the same calls for each picture."""
    device = comfy.model_management.get_torch_device()
    comfy.model_management.load_model_gpu(clip_vision_model.patcher)
    item = image[..., :3].movedim(-1, -3).unsqueeze(0).contiguous().float().clamp(0, 1)
    low = nodes_trellis2._dinov3_encode(clip_vision_model, item, TEXTURE_RESOLUTION, want_patches=True)["patches_2d"]
    high = nodes_trellis2._naf_upsample(clip_vision_model.naf, low, [item], TEXTURE_RESOLUTION,
                                        (TEXTURE_RESOLUTION, TEXTURE_RESOLUTION), device, device)
    return low, high


def _head_projection(features, pack, latent, base):
    """Project each head view only onto voxels actually inside that close-up camera frustum.

    features(camera, device) -> (low, high): that camera's feature maps ([1, C, h, w], high may be None) on `device`. Returns the mean
    over the cameras that see a voxel of their sampled features (low then high), and which voxels any camera saw; (None, None) when
    no camera sees any voxel."""
    coords = latent.get("coords")
    resolution = latent.get("coord_resolution")
    if coords is None or resolution is None:
        raise ValueError("NPC Forge face texturing needs sparse shape coordinates and their resolution")

    transforms = pack["transform_matrix"]
    angles = pack["camera_angle_x"]
    num_views = int(pack["num_views"])

    # Match the device and dtype of Trellis2TextureStage's existing full-body projection tensor.
    device = base.device
    coords = coords.to(device)
    world, batch_ids = _coords_to_proj_world(coords, int(resolution), pack["mesh_scale"].to(device))
    transforms, angles = transforms.to(device), angles.to(device)

    total = counts = None
    for camera in range(transforms.shape[0]):
        selected = batch_ids == camera // num_views
        if not selected.any():
            continue
        low, high = features(camera, device)
        uv, _, valid = _project_points_to_image(
            world[selected].unsqueeze(0), transforms[camera:camera + 1], angles[camera:camera + 1], TEXTURE_RESOLUTION
        )
        uv_ndc = (uv + 0.5) / TEXTURE_RESOLUTION * 2.0 - 1.0
        samples = [_sample_features(low, uv_ndc).squeeze(0).transpose(0, 1)]
        if high is not None:
            samples.append(_sample_features(high, uv_ndc).squeeze(0).transpose(0, 1))
        projected = torch.cat(samples, dim=-1).float()
        if total is None:
            total = torch.zeros((coords.shape[0], projected.shape[1]), device=device, dtype=torch.float32)
            counts = torch.zeros((coords.shape[0], 1), device=device, dtype=torch.float32)
        indices = torch.where(selected)[0]
        visible = valid.squeeze(0).float().unsqueeze(-1)
        total[indices] += projected * visible
        counts[indices] += visible
        del low, high, samples, projected

    if total is None:
        return None, None
    covered = counts.squeeze(-1) > 0
    refined = torch.zeros_like(total)
    refined[covered] = total[covered] / counts[covered]
    return refined, covered


class NPCForgePixal3DHeadTextureRefine(io.ComfyNode):
    """Use full-body conditioning globally and apply close-up features only around the head."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="NPCForgePixal3DHeadTextureRefine",
            display_name="NPC Forge Pixal3D Head Texture Refine",
            category="NPC Forge/3D",
            inputs=[
                io.ClipVision.Input("clip_vision_model"),
                io.Conditioning.Input("body_positive"),
                io.Conditioning.Input("body_negative"),
                io.Latent.Input("shape_latent"),
                io.Image.Input("front_image"), io.Mask.Input("front_mask"),
                io.Image.Input("left_image"), io.Mask.Input("left_mask"),
                io.Image.Input("back_image"), io.Mask.Input("back_mask"),
                io.Image.Input("right_image"), io.Mask.Input("right_mask"),
                io.Float.Input("head_target_up", default=0.35, min=0.0, max=0.8, step=0.001, round=False),
                io.Float.Input("head_crop_fraction", default=0.28, min=0.1, max=0.5, step=0.01, round=False),
                io.Float.Input("body_fov", default=20.0, min=1.0, max=90.0, step=0.1, round=False),
            ],
            outputs=[
                io.Conditioning.Output(display_name="positive"),
                io.Conditioning.Output(display_name="negative"),
            ],
        )

    @classmethod
    def execute(cls, clip_vision_model, body_positive, body_negative, shape_latent,
                front_image, front_mask, left_image, left_mask, back_image, back_mask, right_image, right_mask,
                head_target_up, head_crop_fraction, body_fov) -> io.NodeOutput:
        raw_images = [
            _rgba_foreground(front_image, front_mask), _rgba_foreground(left_image, left_mask),
            _rgba_foreground(back_image, back_mask), _rgba_foreground(right_image, right_mask),
        ]
        images = torch.cat(raw_images, dim=0)

        # The stock Pixal rig places an object's full-body square inside a 1.1-unit frame. Keep its camera distance,
        # tighten the FOV to the source crop's physical size, and move the cameras upward to the head crop center.
        distance = 1.1 * 0.5 / math.tan(math.radians(float(body_fov)) / 2.0)
        head_fov = 2.0 * math.atan(float(head_crop_fraction) * 0.5 / distance)
        azimuths = [0.0, 90.0, 180.0, 270.0]
        cameras = nodes_trellis2._orbit_camera_to_world(azimuths, [0.0] * 4, distance)
        target = torch.tensor([0.0, 0.0, float(head_target_up)], dtype=cameras.dtype)
        cameras[:, :3, 3] += target
        camera_angles = torch.full((4,), head_fov, dtype=torch.float32)
        mesh_scale = torch.ones((1,), dtype=torch.float32)

        body_extra = body_positive[0][1] if body_positive and len(body_positive[0]) >= 2 else {}
        base_projection = body_extra.get("trellis2_proj_feats")
        if not isinstance(base_projection, torch.Tensor):
            raise ValueError("The full-body Pixal3D texture projection was not available for head refinement")

        # Only the texture stage's 1024 feature maps are read, one camera at a time (see _view_features). A ComfyUI whose helpers
        # differ gets the whole conditioning of all four views instead: the same numbers, a lot more memory.
        pack = {"transform_matrix": cameras, "camera_angle_x": camera_angles, "mesh_scale": mesh_scale, "num_views": 4}
        if _lean_path_available():
            def features(camera, device):
                low, high = _view_features(clip_vision_model, images[camera])
                return low.to(device), (high.to(device) if high is not None else None)
        else:
            head_conditioning = nodes_trellis2._build_pixal3d_conditioning(
                clip_vision_model, images, cameras, camera_angles, mesh_scale, num_views=4
            )
            entry = head_conditioning.result[0][0][1]["proj_feat_pack"]["stages"]["tex_1024"]
            all_low, all_high = entry["feature_map"], entry.get("feature_map_hr")

            def features(camera, device):
                return (all_low[camera:camera + 1].to(device),
                        all_high[camera:camera + 1].to(device) if all_high is not None else None)

        # Restrict the replacement to the close-up cameras' projected frusta. All remaining voxels retain the
        # original full-body Pixal3D projection from Trellis2TextureStage.
        head_projection, covered = _head_projection(features, pack, shape_latent, base_projection)
        if head_projection is None:
            return io.NodeOutput(body_positive, body_negative)     # no close-up camera sees any voxel: nothing to refine
        refined = base_projection.clone()
        if refined.shape != head_projection.shape:
            raise ValueError(f"Head projection shape {tuple(head_projection.shape)} does not match full-body projection {tuple(refined.shape)}")
        refined[covered] = head_projection[covered].to(device=refined.device, dtype=refined.dtype)

        return io.NodeOutput(_replace_projection(body_positive, refined), _replace_projection(body_negative, refined))


class NPCForgePixalHeadTextureExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [NPCForgePixal3DHeadTextureRefine]


async def comfy_entrypoint() -> NPCForgePixalHeadTextureExtension:
    return NPCForgePixalHeadTextureExtension()
