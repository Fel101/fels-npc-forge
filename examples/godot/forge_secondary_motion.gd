class_name ForgeSecondaryMotion
extends RefCounted
## Swinging hair and cloth, and soft-body motion, for a character made by NPC Forge, in a Godot that has SpringBoneSimulator3D (checked on 4.7.2 only:
## the editor import and the run-time loader, a Dance clip, three characters with Low and Medium skeletons). See the Body Designer add-on's notes on swinging hair and cloth and "Soft-body motion".
##
## A character built with "Swinging Long hair" and/or "Swinging skirt or dress" carries extra bones in its skeleton
## (secondary_hair_NN_SS, secondary_skirt_NN_SS, secondary_dress_NN_SS) and, on the first bone of each chain, the settings
## NPC Forge suggests for them (stiffness, drag, gravity, radius, length of the free end). Godot delivers those as
## bone metadata under "extras". This script reads them and makes one SpringBoneSimulator3D with one setting per chain,
## so the hair and the hem move with the character. Nothing in the file moves by itself: the simulation is Godot's.
##
## Use:
##     var model := preload("res://characters/ayla.glb").instantiate()
##     add_child(model)
##     ForgeSecondaryMotion.attach(model.find_child("Skeleton3D", true, false))
##
## A character built with "Soft-body motion" (the female body: jiggle) has more kinds of chain: spring bones for the chest, the butt, the belly and the thighs (soft_chest_l_01, soft_chest_l_02, ...
## soft_butt_r_02, soft_belly_c_01, soft_belly_c_02, soft_thighs_l_01, ... soft_thighs_r_02).
## They are made the same way, with the settings on the first bone of each chain (its kind is "chest", "butt", "belly" or "thighs"), and also `max_angle_deg`, how far the bone may turn from where it rests,
## and, for the belly and the thighs, `center_bone` ("root"): the bone whose pose the simulator takes as the centre of the movement (a chain without it uses the bone it hangs on). In Godot 4.7 a
## bone as the centre multiplies that bone's turn by its distance from the skeleton's origin, which a thigh's swing makes violent; the root bone does not move in any clip, so the chains move only
## by what the animation does to the body.
## Godot's simulator has no angle limit, so attach() adds a small modifier after it (ForgeSoftLimit) that pulls a bone back to that angle. Measured on all 85 clips of the animation library
## (Godot 4.7.2, 60 steps a second): the end of a chain stays within 27.6 mm (chest) and 19.1 mm (butt) of where the animation alone puts it, the median clip within 2.1 and 2.6 mm; without the
## modifier the worst clips (a sword combo, a roll) take it as far as 96 mm (chest) and 99 mm (butt). The skin around the bust and the seat follows these bones (npcgen/softbody.py), and while
## they are not simulated it follows the body as before, to within a few millimetres.
## The simulator uses the frame's own time step, so the size of the movement depends on the frame rate: the end of the chest chain in Jog_Fwd_Loop gets up to 10 mm from where the animation alone
## puts it at 144 frames a second, 14 mm at 60, 18 mm at 30 and 26 mm at 10 (the butt: 8.5, 17, 30 and 60 mm). The numbers in the Body Designer add-on's notes are for 60. A game that wants the same jiggle on
## every machine could step its physics at a fixed rate (not tried here); slow motion and a low frame rate show more movement, not less.
## To change how strong it is while the game runs, attach the parts one at a time and set each simulator's influence (0: still, 1: as built):
##     var chest := ForgeSecondaryMotion.attach(skeleton, PackedStringArray(["chest"]))
##     var butt := ForgeSecondaryMotion.attach(skeleton, PackedStringArray(["butt"]))
##     chest.influence = 0.6
##
## The suggested numbers are a starting point. Tune them per game, for example
##     var spring := ForgeSecondaryMotion.attach(skeleton)
##     spring.set_stiffness(0, 1.5)        # setting 0 is the first chain; lower stiffness swings more
## Hair or cloth can pass into the body when it swings far: Godot's simulator does not know the body. Add
## SpringBoneCollisionSphere3D / SpringBoneCollisionCapsule3D shapes (head, neck, torso, thighs) to the
## simulator's collisions if you need that to stop.

