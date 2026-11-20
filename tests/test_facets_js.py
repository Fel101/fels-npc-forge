"""web/facets.js, the filter logic the Library tab and other character lists share. It runs under node (skipped when node is missing)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

FACETS = Path(__file__).resolve().parent.parent / "web" / "facets.js"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

ROWS = [
    {"id": "a", "name": "Yui Sato", "age": 27, "gender": "female", "heritage": "Japanese", "hair_color": "Black", "eyes": "dark brown", "style": "photoreal", "hair": "black long hair"},
    {"id": "b", "name": "Emma Hughes", "age": 34, "gender": "female", "heritage": "Caucasian", "hair_color": "Blonde", "eyes": "blue", "style": "photoreal", "hair": "golden blonde wavy hair"},
    {"id": "c", "name": "Ji-woo Kim", "age": 18, "gender": "female", "heritage": "Korean", "hair_color": "Black", "eyes": "dark brown", "style": "cartoon", "hair": "black bob"},
    {"id": "d", "name": "Old Tom", "age": 71, "gender": "male", "heritage": "", "hair_color": "Gray / white", "eyes": "gray", "style": "pixel", "hair": "white hair"},
    {"id": "e", "name": "Anya Petrova", "age": None, "gender": "female", "heritage": "Eastern European", "hair_color": "Blonde", "eyes": "green", "style": "photoreal", "hair": "blonde braid"},
]

SCRIPT = """
global.window = {};
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
const F = window.Facets, rows = JSON.parse(process.argv[2]);
const styles = { photoreal: 'Photorealistic', cartoon: 'Cartoon', pixel: 'Pixel art' };
const facets = [{ key: 'style', any: 'All styles', values: n => [n.style], label: id => styles[id] }, ...F.TRAITS];
const ids = f => F.apply(rows, f, facets).map(n => n.id).join('');
const opts = (f, key) => F.options(rows, f, facets, facets.find(x => x.key === key)).map(o => `${o.label}:${o.count}`);
const out = {
  bands: [17, 18, 29, 30, 64, 65, '30', null, '', 'x'].map(a => F.ageBand(a)),
  none: ids({ q: '' }),
  heritage: ids({ heritage: 'Japanese' }),
  hair: ids({ hair_color: 'Blonde' }),
  age: ids({ age: '25-29' }),
  age_young: ids({ age: '18-24' }),
  style: ids({ style: 'photoreal' }),
  both: ids({ style: 'photoreal', hair_color: 'Blonde', age: '30-39' }),
  words: ids({ q: 'korean black' }),
  label_words: ids({ q: 'photorealistic blonde' }),
  band_words: ids({ q: '30-39' }),
  nothing: ids({ q: 'zzz' }),
  male: ids({ gender: 'Male' }),
  hair_options_all: opts({}, 'hair_color'),
  hair_options_photoreal: opts({ style: 'photoreal' }, 'hair_color'),
  age_options: opts({}, 'age'),
  chosen_with_no_match: opts({ heritage: 'Japanese', hair_color: 'Blonde' }, 'hair_color'),
  active: [F.active({ q: '' }, facets), F.active({ q: ' ' }, facets), F.active({ q: 'x' }, facets), F.active({ eyes: 'blue' }, facets)],
};
const f = { q: 'abc', style: 'pixel', eyes: 'gray' };
F.clear(f, facets);
out.cleared = [f.q, f.style, f.eyes, F.active(f, facets)];
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out():
    done = subprocess.run(["node", "-e", SCRIPT, str(FACETS), json.dumps(ROWS)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_age_bands(out):
    assert out["bands"] == ["Under 18", "18-24", "25-29", "30-39", "50-64", "65 and over", "30-39", "", "", ""]


def test_each_choice_narrows_the_list(out):
    assert out["none"] == "abcde"
    assert out["heritage"] == "a" and out["hair"] == "be" and out["age"] == "a" and out["age_young"] == "c" and out["style"] == "abe" and out["male"] == "d"
    assert out["both"] == "b"                                                           # every choice must hold at once
    assert out["nothing"] == ""


def test_search_words_all_have_to_match_and_cover_what_the_facets_show(out):
    assert out["words"] == "c"                                                          # heritage and hair colour, as words
    assert out["label_words"] == "be"                                                   # "Photorealistic" is the label of the style, not its id
    assert out["band_words"] == "b"


def test_only_values_that_exist_are_offered_with_counts_that_respect_the_other_choices(out):
    assert out["hair_options_all"] == ["Black:2", "Blonde:2", "Gray / white:1"]
    assert out["hair_options_photoreal"] == ["Black:1", "Blonde:2"]
    assert out["age_options"] == ["18-24:1", "25-29:1", "30-39:1", "65 and over:1"]      # a band nobody is in is not offered
    assert out["chosen_with_no_match"] == ["Black:1", "Blonde:0"]                        # what is chosen stays listed, so it can be undone


def test_clearing_resets_the_search_and_every_choice(out):
    assert out["active"] == [False, False, True, True]
    assert out["cleared"] == ["", "", "", False]
