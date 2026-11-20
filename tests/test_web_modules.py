import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"
MODULES = ("app.js", "advanced.js", "core.js", "library-view.js", "licences-view.js", "queue-view.js", "scenes-view.js", "voice-designer.js", "voice-library-view.js", "voice-studio-view.js", "voice-effects-controls.js", "voice-lip-sync-view.js", "viseme-track.js", "character-rig-player.js", "character-rig-editor.js", "mesh-mouth-player.js", "mesh-rig-editor.js", "mesh-mouth-preview.js", "soft-solver.js", "soft-preview.js")


def test_mesh_mouth_overlay_preserves_baseline_and_never_accumulates_rotation():
    script="""const {MeshMouthPlayer}=await import(requirePath);
    const assert=(await import('node:assert/strict')).default;
    const jaw={rotation:{x:.2,y:0,z:0}};
    const mesh={morphTargetDictionary:{aa:0,E:1,unrelated:2},morphTargetInfluences:[0,0,.7]};
    const actor={bones:{jaw},mover:{traverse:fn=>fn(mesh)}};
    const rig={status:'ready',config:{mapping:{sil:'',aa:'aa',E:'E'},amplitude_target:'aa',jaw_bone:'jaw',jaw_axis:'x',jaw_radians:.28,jaw_sign:1,jaw_amounts:{sil:0,aa:1,E:.5}}};
    const player=new MeshMouthPlayer();
    for(let i=0;i<100;i++) {
      player.prepare(actor);jaw.rotation.x+=.9*.28;
      player.apply(actor,rig,{aa:1},.9);
      assert.ok(Math.abs(jaw.rotation.x-.48)<.0000001);
      assert.deepEqual(mesh.morphTargetInfluences,[1,0,.7]);
    }
    player.prepare(actor);player.apply(actor,rig,{sil:1});
    assert.ok(Math.abs(jaw.rotation.x-.2)<.0000001);
    assert.deepEqual(mesh.morphTargetInfluences,[0,0,.7]);
    player.clear();assert.ok(Math.abs(jaw.rotation.x-.2)<.0000001);
    """.replace('requirePath',repr((WEB/'mesh-mouth-player.js').as_uri()))
    result=subprocess.run(['node','--input-type=module','-e',script],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr


def test_character_sprite_aliases_and_amplitude_fallback_select_real_frame():
    script = """const {rigFrameWeights,mouthWeightsAt} = await import(requirePath);
    const assert = (await import('node:assert/strict')).default;
    const rig = {mapping:{sil:'rest',PP:'rest',aa:'open',E:'open'},amplitude_open:'open',interpolation:'dominant'};
    assert.deepEqual(rigFrameWeights(rig,{aa:1}),{open:1});
    assert.deepEqual(rigFrameWeights(rig,{sil:.4,aa:.3,E:.3}),{open:1});
    assert.deepEqual(rigFrameWeights(rig,{sil:1}),{rest:1});
    assert.deepEqual(mouthWeightsAt({status:'amplitude_fallback',seconds:1,mouth_fps:25},[.4],0),{sil:.6,mouth_open:.4});
    assert.deepEqual(mouthWeightsAt({seconds:1},[1],1),{sil:1});
    """.replace('requirePath', repr((WEB/'character-rig-player.js').as_uri()))
    checked = subprocess.run(['node','--input-type=module','-e',script],capture_output=True,text=True,timeout=30)
    assert checked.returncode == 0,checked.stderr


def test_viseme_preview_holds_samples_at_stored_rounded_timestamps():
    script = """const {visemeWeightsAt} = await import(requirePath);
    const assert = (await import('node:assert/strict')).default;
    const track = {fps:60,frames:[{time:0,weights:{sil:1}},
      {time:.016667,weights:{FF:1}},{time:.033333,weights:{PP:1}},
      {time:.05,weights:{sil:1}}]};
    assert.deepEqual(visemeWeightsAt(track,.033333),{PP:1});
    assert.deepEqual(visemeWeightsAt(track,.033332),{FF:1});
    assert.deepEqual(visemeWeightsAt(track,.049999),{PP:1});
    assert.deepEqual(visemeWeightsAt(track,1),{sil:1});
    assert.deepEqual(visemeWeightsAt(null,0),{sil:1});
    """.replace('requirePath', repr((WEB / 'viseme-track.js').as_uri()))
    checked = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True, timeout=30)
    assert checked.returncode == 0, checked.stderr
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def test_browser_modules_are_syntax_valid_and_all_imports_exist():
    for name in MODULES:
        path = WEB / name
        checked = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, timeout=30)
        assert checked.returncode == 0, checked.stderr
        for imported in re.findall(r"from\s+['\"](\./[^'\"]+)['\"]", path.read_text("utf-8")):
            assert (path.parent / imported).resolve().is_file(), (name, imported)


