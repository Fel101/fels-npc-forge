"""Sound tags in a line: written the neutral way ([laugh], [chuckle], [sigh], [gasp], [cough]; Breeze's (laugh) is accepted
too) and rewritten for the engine that speaks it. An engine that has no such sound gets the line without the tag, so it never
reads "laugh" out loud.

  breeze      (laugh) (sigh) (gasp) (cough); a chuckle becomes (laugh)
  chatterbox  [laugh] [chuckle] [cough]; sigh and gasp are dropped
  qwen, kokoro  no tags: all dropped
"""
import re

from . import engines

_TAG = re.compile(r"[\[(]\s*(laugh|laughs|laughing|chuckle|chuckles|sigh|sighs|gasp|gasps|cough|coughs)\s*[\])]", re.I)
_CUE = re.compile(r'<\|speaker:\d+\|>|\[[^\[\]\r\n]{1,160}\]')
_PROTECTED = re.compile(_CUE.pattern+'|'+_TAG.pattern,re.I)
_CANON = {"laughs": "laugh", "laughing": "laugh", "chuckles": "chuckle", "sighs": "sigh", "gasps": "gasp", "coughs": "cough"}
_BREEZE = {"laugh": "(laugh)", "chuckle": "(laugh)", "sigh": "(sigh)", "gasp": "(gasp)", "cough": "(cough)"}


def tags_in(text):
    return [_CANON.get(m.group(1).lower(), m.group(1).lower()) for m in _TAG.finditer(text or "")]


def transform_words(text, transform, engine_id=None):
    """Transform ordinary text while preserving recognized sound-event tokens."""
    result, cursor = [], 0
    for match in (_PROTECTED if engine_id=='drama3' else _TAG).finditer(text):
        result.extend([transform(text[cursor:match.start()]), match.group()])
        cursor = match.end()
    result.append(transform(text[cursor:]))
    return "".join(result)


def plain(text, engine_id=None):
    """The words alone: what a subtitle shows and what a word check compares against."""
    value=_CUE.sub(' ',text or '') if engine_id=='drama3' else text or ''
    return re.sub(r"\s{2,}", " ", _TAG.sub(" ",value)).strip()


def for_engine(text, engine_id):
    supported = set((engines.spec(engine_id) or {}).get("caps", {}).get("tags") or [])

    def swap(m):
        tag = _CANON.get(m.group(1).lower(), m.group(1).lower())
        if tag not in supported:
            return " "
        if engine_id == 'drama3':
            return '[' + {'laugh':'laughs','chuckle':'laughs','sigh':'sighs','gasp':'gasps','cough':'coughs'}[tag] + ']'
        return _BREEZE[tag] if engine_id == "breeze" else f"[{tag}]"
    return re.sub(r"\s{2,}", " ", _TAG.sub(swap, text or "")).strip()
