"""Every link in the Markdown files points at a file that exists, and at a heading that exists there.

Links to other sites are not fetched (the tests need no network); a link into this repository, with or without a
#heading, is checked against the files and their headings as GitHub names them.
"""
import pathlib
import re
import unicodedata

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "local", "library", "node_modules", "vendor", "__pycache__", ".pytest_cache", "out"}

LINK = re.compile(r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
REFERENCE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s+\"[^\"]*\")?\s*$", re.MULTILINE)
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
CODE = re.compile(r"(`+)(?!`).*?(?<!`)\1(?!`)", re.DOTALL)
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
HTML_ID = re.compile(r"<a\s+(?:name|id)=\"([^\"]+)\"", re.IGNORECASE)


def markdown_files():
    found = []
    for path in sorted(ROOT.rglob("*.md")):
        if SKIP_DIRS.intersection(path.relative_to(ROOT).parts[:-1]):
            continue
        found.append(path)
    return found


def unfenced(text):
    """The text with the lines of fenced code blocks blanked."""
    out, fence = [], None
    for line in text.split("\n"):
        m = FENCE.match(line)
        if fence is None:
            if m:
                fence = m.group(1)
            out.append("" if m else line)
            continue
        if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line.strip()[len(m.group(1)):]:
            fence = None
        out.append("")
    return "\n".join(out)


def prose(text):
    """The text with fenced code blocks and code spans blanked, so a link inside code is not read as one."""
    return CODE.sub(lambda m: " " * len(m.group(0)), unfenced(text))


def slug(heading):
    """The id GitHub gives a heading: links and markup dropped, lower case, punctuation removed, spaces to hyphens."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = re.sub(r"<[^>]+>", "", text).lower()
    kept = "".join(c for c in text if c in " -_" or unicodedata.category(c)[0] in "LNM")
    return kept.replace(" ", "-")


def anchors(path, cache={}):
    if path not in cache:
        text = path.read_text("utf-8")
        seen, ids = {}, set()
        for heading in HEADING.findall(unfenced(text)):
            base = slug(heading)
            count = seen.get(base, 0)
            seen[base] = count + 1
            ids.add(base if count == 0 else f"{base}-{count}")
        cache[path] = ids | set(HTML_ID.findall(text))
    return cache[path]


def links(path):
    text = prose(path.read_text("utf-8"))
    return LINK.findall(text) + REFERENCE.findall(text)


def problems(path):
    found = []
    for target in links(path):
        if re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("//"):
            continue                                            # another site, or mailto: and the like
        where, _, anchor = target.partition("#")
        file = (path.parent / where).resolve() if where else path
        if not file.exists():
            found.append(f"{target}: no such file")
            continue
        if anchor and file.suffix.lower() == ".md" and anchor.lower() not in anchors(file):
            found.append(f"{target}: no heading #{anchor} in {file.relative_to(ROOT).as_posix()}")
    return found


def test_the_markdown_files_are_found():
    names = {p.relative_to(ROOT).as_posix() for p in markdown_files()}
    assert {"README.md", "docs/README.md", "CONTRIBUTING.md", "NOTICE.md", "docs/responsible-use.md"} <= names


@pytest.mark.parametrize("path", markdown_files(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_every_link_points_at_something_there(path):
    assert problems(path) == []


def test_the_docs_index_lists_every_page():
    index = {target.partition("#")[0] for target in links(ROOT / "docs" / "README.md")}
    pages = {p.name for p in (ROOT / "docs").glob("*.md")} - {"README.md"}
    assert sorted(pages - index) == []


def test_the_checker_catches_a_broken_link_and_a_wrong_heading(tmp_path):
    (tmp_path / "other.md").write_text("# Other page\n\n## What it does, in short\n\n## Two\n\n## Two\n", "utf-8")
    page = tmp_path / "page.md"
    page.write_text("[ok](other.md#what-it-does-in-short) [ok](other.md#two-1) [web](https://example.com/x.md)\n"
                    "`[code](missing.md)`\n\n```\n[fenced](missing.md)\n```\n\n"
                    "[gone](missing.md) [bad](other.md#nowhere) [self](#nothing)\n", "utf-8")
    global ROOT
    saved, ROOT = ROOT, tmp_path
    try:
        assert problems(page) == ["missing.md: no such file", "other.md#nowhere: no heading #nowhere in other.md",
                                  "#nothing: no heading #nothing in page.md"]
    finally:
        ROOT = saved
