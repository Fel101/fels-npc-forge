"""ComfyUI node that decodes a Trellis2 shape latent to a mesh without the CuMesh hole fill, which crashes the GPU on ROCm for Windows."""
import sys

import torch


class NPCForgeTrellis2ShapeDecode:
    """The `trellis2` node pack's Trellis2DecodeLatents ends its shape-only decode with Mesh.fill_holes(), which runs CuMesh's
    boundary-connected-components kernel. On an AMD card under Windows that kernel failed with hipErrorLaunchFailure on a Pixal3D
    multi-view mesh (2026-10-01): the driver reset the GPU and ComfyUI aborted. This decodes the same latent without it, for a
    pipeline made by that pack, and hands the mesh on twice: as the pack's own MESHWITHVOXEL (so its other nodes still work) and
    as ComfyUI's core MESH, which RemeshMesh, DecimateMesh, SaveGLB and the rest take. Holes are left for the remesh, which closes
    them."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "pipeline": ("TRELLIS2PIPELINE",),
                "shape_slat": ("SHAPE_SLAT",),
                "resolution": ("INT", {"default": 1024, "min": 256, "max": 4096}),
                "use_tiled_decoder": ("BOOLEAN", {"default": True}),
            }
        }

    RETURN_TYPES = ("MESHWITHVOXEL", "MESH")
    RETURN_NAMES = ("mesh_with_voxel", "mesh")
    FUNCTION = "process"
    CATEGORY = "NPC Forge/3D"

    @torch.no_grad()
    def process(self, pipeline, shape_slat, resolution, use_tiled_decoder):
        from comfy_api.latest import Types

        # MeshWithVoxel is whatever class the pipeline's own module uses, so this follows the pack it came from
        mesh_with_voxel = sys.modules[type(pipeline).__module__].MeshWithVoxel
        meshes, _subs = pipeline.decode_shape_slat(shape_slat, resolution, use_tiled=use_tiled_decoder)
        if getattr(pipeline, "low_vram", False):
            pipeline._cleanup_cuda()
        mesh = meshes[0]
        packed = mesh_with_voxel(mesh.vertices, mesh.faces, origin=[-0.5, -0.5, -0.5], voxel_size=1 / resolution, coords=None,
                                 attrs=None, voxel_shape=None, layout=pipeline.pbr_attr_layout)
        # ComfyUI's mesh nodes expect batched tensors on the CPU (they move them to the GPU themselves)
        core = Types.MESH(mesh.vertices.detach().float().cpu().unsqueeze(0), mesh.faces.detach().to(torch.int64).cpu().unsqueeze(0))
        return (packed, core)


NODE_CLASS_MAPPINGS = {"NPCForgeTrellis2ShapeDecode": NPCForgeTrellis2ShapeDecode}
NODE_DISPLAY_NAME_MAPPINGS = {"NPCForgeTrellis2ShapeDecode": "NPC Forge Trellis2 Shape Decode (no CuMesh)"}