def used_but_never_imported(text, exported):
    """The names a module calls like core.js's helpers (toast(...), api(...)) but neither imports nor declares: every occurrence of the name in
    it is a bare call. That is a ReferenceError waiting for the one click that reaches it (library-view.js called toast() without importing it)."""
    missing = []
    for name in exported:
        calls = len(re.findall(rf"(?<![\w$.])(?<!function ){re.escape(name)}\(", text))
        mentions = len(re.findall(rf"(?<![\w$.]){re.escape(name)}(?![\w$])", text))
        if calls and mentions == calls:
            missing.append(name)
    return missing


def test_a_helper_a_page_module_calls_from_core_is_imported_there():
    core = (WEB / "core.js").read_text("utf-8")
    exported = re.findall(r"^export (?:async )?(?:function|const) ([A-Za-z_]\w*)", core, re.M)
    assert {"toast", "api", "safe", "btn", "h"} <= set(exported)
    for path in sorted(WEB.glob("*.js")):
        if path.name != "core.js":
            assert used_but_never_imported(path.read_text("utf-8"), exported) == [], path.name
    # and the check does see the mistake it exists for
    forgotten = "import { api, h } from './core.js';\nexport const x = () => { toast('saved'); return api('GET', '/'); };\n"
    assert used_but_never_imported(forgotten, exported) == ["toast"]
    defined = "function toast(m) { return m; }\nexport const x = () => toast('saved');\n"
    assert used_but_never_imported(defined, exported) == []


def test_the_viewers_meshopt_decoder_is_a_script_the_viewer_can_load():
    """model-viewer adds the decoder to the page as a classic <script>, so a file with an `export` statement fails there with a SyntaxError (it did)."""
    wanted = re.search(r"meshoptDecoderLocation\s*=\s*`\$\{VIEWER_BASE\}([^`]+)`", (WEB / "model-preview.js").read_text("utf-8"))
    assert wanted, "model-preview.js no longer sets meshoptDecoderLocation"
    path = WEB / "vendor" / "model-viewer" / wanted.group(1)
    assert path.is_file(), path
    # (`node --check` would let an `export` through: current Node detects a module by its syntax. vm.Script parses what a <script> element does.)
    parse = "new (require('vm').Script)(require('fs').readFileSync(process.argv[1], 'utf8'), {filename: process.argv[1]})"
    checked = subprocess.run(["node", "-e", parse, str(path)], capture_output=True, text=True, timeout=30)
    assert checked.returncode == 0, checked.stderr[-600:]
    assert "var MeshoptDecoder" in path.read_text("utf-8")


def test_page_loads_the_entrypoint_as_a_module():
    page = (WEB / "index.html").read_text("utf-8")
    assert '<script type="module" src="/app.js"></script>' in page


def test_browser_loads_modules_and_renders_create_view(tmp_path):
    chrome = shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        installed = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
        chrome = str(installed) if installed.is_file() else None
    if not chrome:
        pytest.skip("Chrome is not installed")

    import copy
    import server
    from npcgen.config import DEFAULTS

    cfg = copy.deepcopy(DEFAULTS)
    cfg.update(library_path=tmp_path / "library", comfy_url="http://127.0.0.1:9")
    httpd = server.create_server(("127.0.0.1", 0), server.App(cfg))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/"
        browser = subprocess.run(
            [chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
             f"--user-data-dir={tmp_path / 'chrome-profile'}", "--virtual-time-budget=5000", "--dump-dom", url],
            capture_output=True, text=True, timeout=40,
        )
        assert browser.returncode == 0, browser.stderr[-2000:]
        assert 'id="sidebar"' in browser.stdout and 'id="sheet"' in browser.stdout
        assert "The NPC Forge page could not start" not in browser.stdout
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


