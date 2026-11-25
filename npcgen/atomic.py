"""Reading and replacing files that another thread may have open, which Windows refuses for a moment.

NPC Forge saves a record (npc.json, item.json, a scene) or a picture whole: a temporary file beside it, then os.replace, so a
reader never sees half of it. On Windows that replace is refused ("Access is denied") while any other handle has the file open,
as the page has when it reads a character or loads its portrait at that moment, and a read is refused ("Permission denied")
while a replace is under way. Measured with two threads reading a record while a third saved it: hundreds of refused saves and
reads a second, in the character library and the items alike. Each lasts only as long as the other side's read or replace,
so here it is tried again, for up to two seconds, as npcgen/fels_suite.py does for the suite's address files. A
PermissionError that lasts longer (a file that really may not be written) is raised as before.
"""
import os
import pathlib
import time

PATIENCE = 2.0          # seconds a refused read or replace is tried again for
FIRST_WAIT = 0.005      # the first wait; each one after it is twice as long, up to MAX_WAIT
MAX_WAIT = 0.05


def _again(action, *args):
    deadline = time.monotonic() + PATIENCE
    wait = FIRST_WAIT
    while True:
        try:
            return action(*args)
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(wait)
            wait = min(wait * 2, MAX_WAIT)


def replace(source, target):
    """os.replace(source, target), tried again while another handle has the target open."""
    return _again(os.replace, source, target)


def read_text(path):
    """The file's text (UTF-8), tried again while it is being replaced."""
    return _again(pathlib.Path(path).read_text, "utf-8")


def read_bytes(path):
    """The file's bytes, tried again while it is being replaced."""
    return _again(pathlib.Path(path).read_bytes)
