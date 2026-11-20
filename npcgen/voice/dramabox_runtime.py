"""The verified local DramaBox installation. Discovery never installs/downloads."""
import json
import os
from pathlib import Path

REPO = 'ResembleAI/Dramabox'
REVISION = '404f967f653fa1170dc15a9d1ddd3fdb9a0a842d'
GEMMA_REPO = 'unsloth/gemma-3-12b-it-bnb-4bit'
GEMMA_REVISION = '826e729dbaeea4ecb143738eed2bcf3539ebf7bf'
SOURCE_REVISION = 'a70a5818e103c1c9fef22409c1e0c707ebf4f8a7'
ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / 'local/dramabox-eval-20261013'
LICENCE_CONDITIONS = ('LTX-2 Community License + Gemma terms. Commercial use has conditions; '
    'entities with annual revenue of at least $10 million need a paid LTX license. '
    'Keep license notices and use restrictions. RE-USE denoising is disabled.')


def defaults(lab=None):
    overlays = [str(lab / name / 'Lib/site-packages') for name in ('.venv-fqwen', '.venv')] if lab else []
    return {'python': str(INSTALL / 'env/Scripts/python.exe'), 'env': {
        'DRAMABOX_SOURCE': str(INSTALL / 'upstream'),
        'DRAMABOX_MODEL_DIR': str(INSTALL / 'models/Dramabox' / REVISION),
        'DRAMABOX_GEMMA_DIR': str(INSTALL / 'models/gemma-3-12b-it-bnb-4bit' / GEMMA_REVISION),
        'DRAMABOX_SITE_OVERLAYS': os.pathsep.join(overlays),
        'HIP_VISIBLE_DEVICES': '1', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
        'HF_HUB_DISABLE_TELEMETRY': '1', 'PYTHONDONTWRITEBYTECODE': '1'}}


def assets(env):
    source = Path(env.get('DRAMABOX_SOURCE') or INSTALL / 'upstream').resolve()
    model = Path(env.get('DRAMABOX_MODEL_DIR') or INSTALL / 'models/Dramabox' / REVISION).resolve()
    gemma = Path(env.get('DRAMABOX_GEMMA_DIR') or INSTALL / 'models/gemma-3-12b-it-bnb-4bit' / GEMMA_REVISION).resolve()
    required = {source: ['src/inference_server.py', 'ltx2/ltx_pipelines/utils/blocks.py'],
        model: ['config.json', 'dramabox-dit-v1.safetensors', 'dramabox-audio-components.safetensors'],
        gemma: ['config.json', 'model.safetensors.index.json', 'tokenizer.model', 'preprocessor_config.json']}
    for folder, names in required.items():
        missing = [name for name in names if not (folder / name).is_file()]
        if missing: raise ValueError('DramaBox cached installation is missing ' + ', '.join(missing))
    config = json.loads((gemma / 'config.json').read_text('utf-8'))
    quant = config.get('quantization_config') or {}
    if config.get('model_type') != 'gemma3' or not quant.get('load_in_4bit') or quant.get('bnb_4bit_quant_type') != 'nf4':
        raise ValueError('DramaBox requires the verified Gemma 3 NF4 text encoder')
    index = json.loads((gemma / 'model.safetensors.index.json').read_text('utf-8'))
    shards = set(index.get('weight_map', {}).values())
    if not shards or any(Path(name).name != name or not (gemma / name).is_file() for name in shards):
        raise ValueError('DramaBox Gemma text-encoder shards are missing or invalid')
    return source, model, gemma


def source_revision(source):
    try:
        head = (source / '.git/HEAD').read_text().strip()
        if head.startswith('ref: '): head = (source / '.git' / head[5:]).read_text().strip()
        return head if len(head) == 40 and all(c in '0123456789abcdef' for c in head) else 'local-files'
    except OSError: return 'local-files'
