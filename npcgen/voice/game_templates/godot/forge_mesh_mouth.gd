extends SkeletonModifier3D
## Child of the imported Skeleton3D; runs after its AnimationMixer pose.
## Connect ForgeLipPlayer.weights_changed to set_weights. Supply the model root.
@export var model_root: Node3D
var config: Dictionary = {}
var weights: Dictionary = {"sil": 1.0}
var _jaw: int = -1
var _meshes: Array[MeshInstance3D] = []

func load_rig(json_path: String, model_path: String) -> Error:
    var parsed = JSON.parse_string(FileAccess.get_file_as_string(json_path))
    if not parsed is Dictionary or parsed.get("schema") != 1 or not parsed.get("config") is Dictionary:
        return ERR_INVALID_DATA
    if FileAccess.get_sha256(model_path) != parsed.get("model_sha256", ""):
        return ERR_FILE_CORRUPT
    var skeleton := get_skeleton()
    if not skeleton or not model_root:
        return ERR_UNCONFIGURED
    var candidate: Dictionary = parsed.config
    var jaw_name: String = str(candidate.get("jaw_bone", ""))
    var index: int = skeleton.find_bone(jaw_name) if not jaw_name.is_empty() else -1
    if not jaw_name.is_empty() and index < 0:
        return ERR_DOES_NOT_EXIST
    if not candidate.get("mapping") is Dictionary or not candidate.get("jaw_amounts") is Dictionary:
        return ERR_INVALID_DATA
    var angle: float = float(candidate.get("jaw_radians", -1))
    var direction: float = float(candidate.get("jaw_sign", 0))
    if not is_finite(angle) or angle < 0 or angle > 0.6 or candidate.get("jaw_axis") not in ["x", "y", "z"] or (direction != -1.0 and direction != 1.0):
        return ERR_INVALID_DATA
    _meshes.clear()
    _collect_meshes(model_root)
    for target in candidate.mapping.values() + [candidate.get("amplitude_target", "")]:
        if str(target).is_empty():
            continue
        var found := false
        for mesh in _meshes:
            found = found or mesh.find_blend_shape_by_name(str(target)) >= 0
        if not found:
            return ERR_DOES_NOT_EXIST
    config = candidate.duplicate(true)
    _jaw = index
    set_weights({"sil": 1.0}, -1)
    return OK

func _collect_meshes(node: Node) -> void:
    if node is MeshInstance3D and node.mesh:
        _meshes.append(node)
    for child in node.get_children():
        _collect_meshes(child)

func set_weights(value: Dictionary, _seconds: float = 0) -> void:
    weights = value.duplicate()
    if config.is_empty():
        return
    var mapped: Dictionary = {}
    for group in config.mapping:
        var target: String = str(config.mapping[group])
        if not target.is_empty():
            mapped[target] = minf(1.0, float(mapped.get(target, 0)) + float(weights.get(group, 0)))
    var fallback: String = str(config.amplitude_target)
    if not fallback.is_empty():
        mapped[fallback] = minf(1.0, float(mapped.get(fallback, 0)) + float(weights.get("mouth_open", 0)))
    for mesh in _meshes:
        for target in mapped:
            var index := mesh.find_blend_shape_by_name(target)
            if index >= 0:
                mesh.set_blend_shape_value(index, mapped[target])

func _process_modification() -> void:
    if config.is_empty() or not config.enabled or _jaw < 0:
        return
    var amount: float = 0
    for group in weights:
        amount += float(weights[group]) * (1.0 if group == "mouth_open" else float(config.jaw_amounts.get(group, 0)))
    var axis := Vector3.RIGHT if config.jaw_axis == "x" else Vector3.UP if config.jaw_axis == "y" else Vector3.BACK
    var delta := Quaternion(axis, clampf(amount, 0, 1) * float(config.jaw_radians) * float(config.jaw_sign))
    var skeleton := get_skeleton()
    skeleton.set_bone_pose_rotation(_jaw, skeleton.get_bone_pose_rotation(_jaw) * delta)
