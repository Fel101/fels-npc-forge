extends Node
## Attach to a node, assign audio_player, then load_clip(json_path, wav_path).
## weights_changed can drive portrait textures or a custom facial rig.
signal weights_changed(weights: Dictionary, seconds: float)

@export var audio_player: AudioStreamPlayer
@export var face_mesh: MeshInstance3D
@export var mouth_sprite: AnimatedSprite2D
@export var sprite_animation: StringName = &"mouth"
@export var sprite_frames: Dictionary = {"sil": 0, "PP": 1, "FF": 2, "TH": 3,
    "DD": 4, "kk": 5, "CH": 6, "SS": 7, "nn": 8, "RR": 9, "aa": 10,
    "E": 11, "ih": 12, "oh": 13, "ou": 14, "mouth_open": 10}
@export var targets: Dictionary = {"sil": "", "PP": "viseme_PP", "FF": "viseme_FF",
    "TH": "viseme_TH", "DD": "viseme_DD", "kk": "viseme_kk", "CH": "viseme_CH",
    "SS": "viseme_SS", "nn": "viseme_nn", "RR": "viseme_RR", "aa": "viseme_aa",
    "E": "viseme_E", "ih": "viseme_ih", "oh": "viseme_oh", "ou": "viseme_ou", "mouth_open": "jawOpen"}
var clip: Dictionary = {}
var _last_targets: Dictionary = {}
var _last_time: float = 0.0

func load_sprite_rig(json_path: String, pack_root: String) -> Error:
    ## Build a real mouth atlas from exported PNG frames and artist mappings.
    if not mouth_sprite:
        return ERR_UNCONFIGURED
    var parsed = JSON.parse_string(FileAccess.get_file_as_string(json_path))
    if not parsed is Dictionary or parsed.get("schema") != 1 or not parsed.get("frames") is Dictionary:
        return ERR_INVALID_DATA
    var atlas := SpriteFrames.new()
    atlas.add_animation(sprite_animation)
    var indices: Dictionary = {}
    for name in parsed.frames:
        var frame: Dictionary = parsed.frames[name]
        var path: String = pack_root.path_join(str(frame.file))
        if FileAccess.get_sha256(path) != frame.sha256:
            return ERR_FILE_CORRUPT
        var image := Image.load_from_file(path)
        if not image or image.is_empty():
            return ERR_INVALID_DATA
        indices[name] = atlas.get_frame_count(sprite_animation)
        atlas.add_frame(sprite_animation, ImageTexture.create_from_image(image))
    var mapped: Dictionary = {}
    for group in parsed.mapping:
        if not indices.has(parsed.mapping[group]):
            return ERR_INVALID_DATA
        mapped[group] = indices[parsed.mapping[group]]
    if not indices.has(parsed.amplitude_open) or not mapped.has("sil"):
        return ERR_INVALID_DATA
    mapped["mouth_open"] = indices[parsed.amplitude_open]
    mouth_sprite.stop()
    mouth_sprite.sprite_frames = atlas
    sprite_frames = mapped
    apply_time(-1)
    return OK

func load_clip(json_path: String, wav_path: String) -> Error:
    if not audio_player:
        return ERR_UNCONFIGURED
    stop()
    clip = {}
    var parsed = JSON.parse_string(FileAccess.get_file_as_string(json_path))
    if not parsed is Dictionary or parsed.get("schema") != 1:
        return ERR_INVALID_DATA
    if FileAccess.get_sha256(wav_path) != parsed.get("audio_sha256", ""):
        push_error("Forge lip-sync WAV hash mismatch; keep the original delivered WAV.")
        return ERR_FILE_CORRUPT
    if not parsed.get("frames") is Array or parsed.frames.is_empty() or float(parsed.get("seconds", 0)) <= 0:
        return ERR_INVALID_DATA
    var previous: float = -1.0
    for frame in parsed.frames:
        if not frame is Dictionary or not frame.get("weights") is Dictionary:
            return ERR_INVALID_DATA
        var time: float = float(frame.get("time", -1))
        if not is_finite(time) or time <= previous or time > float(parsed.seconds) + 0.000001:
            return ERR_INVALID_DATA
        previous = time
        for value in frame.weights.values():
            if not is_finite(float(value)) or float(value) < 0 or float(value) > 1:
                return ERR_INVALID_DATA
    var stream := AudioStreamWAV.load_from_file(wav_path)
    if not stream or absf(stream.get_length() - float(parsed.seconds)) > 0.0001:
        return ERR_INVALID_DATA
    audio_player.stream = stream
    clip = parsed
    apply_time(0)
    return OK

func weights_at(seconds: float) -> Dictionary:
    if clip.is_empty() or seconds < 0 or seconds >= float(clip.seconds):
        return {"sil": 1.0}
    # Search stored seconds; rounded sample timestamps need not equal index/fps.
    var low: int = 0
    var high: int = clip.frames.size()
    while low < high:
        var middle: int = (low + high) / 2
        if float(clip.frames[middle].time) <= seconds:
            low = middle + 1
        else:
            high = middle
    return clip.frames[maxi(0, low - 1)].weights.duplicate()

func apply_time(seconds: float) -> void:
    var weights := weights_at(seconds)
    var mapped: Dictionary = {}
    for group in targets:
        var name: String = str(targets[group])
        if not name.is_empty():
            mapped[name] = minf(1.0, float(mapped.get(name, 0)) + float(weights.get(group, 0)))
    if face_mesh and face_mesh.mesh:
        for old_name in _last_targets:
            if not mapped.has(old_name):
                mapped[old_name] = 0.0
        for name in mapped:
            var index := face_mesh.find_blend_shape_by_name(name)
            if index >= 0:
                face_mesh.set_blend_shape_value(index, mapped[name])
    _last_targets = mapped
    if mouth_sprite and mouth_sprite.sprite_frames and mouth_sprite.sprite_frames.has_animation(sprite_animation):
        var scores: Dictionary = {}
        for group in weights:
            var index: int = int(sprite_frames.get(group, sprite_frames.get("sil", 0)))
            scores[index] = float(scores.get(index, 0)) + float(weights[group])
        var chosen: int = int(sprite_frames.get("sil", 0))
        for index in scores:
            if float(scores[index]) > float(scores.get(chosen, 0)):
                chosen = index
        mouth_sprite.stop()
        mouth_sprite.animation = sprite_animation
        mouth_sprite.frame = clampi(chosen, 0,
            mouth_sprite.sprite_frames.get_frame_count(sprite_animation) - 1)
    weights_changed.emit(weights, seconds)

func play(from_seconds: float = 0.0) -> void:
    if not clip.is_empty() and audio_player:
        _last_time = clampf(from_seconds, 0, float(clip.seconds))
        audio_player.stream_paused = false
        audio_player.play(_last_time)
        apply_time(_last_time)

func seek(seconds: float) -> void:
    if not clip.is_empty() and audio_player:
        _last_time = clampf(seconds, 0, float(clip.seconds))
        audio_player.seek(_last_time)
        apply_time(_last_time)

func stop() -> void:
    if audio_player:
        audio_player.stop()
    _last_time = 0.0
    apply_time(-1)

func _process(_delta: float) -> void:
    if clip.is_empty() or not audio_player:
        return
    if audio_player.stream_paused:
        return
    if not audio_player.playing:
        apply_time(-1)
    else:
        _last_time = maxf(0, audio_player.get_playback_position() +
            (AudioServer.get_time_since_last_mix() - AudioServer.get_output_latency()) * audio_player.pitch_scale)
        apply_time(_last_time)
