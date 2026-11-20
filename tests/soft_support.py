"""Real bodies and a small skinning evaluator for the soft-body tests (tests/test_softbody.py; npcgen/softbody.py)."""
import math

import numpy as np

from npcgen import bodymaker, softbody
from npcgen.paint3d import Glb

FEMALE, MALE = "forge-anatomical-female-v7", "forge-anatomical-male-v7"


def game_body(base=FEMALE, values=None, skeleton="low", texture=512):
    """(the Body Designer's own game GLB of `base`, built here from its default recipe with `values` over it, the Natural face and a 512 px skin: about 20 s; the recipe)."""
    api = bodymaker._api()
    recipe = api.default_recipe(base)
    recipe["values"].update(values or {})
    recipe = api.clean_recipe(recipe)
    recipe = api.clean_recipe(bodymaker.with_face(api, recipe, "natural", explicit=False))
    data, _info = api.build_game_glb(recipe, skeleton=skeleton, faces=100000, texture=texture)
    return data, recipe


def rotation(axis, angle):
    """The 3 x 3 rotation of `angle` radians about `axis`."""
    x, y, z = np.asarray(axis, float) / np.linalg.norm(axis)
    c, s = math.cos(angle), math.sin(angle)
    t = 1.0 - c
    return np.array([[t * x * x + c, t * x * y - s * z, t * x * z + s * y],
                     [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
                     [t * x * z - s * y, t * y * z + s * x, t * z * z + c]])


class Rig:
    """A skinned GLB evaluated in numpy, without an engine: the world transform of every node for a pose, and a mesh's skinned vertices in it."""

    def __init__(self, data):
        self.glb = Glb(data)
        self.doc = self.glb.json
        skin = self.doc["skins"][0]
        self.joints = list(skin["joints"])
        self.names = [self.doc["nodes"][j]["name"] for j in self.joints]
        self.ibm = self.glb.accessor(skin["inverseBindMatrices"]).astype(float).reshape(-1, 4, 4).transpose(0, 2, 1)       # (file order is column-major)
        self.parent = {child: i for i, node in enumerate(self.doc["nodes"]) for child in node.get("children", [])}
        self.meshes = {}
        for name, primitive in softbody._skinned(self.doc).items():
            attributes = primitive["attributes"]
            self.meshes[name] = {"pos": self.glb.accessor(attributes["POSITION"]).astype(float), "jn": self.glb.accessor(attributes["JOINTS_0"]).astype(np.int64),
                                 "wt": self.glb.accessor(attributes["WEIGHTS_0"]).astype(float),
                                 "tris": self.glb.accessor(primitive["indices"]).astype(np.int64).reshape(-1, 3)}

    def world(self, turns=None):
        """{node index: 4 x 4} for a pose: `turns` is {bone name: (axis, angle)}, each turning that bone about its own axes from where it rests (as Godot's `rest * Quaternion(axis, angle)`)."""
        nodes = self.doc["nodes"]
        extra = {self.joints[self.names.index(name)]: rotation(*turn) for name, turn in (turns or {}).items()}
        out = {}

        def get(i):
            if i not in out:
                local = softbody._matrix(nodes[i]).copy()
                if i in extra:
                    local[:3, :3] = local[:3, :3] @ extra[i]
                out[i] = get(self.parent[i]) @ local if i in self.parent else local
            return out[i]
        for i in range(len(nodes)):
            get(i)
        return out

    def skinned(self, mesh, world):
        """(N, 3): the vertices of `mesh` in the pose `world` (four bones a vertex)."""
        m = self.meshes[mesh]
        matrices = np.array([world[j] @ self.ibm[k] for k, j in enumerate(self.joints)])
        homo = np.c_[m["pos"], np.ones(len(m["pos"]))]
        out = np.zeros((len(homo), 3))
        for slot in range(4):
            out += m["wt"][:, slot, None] * np.einsum("nij,nj->ni", matrices[m["jn"][:, slot]], homo)[:, :3]
        return out

    def soft_share(self, mesh, part=None):
        """(N,): the weight of each vertex of `mesh` that sits on the soft-body bones (of `part`, or of all)."""
        m = self.meshes[mesh]
        columns = [i for i, name in enumerate(self.names) if name.startswith("soft_" + (part + "_" if part else ""))]
        return np.where(np.isin(m["jn"], columns), m["wt"], 0.0).sum(axis=1)


def random_turns(rng, names, limit):
    """{bone name: (random axis, random angle up to `limit` radians)} for each of `names`."""
    return {name: (rng.normal(size=3), float(rng.uniform(-limit, limit))) for name in names}