LICENCE_VIEW_SCRIPT = r"""
import fs from 'node:fs';
import assert from 'node:assert/strict';

class Text { constructor(text) { this.nodeType = 3; this.text = String(text); } }
class El {
  constructor(tag) { this.tag = tag; this.nodeType = 1; this.children = []; this.attrs = {}; this.style = {}; this.dataset = {}; this.className = ''; this.listeners = {}; }
  append(...kids) { this.children.push(...kids); }
  setAttribute(name, value) { this.attrs[name] = value; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
}
globalThis.document = { createElement: tag => new El(tag), createTextNode: text => new Text(text) };
const textOf = node => (node.nodeType === 3 ? node.text : node.children.map(textOf).join(''));
const find = (node, test) => (node.nodeType === 1 ? (test(node) ? node : node.children.map(c => find(c, test)).find(Boolean)) : undefined);

const core = await import(process.env.CORE);
const view = await import(process.env.VIEW);
core.S.cfg = { licences: JSON.parse(fs.readFileSync(process.env.CATALOGUE, 'utf8')) };
core.S.library = [{ id: 'a', commercial: 'no' }, { id: 'b', commercial: 'none' }, { id: 'c', commercial: 'yes' }, { id: 'd', commercial: 'unclear' }];

// the words after a model's name in a drop-down
assert.equal(view.optionTag('image:qwen21'), ' · non-commercial');
assert.equal(view.optionTag('image:zimage'), ' · commercial OK');
assert.equal(view.optionTag('image:mage'), ' · not cleared');
assert.equal(view.optionTag('three_d:trellis2'), ' · commercial, with conditions');
assert.equal(view.optionTag('voice:breeze'), ' · non-commercial');
assert.equal(view.optionTag('image:no-such-engine'), '');
assert.equal(view.modelFor('edit:qwen2511').id, 'qwen2511');
assert.equal(view.modelFor('edit:nothing'), null);

// the note under a picker: the rating, the licence in a few words, and a way to the details
const note = view.licenceNote('image:qwen21');
assert.ok(find(note, el => el.className.includes('licence bad')), 'a red badge for a non-commercial model');
assert.match(textOf(note), /Not for commercial work/);
assert.match(textOf(note), /Details/);
assert.equal(view.licenceNote('image:no-such-engine'), null);
assert.ok(find(view.licenceNote('image:zimage'), el => el.className.includes('licence ok')));
assert.ok(find(view.licenceNote('three_d:trellis2'), el => el.className.includes('licence accent')));
assert.ok(find(view.licenceNote('image:mage'), el => el.className.includes('licence warn')));

// the badge on a character's header and the dot on a Library card
const badge = view.commercialBadge({ id: 'a' });
assert.equal(badge.tag, 'button');
assert.ok(badge.className.includes('bad') && /Not for commercial work/.test(textOf(badge)));
assert.equal(view.commercialBadge({ id: 'b' }), null);              // nothing made yet: nothing to rate
assert.equal(view.commercialBadge({ id: 'not-in-the-library' }), null);
assert.equal(view.commercialBadge({}), null);
assert.ok(view.commercialBadge({ id: 'c' }).className.includes(' ok'));
assert.equal(view.commercialDot({ commercial: 'none' }), null);
assert.ok(view.commercialDot({ commercial: 'yes' }).className.includes('licence-dot ok'));
assert.match(view.commercialDot({ commercial: 'no' }).attrs.title, /Non-commercial only/);

// the Library filter's names
assert.deepEqual(Object.values(view.ratingNames()), ['Commercial use allowed', 'Allowed, with conditions', 'Not cleared', 'Non-commercial only', 'Nothing made yet']);

// an older server sends no list: nothing is added anywhere
core.S.cfg = {};
assert.equal(view.optionTag('image:zimage'), '');
assert.equal(view.licenceNote('image:zimage'), null);
assert.equal(view.commercialDot({ commercial: 'yes' }), null);
assert.ok(view.commercialBadge({ id: 'c' }));                      // (the badge still reads the Library's own field)
"""


