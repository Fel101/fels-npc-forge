"""
Hard content rules for NPC Forge's adult studio, enforced in code: the same rules as the Altera companion app's core/safety.py
(a test compares them when NPCGEN_ALTERA names Altera's folder). The text below is Altera's.

These rules hold at every adult-mode level and do not depend on the chat model. An uncensored ("heretic") model refuses
nothing on its own, so what must always be true is checked here instead of being left to the prompt:

  * avatars are adults (MIN_AVATAR_AGE, the legal floor) and nothing describes a minor or a childlike character;
  * nothing depicts a real, identifiable person;
  * nothing is non-consensual.

The word lists are heuristics. They catch the obvious phrasings, not every phrasing. The second layer is the image age
check (core/age_check.py), which looks at the finished picture. The editable tier tables in content/adult_tiers.yaml
cannot loosen anything in this file.
"""
import re
from typing import Optional

MIN_AVATAR_AGE = 18        # the legal floor. Not configurable: there is no setting that lowers it
MIN_APPARENT_AGE = 18      # what the image age check must estimate before a picture is delivered. It is an ESTIMATE by a vision
                           # model and can be several years off either way: a second layer behind the text rules, not a guarantee

# Always added to an image's negative prompt.
YOUTH_NEGATIVE = "child, childlike, minor, underage, teenager, teen, youthful, baby face, schoolgirl, school uniform"

_NUM_WORDS = "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen"
_MINOR_DIGITS = r"(?:1[0-7]|[1-9])"
_MINOR_NUM = rf"(?:{_NUM_WORDS}|1[0-7]|[1-9])"
_UNITS = (r"min(?:ute)?s?|hours?|hrs?|days?|weeks?|months?|miles?|km|kg|lbs?|pounds?|feet|foot|ft|inch(?:es)?|cm|mm|"
          r"percent|%|dollars?|bucks?|euros?|times?|items?|people|more")

# Clear signals of a minor. Used on chat messages, where "kid" or "child" often just means the user's own family.
_YOUTH_STRONG = [
    r"\b(?:teen|teens|teenage|teenaged|teenager|teenagers|preteen|pre-teen|tween|tweens)\b",
    r"\b(?:underage|under-age)\b",
    r"\b(?:schoolgirl|schoolboy|school\s?girl|school\s?boy|high\s?school|middle\s?school|junior\s?high|grade\s?school|"
    r"elementary|school\s?uniform)s?\b",
    r"\b(?:loli|lolita|shota|jailbait|barely\s+legal)\b",
    r"\b(?:little|young)\s+(?:girl|boy)s?\b",
    r"\bunder\s*(?:the\s+age\s+of\s+)?(?:18|eighteen)\b",
    r"\b(?:not\s+yet|almost|nearly)\s+(?:18|eighteen)\b",
    rf"\b{_MINOR_NUM}\s*-?\s*(?:years?|yrs?)\s*-?\s*old\b",
    rf"\b{_MINOR_NUM}\s*-?\s*(?:yo|y/o)\b",
    rf"\b(?:aged?|age)\s*(?:of\s+)?{_MINOR_DIGITS}\b(?!\s*(?:{_UNITS}))",
    rf"\b(?:she|he|they|i|you)(?:'s|'m|'re|\s+(?:is|was|am|are|were))\s+{_MINOR_DIGITS}\b(?!\s*(?:{_UNITS}|and\b)|\s*[-/.:]\d)",
]
# Everything above plus the family words. Used on picture descriptions and avatar text, where they have no innocent use.
_YOUTH_ALL = _YOUTH_STRONG + [
    r"\b(?:child|children|childlike|childish|kid|kids|toddler|infant|juvenile|adolescent|minors)\b",
]
_YOUTH_STRONG_RE = re.compile("|".join(_YOUTH_STRONG), re.I)
_YOUTH_ALL_RE = re.compile("|".join(_YOUTH_ALL), re.I)

_SEXUAL_RE = re.compile(
    r"\b(?:sex|sexy|sexual\w*|nude|naked|topless|bottomless|erotic\w*|horny|porn\w*|nsfw|fuck\w*|cock|dick|pussy|tits?|"
    r"boobs?|breasts?|nipples?|cum|cumming|orgasm\w*|blowjob|masturbat\w*|lingerie|undress\w*|strip(?:ping|tease)?|"
    r"fetish|kinky|naughty|seduc\w*|aroused|arousal)\b", re.I)

# Real, identifiable people: celebrity and likeness phrasing, and "looks like <Name>".
_REAL_PERSON_RE = re.compile(
    r"\b(?:celebrit\w*|public\s+figure|deep-?fakes?|face[- ]?swap\w*|look-?alikes?|likeness|"
    r"famous\s+(?:actress|actor|singer|model|person|people))\b", re.I)
_REAL_PERSON_NAMED_RE = re.compile(          # the verb is case-insensitive, the name after it must be capitalised
    r"(?i:\b(?:looks?\s+like|looking\s+like|resembl(?:es|ing)|impersonat(?:es|ing)|version\s+of)\s+(?:the\s+)?)[A-Z][a-z]+")

_NON_CONSENT_RE = re.compile(
    r"\b(?:rap(?:e|ed|es|ing|ist)|non-?consensual|without\s+(?:her|his|their|my|your)\s+consent|"
    r"against\s+(?:her|his|their|my|your)\s+will|forced\s+(?:to|into)|unconscious|drugged|molest\w*|coerc\w*|"
    r"blackmail\w*|held\s+down|sexually\s+assault\w*|passed\s+out)\b", re.I)


def has_minor_signal(text: str, strong_only: bool = False) -> bool:
    """True if the text describes or hints at a minor or a childlike character."""
    pattern = _YOUTH_STRONG_RE if strong_only else _YOUTH_ALL_RE
    return bool(pattern.search(text or ""))


def blocks_message(text: str) -> Optional[str]:
    """Chat tripwire: a sexual message that also points at a minor never reaches the model. Returns the reason or None."""
    if _YOUTH_STRONG_RE.search(text or "") and _SEXUAL_RE.search(text):
        return "minor_sexual"
    return None


def hard_problem(text: str) -> Optional[str]:
    """Why a picture description or avatar text can never be used, or None. Applies at every adult-mode level."""
    text = text or ""
    if _YOUTH_ALL_RE.search(text):
        return "It mentions a minor or a childlike detail, which is never allowed."
    if _REAL_PERSON_RE.search(text) or _REAL_PERSON_NAMED_RE.search(text):
        return "It refers to a real person's likeness, which is never allowed."
    if _NON_CONSENT_RE.search(text):
        return "It describes something non-consensual, which is never allowed."
    return None


def age_problem(age) -> Optional[str]:
    """Why an avatar's age is not acceptable, or None."""
    try:
        age = int(age)
    except (TypeError, ValueError):
        return f"Avatars need a stated age of {MIN_AVATAR_AGE} or older."
    if age < MIN_AVATAR_AGE:
        return f"Avatars must be {MIN_AVATAR_AGE} or older (this one is {age})."
    return None


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def card_problem(card: dict) -> Optional[str]:
    """Why an avatar card is not acceptable (age, or any text describing a minor or a real person), or None."""
    problem = age_problem(card.get("age"))
    if problem:
        return problem
    for text in _strings({k: v for k, v in card.items() if k != "age"}):
        found = hard_problem(text)
        if found:
            return f"The avatar's description is not allowed: {found[0].lower()}{found[1:]}"
    return None
