from pathlib import Path

from npcgen.fels_suite import check_vectors


def test_shared_suite_vectors():
    failures = check_vectors(Path(__file__).with_name("suite_vectors.json"))
    assert not failures, "\n".join(failures)