def test_the_licence_badges_notes_and_dots_follow_the_servers_list(tmp_path):
    import json
    from npcgen import licences

    catalogue = tmp_path / "catalogue.json"
    catalogue.write_text(json.dumps(licences.catalogue()), "utf-8")
    env = {**__import__("os").environ, "CORE": (WEB / "core.js").as_uri(), "VIEW": (WEB / "licences-view.js").as_uri(), "CATALOGUE": str(catalogue)}
    checked = subprocess.run(["node", "--input-type=module", "-e", LICENCE_VIEW_SCRIPT], capture_output=True, text=True, timeout=60, env=env)
    assert checked.returncode == 0, checked.stderr[-1500:]


def test_every_place_that_offers_a_model_shows_its_licence():
    """The owner asked for clear commercial-use and licence information on every model: each picker must use the shared helpers."""
    app = (WEB / "app.js").read_text("utf-8")
    assert "from './licences-view.js'" in app and "optionTag(`${kind}:${e.id}`)" in app and "licenceNote(`${kind}:${chosen.id}`)" in app
    assert "optionTag('voice:'" in app and "licenceNote('voice:'" in app
    assert "commercialBadge(npc)" in app and "openLicences()" in app and "Licences of every model" in app
    voices = (WEB / "voice-designer.js").read_text("utf-8")
    assert "from './licences-view.js'" in voices and voices.count("engineTag(") == 3 and "licenceNote('voice:' + e.id)" in voices
    assert voices.count("' · non-commercial'") == 1                     # (only inside engineTag, the fallback for an older server)
    library = (WEB / "library-view.js").read_text("utf-8")
    assert "commercialDot(npc)" in library and "key: 'commercial'" in library
    assert 'id="aboutLicences"' in (WEB / "index.html").read_text("utf-8")
    assert "export async function openLicences" in (WEB / "licences-view.js").read_text("utf-8")


def test_the_game_size_picker_warns_that_clean_hands_need_a_dense_mesh():
    """Measured 2026-10-13: a hand has 15 to 130 faces at 25,000, 120 to 520 at 100,000 and 320 to 1,200 at 200,000. Ticking Clean hands under a size below 100,000
    must say so, and the size hint must carry the measured file sizes instead of the earlier guess."""
    app = (WEB / "app.js").read_text("utf-8")
    assert "const handsWarning = chosen.hands && +chosen.faces < 100000" in app
    assert "Clean hands need 100,000 faces or more to have real fingers" in app
    assert "handsWarning,\n" in app.replace("\r\n", "\n")                               # (it is shown right under the picker)
    assert "if (o.key === 'hands') renderGallery();" in app                           # (ticking the box re-draws the card so the warning appears at once)
    assert "26 MB at 200,000 faces to 16 MB at 25,000" in app and "adds about 19,000 faces" in app
    assert "a hand has only a few hundred faces at 100,000" not in app               # (the earlier, unmeasured wording)


def _object_keys(source, start):
    """The keys at the top level of the {...} that opens at source[start]: what is nested deeper (and a spread) is not looked into."""
    depth, flat = 0, []
    for i in range(start, len(source)):
        ch = source[i]
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
            if depth == 0:
                break
        flat.append(ch if depth == 1 else " ")
    return re.findall(r"[{,]\s*(\w+)\s*:", "".join(flat))


def test_every_button_that_asks_for_something_to_make_is_counted_as_something_to_make():
    """The head's Redo and Make buttons answered "Tick at least one thing to make." for as long as they existed (2026-09-29 to 2026-10-15): they ask for
    nothing but `head_3d`, and the page's check of whether anything was asked for did not know it. Every key a generateDraft call passes must be in that check."""
    app = (WEB / "app.js").read_text("utf-8")
    check = re.search(r"const hasAssets = a => (.*);", app).group(1)
    known = set(re.findall(r"\ba\.(\w+)", check))
    asked = {}
    for call in re.finditer(r"generateDraft\(\{", app):
        for key in _object_keys(app, call.end() - 1):
            asked.setdefault(key, call.start())
    assert {"head_3d", "model_3d", "rig_3d", "import_3d", "best_3d"} <= set(asked), "the scan found too little to be trusted"
    missing = sorted(key for key in asked if key not in known)
    assert not missing, f"generateDraft is asked for {missing}, which hasAssets does not count: its button would say there is nothing to make"

