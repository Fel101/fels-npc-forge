"""Adult features for NPC Forge, shipped as an extension (delete this folder and NPC Forge runs without them). See README.md.

Everything here is hidden until the 18+ box is ticked: About > the π in its lower right (ui/gate.js). Then the "Adult studio" tab, the
"Models" setting and the 3D body's "Nothing (nude)" choice of clothes appear; un-ticking hides them again.
"""
from pathlib import Path

from . import engines, models, safety
from .studio import Forbidden, NotFound, Studio, StudioError, card_of

HERE = Path(__file__).parent

PREFIX = "/api/ext/adult"
NUDE = "nude"                                          # the id of the choice in the Clothes list of a designed body (npcgen/bodymaker.py add_clothes_choice)
NUDE_LABEL = "Nothing (nude)"
NUDE_NOTE = ("The body exactly as the Body Designer made it: no underwear and no clothes. It cannot have swinging hair or cloth (those are added to a body in its underwear). "
             "Listed while the 18+ box is ticked, for characters that pass the studio's rules.")
NPC = r"([a-z0-9][a-z0-9-]{1,80})"
ITEM = r"(s-[0-9a-f]{8})"


def body_problem(npc):
    """Why this character cannot have its 3D body without underwear (None if it can): the studio's own check on the character's card (age 18 or older, nothing that reads as a minor
    or as a real person), the same rules as for its pictures."""
    return safety.card_problem(card_of(npc))


def register(host, studio=None):
    """NPC Forge calls register(host). `studio` is only for tests, which pass one that works inline."""
    studio = studio or Studio(host, root=host.root)
    if hasattr(host, "add_portrait_engine"):              # an NPC Forge that is too old for the hook still loads the studio
        host.add_portrait_engine(engines.make_portrait_engine(lambda: studio.settings()["engines"]["portrait"], host.PortraitFallback))

    def guarded(fn):
        """Turn the studio's errors into the HTTP errors NPC Forge's server understands."""
        def wrapper(q, body, *args):
            try:
                return fn(q, body, *args)
            except NotFound as e:
                raise host.ApiError(404, str(e)) from None
            except Forbidden as e:
                raise host.ApiError(403, str(e)) from None
            except StudioError as e:
                raise host.ApiError(422, str(e)) from None
        wrapper.__name__ = fn.__name__
        return wrapper

    def route(method, pattern):
        def deco(fn):
            host.route(method, PREFIX + pattern)(guarded(fn))
            return fn
        return deco

    @route("GET", "/state")
    def state(q, body):
        return studio.state()

    @route("POST", "/confirm")
    def confirm(q, body):
        return studio.confirm(body.get("confirm_adult"))

    @route("DELETE", "/confirm")
    def unconfirm(q, body):
        return studio.unconfirm()

    @route("PUT", "/settings")
    def settings(q, body):
        return studio.update_settings(body)

    @route("GET", "/characters")
    def characters(q, body):
        studio.require_confirmed()
        return {"characters": studio.characters()}

    @route("GET", f"/characters/{NPC}")
    def character(q, body, npc_id):
        return studio.record(npc_id)

    @route("PUT", f"/characters/{NPC}/notes")
    def notes(q, body, npc_id):
        return studio.set_notes(npc_id, body.get("notes"))

    @route("PUT", f"/characters/{NPC}/body")
    def set_body(q, body, npc_id):
        return studio.set_body(npc_id, body)

    @route("POST", f"/characters/{NPC}/items")
    def add_items(q, body, npc_id):
        return {"items": studio.add_items(npc_id, body.get("scene_ids") or [], body.get("custom") or [])}

    @route("POST", f"/characters/{NPC}/redo-outdated")
    def redo_outdated(q, body, npc_id):
        return {"items": studio.redo_outdated(npc_id)}

    @route("POST", f"/characters/{NPC}/items/{ITEM}/redo")
    def redo(q, body, npc_id, item_id):
        return studio.redo(npc_id, item_id)

    @route("PUT", f"/characters/{NPC}/items/{ITEM}")
    def keep(q, body, npc_id, item_id):
        return studio.set_keep(npc_id, item_id, body.get("keep") is True)

    @route("DELETE", f"/characters/{NPC}/items/{ITEM}")
    def delete(q, body, npc_id, item_id):
        studio.delete_item(npc_id, item_id)
        return {"status": "ok"}

    @route("GET", f"/characters/{NPC}/items/{ITEM}/image")
    def image(q, body, npc_id, item_id):
        path = studio.image_path(npc_id, item_id)
        if not path:
            raise host.ApiError(404, "No picture.")
        return host.Raw(path.read_bytes(), "image/png", {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    # Hidden until the 18+ box is ticked: the studio's tab and page, the Models setting (and so the characters made with it).
    host.add_page("adult", "Adult studio", HERE / "web", visible=studio.confirmed)
    host.add_settings(HERE / "settings", visible=studio.confirmed)
    host.add_guard(models.problem)                       # Models follow Altera's hard rules, whatever is typed
    # The Clothes list of a designed body gets one more choice while the box is ticked: the body without its underwear. NPC Forge only has the neutral hook; what the choice is, who may have it
    # and when it is listed are decided here.
    if hasattr(host, "add_clothes_choice"):              # an NPC Forge that is too old for the hook still loads the studio
        host.add_clothes_choice(NUDE, NUDE_LABEL, NUDE_NOTE, kinds=(), visible=studio.confirmed, check=body_problem)
    # What is always loaded, because it is what unlocks the rest: the π in the About box and the box it opens.
    host.add_ui("adult-ui", HERE / "ui", script="gate.js", style="gate.css")
