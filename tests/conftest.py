"""Shared test set-up."""
import pytest


@pytest.fixture(autouse=True)
def _only_the_example_briefs(monkeypatch, tmp_path_factory):
    """The tests read the example games' briefs in data/briefs/ only, never the briefs of the owner's own games in local/briefs/."""
    from npcgen import creatures
    monkeypatch.setattr(creatures, "LOCAL_BRIEFS_DIR", tmp_path_factory.mktemp("no-local-briefs"))
