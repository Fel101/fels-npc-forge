"""Persistent cast-pack manifests that group existing NPC library records."""
import copy
import json
import os
import re
import secrets
import threading

from .library import now

PACK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,80}$")
TEMPLATES = {"custom", "party", "guild", "town", "ship_crew"}
MAX_MEMBERS = 500
MAX_RELATIONSHIPS = 2000


def _text(value, label, limit, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if required and not value:
        raise ValueError(f"{label} is required")
    if len(value) > limit:
        raise ValueError(f"{label} must be {limit} characters or fewer")
    return value


def _lines(value, label, item_limit, max_items):
    if isinstance(value, str):
        values = value.splitlines()
    elif isinstance(value, list):
        values = value
    elif value is None:
        values = []
    else:
        raise ValueError(f"{label} must be a list of strings")
    result = []
    for item in values:
        item = _text(item, label, item_limit)
        if item:
            result.append(item)
    if len(result) > max_items:
        raise ValueError(f"{label} can have at most {max_items} entries")
    return result


def clean_pack(raw, *, pack_id=None, existing=None, allowed_npc_ids=None):
    """Validate and normalize pack data. `allowed_npc_ids` prevents dangling member and edge references."""
    if not isinstance(raw, dict):
        raise ValueError("Cast pack must be an object")
    raw_members = raw.get("members", [])
    if not isinstance(raw_members, list) or len(raw_members) > MAX_MEMBERS:
        raise ValueError(f"A cast pack must have between 1 and {MAX_MEMBERS} members")
    members, seen = [], set()
    for pos, member in enumerate(raw_members, 1):
        if not isinstance(member, dict):
            raise ValueError(f"Member {pos} must be an object")
        npc_id = member.get("npc_id") or member.get("id")
        if not isinstance(npc_id, str) or not PACK_ID_RE.fullmatch(npc_id):
            raise ValueError(f"Member {pos} needs a valid npc_id")
        if npc_id in seen:
            raise ValueError(f"NPC {npc_id} appears more than once in this pack")
        if allowed_npc_ids is not None and npc_id not in allowed_npc_ids:
            raise ValueError(f"No NPC with id {npc_id}")
        seen.add(npc_id)
        members.append({
            "npc_id": npc_id,
            "role": _text(member.get("role"), f"Member {pos} role", 120),
            "notes": _text(member.get("notes"), f"Member {pos} notes", 1000),
        })
    if not members:
        raise ValueError("A cast pack needs at least one NPC")

    prior = existing or {}
    unique_roles = raw.get("unique_roles", prior.get("unique_roles", False))
    if not isinstance(unique_roles, bool):
        raise ValueError("unique_roles must be true or false")
    if unique_roles:
        roles = set()
        for member in members:
            role = member["role"].casefold()
            if role and role in roles:
                raise ValueError(f'Member role "{member["role"]}" appears more than once in this pack')
            roles.add(role)

    raw_relationships = raw.get("relationships", [])
    if not isinstance(raw_relationships, list) or len(raw_relationships) > MAX_RELATIONSHIPS:
        raise ValueError(f"A cast pack can have at most {MAX_RELATIONSHIPS} relationships")
    relationships = []
    for pos, edge in enumerate(raw_relationships, 1):
        if not isinstance(edge, dict):
            raise ValueError(f"Relationship {pos} must be an object")
        source = edge.get("from", edge.get("source_id"))
        target = edge.get("to", edge.get("target_id"))
        if (not isinstance(source, str) or not isinstance(target, str)
                or source not in seen or target not in seen):
            raise ValueError(f"Relationship {pos} must connect NPCs in this pack")
        if source == target:
            raise ValueError(f"Relationship {pos} must connect two different NPCs")
        relationships.append({
            "from": source,
            "to": target,
            "type": _text(edge.get("type"), f"Relationship {pos} type", 120, required=True),
            "notes": _text(edge.get("notes"), f"Relationship {pos} notes", 1000),
        })

    template = raw.get("template", prior.get("template", "custom"))
    if not isinstance(template, str) or template not in TEMPLATES:
        raise ValueError(f"Unknown cast pack template '{template}'")
    created = prior.get("created") or now()
    return {
        "id": pack_id or prior.get("id") or f"cast-pack-{secrets.token_hex(5)}",
        "name": _text(raw.get("name", prior.get("name")), "Cast pack name", 100, required=True),
        "template": template,
        "shared_context": _text(raw.get("shared_context"), "Shared context", 4000),
        "unique_roles": unique_roles,
        "uniqueness_constraints": _lines(raw.get("uniqueness_constraints"), "Uniqueness constraints", 300, 40),
        "rumors": _lines(raw.get("rumors"), "Rumors", 500, 40),
        "plot_hooks": _lines(raw.get("plot_hooks"), "Plot hooks", 1000, 40),
        "members": members,
        "relationships": relationships,
        "created": created,
        "updated": now(),
    }


class CastPacks:
    """Small atomic JSON store scoped to one NPC library."""
    def __init__(self, library_root):
        self.path = library_root / ".npcforge-cast-packs.json"
        self.lock = threading.RLock()
        self._packs = {}
        if self.path.exists():
            data = json.loads(self.path.read_text("utf-8"))
            if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
                raise ValueError(f"Invalid cast pack store: {self.path}")
            self._packs = {item["id"]: item for item in data if isinstance(item.get("id"), str)}

    def _persist(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(list(self._packs.values()), indent=2, ensure_ascii=False), "utf-8")
        os.replace(tmp, self.path)

    def list(self):
        with self.lock:
            return copy.deepcopy(sorted(self._packs.values(), key=lambda item: item.get("updated", ""), reverse=True))

    def get(self, pack_id):
        with self.lock:
            if pack_id not in self._packs:
                raise KeyError(pack_id)
            return copy.deepcopy(self._packs[pack_id])

    def create(self, raw, allowed_npc_ids):
        with self.lock:
            pack = clean_pack(raw, allowed_npc_ids=allowed_npc_ids)
            while pack["id"] in self._packs:
                pack["id"] = f"cast-pack-{secrets.token_hex(5)}"
            self._packs[pack["id"]] = pack
            try:
                self._persist()
            except Exception:
                self._packs.pop(pack["id"], None)
                raise
            return copy.deepcopy(pack)

    def update(self, pack_id, raw, allowed_npc_ids):
        with self.lock:
            if pack_id not in self._packs:
                raise KeyError(pack_id)
            prior = self._packs[pack_id]
            pack = clean_pack(raw, pack_id=pack_id, existing=prior, allowed_npc_ids=allowed_npc_ids)
            self._packs[pack_id] = pack
            try:
                self._persist()
            except Exception:
                self._packs[pack_id] = prior
                raise
            return copy.deepcopy(pack)

    def delete(self, pack_id):
        with self.lock:
            if pack_id not in self._packs:
                raise KeyError(pack_id)
            prior = self._packs.pop(pack_id)
            try:
                self._persist()
            except Exception:
                self._packs[pack_id] = prior
                raise

    def remove_npc(self, npc_id):
        """Keep all manifests valid when a member NPC is deleted."""
        with self.lock:
            changed = False
            previous = copy.deepcopy(self._packs)
            for pack in list(self._packs.values()):
                members = [member for member in pack["members"] if member["npc_id"] != npc_id]
                if len(members) == len(pack["members"]):
                    continue
                member_ids = {member["npc_id"] for member in members}
                if not members:
                    del self._packs[pack["id"]]
                else:
                    pack["members"] = members
                    pack["relationships"] = [edge for edge in pack["relationships"]
                                              if edge["from"] in member_ids and edge["to"] in member_ids]
                    pack["updated"] = now()
                changed = True
            if changed:
                try:
                    self._persist()
                except Exception:
                    self._packs = previous
                    raise
