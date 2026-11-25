"""A record or picture that one thread saves while another reads it (npcgen/atomic.py).

On Windows a replace is refused while another handle has the file open, and a read is refused while a replace is under way.
Before npcgen/atomic.py, two threads reading a character while a third saved it gave hundreds of refused saves a second, and
tests/test_items.py failed now and then when the picture thread read an item as the test saved it.
"""
import os
import threading
import time

import pytest

from npcgen import atomic, items
from npcgen.itemstore import ItemStore
from npcgen.library import Library


def refusing(times, then):
    """A stand-in that raises PermissionError `times` times, then does `then`."""
    calls = []

    def action(*args):
        calls.append(args)
        if len(calls) <= times:
            raise PermissionError(13, "Access is denied")
        return then(*args)
    return action, calls


def test_a_replace_refused_for_a_moment_is_tried_again(tmp_path, monkeypatch):
    source, target = tmp_path / "new.tmp", tmp_path / "record.json"
    source.write_text("new", "utf-8")
    target.write_text("old", "utf-8")
    real = os.replace
    fake, calls = refusing(3, real)
    monkeypatch.setattr(os, "replace", fake)
    atomic.replace(source, target)
    assert len(calls) == 4 and target.read_text("utf-8") == "new" and not source.exists()


def test_a_read_refused_for_a_moment_is_tried_again(tmp_path, monkeypatch):
    path = tmp_path / "record.json"
    path.write_bytes(b'{"a": 1}')
    for name, expected in (("read_text", '{"a": 1}'), ("read_bytes", b'{"a": 1}')):
        real = getattr(type(path), name)
        fake, calls = refusing(2, real)
        monkeypatch.setattr(type(path), name, fake)
        assert getattr(atomic, name)(path) == expected and len(calls) == 3
        monkeypatch.undo()


def test_a_refusal_that_lasts_is_raised_and_other_errors_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(atomic, "PATIENCE", 0.1)
    fake, calls = refusing(10 ** 6, None)
    monkeypatch.setattr(os, "replace", fake)
    started = time.monotonic()
    with pytest.raises(PermissionError):
        atomic.replace(tmp_path / "a", tmp_path / "b")
    assert 0.1 <= time.monotonic() - started < 1.0 and len(calls) > 1
    monkeypatch.undo()
    with pytest.raises(FileNotFoundError):
        atomic.read_text(tmp_path / "missing.json")


def hammer(read, save, seconds=0.4):
    """Two threads reading while a third saves, for a moment: every error any of them met."""
    errors, stop = [], threading.Event()

    def loop(action):
        while not stop.is_set():
            try:
                action()
            except Exception as exc:                  # what the threads met is the answer
                errors.append(repr(exc))
    threads = [threading.Thread(target=loop, args=(read,)) for _ in range(2)] + [threading.Thread(target=loop, args=(save,))]
    for thread in threads:
        thread.start()
    time.sleep(seconds)
    stop.set()
    for thread in threads:
        thread.join()
    return errors


def test_a_character_is_saved_and_read_while_other_threads_read_it(tmp_path):
    lib = Library(tmp_path / "library")
    (lib.root / "ada-vale-abc123").mkdir(parents=True)
    npc = lib.save({"id": "ada-vale-abc123", "profile": {"name": "Ada Vale"}, "assets": {}})
    lib.write_asset(npc["id"], "portrait.png", b"first")

    def save():
        lib.update(npc["id"], lambda n: n.__setitem__("saves", n.get("saves", 0) + 1))
        lib.write_asset(npc["id"], "portrait.png", b"again")
    assert hammer(lambda: (lib.get(npc["id"]), lib.read_asset(npc["id"], "portrait.png")), save) == []
    assert lib.get(npc["id"])["saves"] >= 1


def test_an_item_is_saved_and_read_while_other_threads_read_it(tmp_path):
    lib = Library(tmp_path / "library")
    store = ItemStore(lib)
    item = store.create(items.clean_item({"family": "weapon", "kind": "sword"}))
    assert hammer(lambda: store.get(item["id"]), lambda: store.update(item["id"], lambda n: n.__setitem__("name", "Cutlass"))) == []
    assert store.get(item["id"])["name"] == "Cutlass"
