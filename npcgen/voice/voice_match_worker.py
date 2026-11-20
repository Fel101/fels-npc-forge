"""Proxy speaker similarity for one take against its voice's reference.

Runs in Voice Lab's own environment, so the score uses the same speaker encoder and the same calibrated bands
as Voice Lab (speaker_similarity.py). It prints one JSON object on stdout. The score is a proxy: it is not a
listening test and not an identity claim.
"""
import argparse
import json
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lab', required=True, help='Voice Lab root (provides speaker_similarity.py)')
    parser.add_argument('--reference', required=True)
    parser.add_argument('--take', required=True)
    args = parser.parse_args(argv)
    lab = Path(args.lab).resolve()
    sys.path.insert(0, str(lab))
    import speaker_similarity

    score = speaker_similarity.similarity(args.reference, args.take)
    print(json.dumps({'status': 'ok', 'score': round(float(score), 4), 'band': speaker_similarity.band(score)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
