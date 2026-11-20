"""Run explicitly in Unreal's Python console; never executes on opening a Forge ZIP."""
import csv
import hashlib
import json
import math
from pathlib import Path

import unreal


def import_clip(json_path, pack_root, destination='/Game/ForgeVoices', asset_name=None):
    """Import one checked CurveTable. Refuse replacing an existing Unreal asset."""
    path = Path(json_path)
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema') != 1:
        raise ValueError('Unsupported Forge playback schema')
    root = Path(pack_root).resolve()
    audio = (root / data['audio_file']).resolve()
    if not audio.is_relative_to(root) or hashlib.sha256(audio.read_bytes()).hexdigest() != data['audio_sha256']:
        raise ValueError('Delivered audio is missing or changed')
    csv_path = path.with_suffix('.curves.csv')
    with csv_path.open(encoding='utf-8', newline='') as file:
        rows = list(csv.reader(file))
    def differs(actual, expected):
        actual, expected = float(actual), float(expected)
        return not math.isfinite(actual) or not math.isfinite(expected) or abs(actual - expected) > 0.000001
    if not rows:
        raise ValueError('CurveTable is empty')
    if rows[0][0] != 'Name' or [r[0] for r in rows[1:]] != data['groups']:
        raise ValueError('CurveTable groups differ from checked metadata')
    expected_times = [f['time'] for f in data['frames']]
    if len(rows[0]) != len(expected_times) + 1 or any(differs(a, b) for a, b in zip(rows[0][1:], expected_times)):
        raise ValueError('CurveTable timing differs from checked metadata')
    for row in rows[1:]:
        if len(row) != len(rows[0]) or any(differs(a, f['weights'][row[0]]) for a, f in zip(row[1:], data['frames'])):
            raise ValueError('CurveTable weights differ from checked metadata')
    name = asset_name or path.stem.replace('-', '_')
    if unreal.EditorAssetLibrary.does_asset_exist(destination + '/' + name):
        raise ValueError('Asset already exists; choose a new name or use Unreal Reimport deliberately')
    factory = unreal.CSVImportFactory()
    settings = unreal.CSVImportSettings()
    settings.set_editor_property('import_type', unreal.CSVImportType.ECSV_CURVE_TABLE)
    settings.set_editor_property('import_curve_interp_mode', unreal.RichCurveInterpMode.RCIM_CONSTANT)
    factory.set_editor_property('automated_import_settings', settings)
    task = unreal.AssetImportTask()
    for key, value in {'filename': str(csv_path), 'destination_path': destination,
                       'destination_name': name, 'automated': True, 'replace_existing': False,
                       'save': True, 'factory': factory}.items():
        task.set_editor_property(key, value)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    objects = task.get_objects()
    if len(objects) != 1 or not isinstance(objects[0], unreal.CurveTable):
        raise RuntimeError('Unreal CurveTable import failed; inspect the editor log')
    return objects[0], data