## Returns the simulator that was added to the skeleton, or null when the character has no swinging chains. `kinds` limits it to some of them
## ("hair", "skirt", "dress", "chest", "butt", "belly", "thighs"); empty means all.
static func attach(skeleton: Skeleton3D, kinds: PackedStringArray = PackedStringArray()) -> SpringBoneSimulator3D:
	if skeleton == null:
		return null
	var chains := chains_of(skeleton)
	if not kinds.is_empty():
		chains = chains.filter(func(chain): return kinds.has(String(chain.get("kind", ""))))
	if chains.is_empty():
		return null
	var spring := SpringBoneSimulator3D.new()
	spring.name = "ForgeSecondaryMotion"
	skeleton.add_child(spring)
	spring.setting_count = chains.size()
	var limits: Array = []
	for i in chains.size():
		var chain: Dictionary = chains[i]
		var bones: Array = chain["bones"]
		spring.set_root_bone_name(i, bones[0])
		spring.set_end_bone_name(i, bones[bones.size() - 1])
		spring.set_center_from(i, SpringBoneSimulator3D.CENTER_FROM_BONE)
		spring.set_center_bone_name(i, chain.get("center_bone", chain["parent"]))
		spring.set_extend_end_bone(i, true)
		spring.set_end_bone_direction(i, SkeletonModifier3D.BONE_DIRECTION_PLUS_Y)
		spring.set_end_bone_length(i, chain["end_length_m"])
		spring.set_stiffness(i, chain["stiffness"])
		spring.set_drag(i, chain["drag"])
		spring.set_gravity(i, chain["gravity"])
		var down: Array = chain["gravity_direction"]
		spring.set_gravity_direction(i, Vector3(down[0], down[1], down[2]))
		spring.set_radius(i, chain["radius_m"])
		if chain.has("max_angle_deg"):
			for bone_name in bones:
				var bone := skeleton.find_bone(bone_name)
				if bone >= 0:
					limits.append({"bone": bone, "limit": deg_to_rad(float(chain["max_angle_deg"]))})
	if not limits.is_empty():
		var limiter := SoftLimit.new()
		limiter.name = "ForgeSoftLimit"
		limiter.entries = limits
		skeleton.add_child(limiter)                         # (after the simulator: modifiers run in the order they are children)
	return spring


## The chains a skeleton carries: one dictionary per chain (kind, chain, bones, parent, stiffness, drag, gravity, ...).
static func chains_of(skeleton: Skeleton3D) -> Array:
	var chains: Array = []
	for bone in skeleton.get_bone_count():
		if not skeleton.has_bone_meta(bone, &"extras"):
			continue
		var extras = skeleton.get_bone_meta(bone, &"extras")
		if typeof(extras) == TYPE_DICTIONARY and extras.has("forge_secondary"):
			chains.append(extras["forge_secondary"])
	return chains


## Keeps the bones of the soft body (chest, butt, belly, thighs) from swinging farther than the file says: Godot's simulator has no angle limit, and in a sword combo or a roll a free spring bone
## gets up to 99 mm from its place in the trials. It runs after the simulator and pulls any bone that has turned more than its limit (the chain's `max_angle_deg`) back to the limit.
class SoftLimit extends SkeletonModifier3D:
	var entries: Array = []                        # [{bone: index, limit: radians}]
	var max_angle_deg := -1.0                      # above zero: one limit for all (for trying values out)

	func _process_modification() -> void:
		var skeleton := get_skeleton()
		if skeleton == null:
			return
		for entry in entries:
			var bone: int = entry["bone"]
			var limit: float = deg_to_rad(max_angle_deg) if max_angle_deg > 0.0 else entry["limit"]
			var rest: Quaternion = skeleton.get_bone_rest(bone).basis.get_rotation_quaternion()
			var pose: Quaternion = skeleton.get_bone_pose_rotation(bone)
			var angle := rest.angle_to(pose)
			if angle > limit:
				skeleton.set_bone_pose_rotation(bone, rest.slerp(pose, limit / angle))
