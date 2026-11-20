"""scripts/install_pixal_head_node.py: NPC Forge's own ComfyUI nodes copied into a ComfyUI install."""
import importlib.util
import pathlib
import sys

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "install_pixal_head_node.py"
spec = importlib.util.spec_from_file_location("install_pixal_head_node", SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.fixture()
def comfy(tmp_path):
    root = tmp_path / "ComfyUI"
    (root / "custom_nodes").mkdir(parents=True)
    (root / "comfy_api" / "latest").mkdir(parents=True)
    return root


def run(monkeypatch, comfy_dir, *flags):
    monkeypatch.setattr(sys, "argv", ["install_pixal_head_node.py", str(comfy_dir), *flags])
    installer.main()


def installed(comfy_dir):
    return sorted(p.name for p in (comfy_dir / "custom_nodes").iterdir())


def test_by_default_only_the_head_texture_node_is_installed(comfy, monkeypatch, capsys):
    run(monkeypatch, comfy)
    assert installed(comfy) == ["npcforge_pixal_head_texture"]
    assert "Restart ComfyUI" in capsys.readouterr().out


def test_the_trellis2_decode_node_is_installed_when_asked_and_both_match_the_repository(comfy, monkeypatch):
    run(monkeypatch, comfy, "--trellis2")
    assert installed(comfy) == ["npcforge_pixal_head_texture", "npcforge_trellis2_decode"]
    for node in installer.NODES.values():
        assert (comfy / "custom_nodes" / node["folder"] / "__init__.py").read_text("utf-8") == node["source"].read_text("utf-8")


def test_running_it_again_changes_nothing_and_does_not_ask_for_a_restart(comfy, monkeypatch, capsys):
    run(monkeypatch, comfy, "--trellis2")
    capsys.readouterr()
    run(monkeypatch, comfy, "--trellis2")
    out = capsys.readouterr().out
    assert out.count("already current") == 2 and "Restart" not in out


def test_an_older_copy_of_our_own_node_is_replaced(comfy, monkeypatch, capsys):
    node = installer.NODES["trellis2"]
    target = comfy / "custom_nodes" / node["folder"] / "__init__.py"
    target.parent.mkdir()
    target.write_text(node["marker"] + "\nOLD = True\n", encoding="utf-8")
    run(monkeypatch, comfy, "--trellis2")
    assert "OLD" not in target.read_text("utf-8") and "Installed NPC Forge Trellis2 shape-decode node" in capsys.readouterr().out


def test_someone_elses_node_in_the_same_folder_is_never_replaced(comfy, monkeypatch):
    target = comfy / "custom_nodes" / installer.NODES["trellis2"]["folder"] / "__init__.py"
    target.parent.mkdir()
    target.write_text("# somebody else's node\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        run(monkeypatch, comfy, "--trellis2")
    assert target.read_text("utf-8") == "# somebody else's node\n"


def test_a_folder_that_is_not_comfyui_is_refused(tmp_path, monkeypatch):
    with pytest.raises(SystemExit):
        run(monkeypatch, tmp_path)


def test_every_marker_is_the_first_line_of_its_source():
    for node in installer.NODES.values():
        assert node["source"].read_text("utf-8").splitlines()[0] == node["marker"]


# ---- the decode node itself, with stand-ins for torch and ComfyUI (neither is in this environment) ----------------------------

class Tensor:
    """Records what is done to it, so the conversion to ComfyUI's mesh layout can be checked without torch."""

    def __init__(self, name, calls=()):
        self.name, self.calls = name, tuple(calls)

    def _then(self, step):
        return Tensor(self.name, self.calls + (step,))

    def detach(self):
        return self._then("detach")

    def float(self):
        return self._then("float")

    def to(self, dtype):
        return self._then(f"to({dtype})")

    def cpu(self):
        return self._then("cpu")

    def unsqueeze(self, dim):
        return self._then(f"unsqueeze({dim})")


def test_the_decode_node_hands_the_mesh_on_in_both_types_without_touching_cumesh(monkeypatch):
    import types

    torch = types.ModuleType("torch")
    torch.int64 = "int64"
    torch.no_grad = lambda: (lambda fn: fn)
    api = types.ModuleType("comfy_api")
    latest = types.ModuleType("comfy_api.latest")
    latest.Types = types.SimpleNamespace(MESH=lambda vertices, faces: ("MESH", vertices, faces))
    api.latest = latest
    pack = types.ModuleType("fake_trellis_pack")
    pack.MeshWithVoxel = lambda *args, **kwargs: ("MESHWITHVOXEL", args, kwargs)
    for name, module in (("torch", torch), ("comfy_api", api), ("comfy_api.latest", latest), ("fake_trellis_pack", pack)):
        monkeypatch.setitem(sys.modules, name, module)

    node_spec = importlib.util.spec_from_file_location("decode_node", installer.NODES["trellis2"]["source"])
    node_module = importlib.util.module_from_spec(node_spec)
    node_spec.loader.exec_module(node_module)

    class Pipeline:
        low_vram, pbr_attr_layout, cleaned = True, "layout", 0
        __module__ = "fake_trellis_pack"

        def decode_shape_slat(self, slat, resolution, use_tiled=True):
            self.asked = (slat, resolution, use_tiled)
            return [types.SimpleNamespace(vertices=Tensor("v"), faces=Tensor("f"))], ["subs"]

        def _cleanup_cuda(self):
            self.cleaned += 1

        # no fill_holes anywhere: calling it would be the crash this node exists to avoid

    pipe = Pipeline()
    node = node_module.NPCForgeTrellis2ShapeDecode()
    packed, core = node.process(pipe, "slat", 1024, False)

    assert node.RETURN_TYPES == ("MESHWITHVOXEL", "MESH") and node.RETURN_NAMES == ("mesh_with_voxel", "mesh")
    assert pipe.asked == ("slat", 1024, False) and pipe.cleaned == 1
    assert packed[0] == "MESHWITHVOXEL" and packed[2]["voxel_size"] == 1 / 1024 and packed[2]["layout"] == "layout"
    assert packed[2]["origin"] == [-0.5, -0.5, -0.5]
    assert core[0] == "MESH"
    assert core[1].calls == ("detach", "float", "cpu", "unsqueeze(0)")                 # batched float32 on the CPU, as ComfyUI's mesh nodes take it
    assert core[2].calls == ("detach", "to(int64)", "cpu", "unsqueeze(0)")
