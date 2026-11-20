"""Append only DramaBox records to an optional existing license catalogue.

Preserves the exact bytes of all existing records; refuses conflicting IDs.
The native picker also displays conditions when this optional catalogue is absent.
"""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def register(path, entries):
    path = Path(path)
    before = path.read_bytes()
    text = before.decode('utf-8')
    existing = json.loads(text)
    by_id = {row['id']: row for row in existing['models']}
    additions = []
    for row in entries:
        if row['id'] in by_id:
            if by_id[row['id']] != row: raise ValueError('Existing license record differs: ' + row['id'])
        else: additions.append(row)
    if not additions: return False
    match = re.search(r'"models"\s*:\s*\[', text)
    if not match: raise ValueError('Catalogue has no models array')
    start = match.end() - 1
    _, size = json.JSONDecoder().raw_decode(text[start:])
    end = start + size - 1
    content = ',\n'.join('    ' + json.dumps(row, ensure_ascii=False, indent=2).replace('\n', '\n    ') for row in additions)
    updated = text[:end].rstrip() + (',' if existing['models'] else '') + '\n' + content + '\n  ' + text[end:]
    parsed = json.loads(updated)
    assert parsed['models'] == existing['models'] + additions
    if path.read_bytes() != before: raise RuntimeError('Catalogue changed while preparing entries')
    path.write_bytes(updated.encode('utf-8'))
    return True


if __name__ == '__main__':
    path = ROOT / 'data/licences.json'
    if not path.exists(): print('No optional catalogue; native DramaBox conditions remain visible.')
    else:
        entries = json.loads((ROOT / 'data/dramabox-licences.json').read_text('utf-8'))
        print('Added DramaBox records only.' if register(path, entries) else 'DramaBox records already present.')
