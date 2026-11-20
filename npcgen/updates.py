"""Is there a newer release of NPC Forge on GitHub? Asked by About > Check for updates and by update.bat.

The answer comes from GitHub's API for the newest published release of REPOSITORY, over HTTPS. The request says which version
of NPC Forge is asking and nothing about this computer or its library, though GitHub sees the internet address it comes from.
NPC Forge asks only when someone presses the button or runs update.bat. A release is a tag such as v1.0.1, and its version is
compared with npcgen.__version__.

Standard library only, so update.bat can use it with nothing else installed.
"""
import http.client
import json
import os
import re
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import __version__ as VERSION

REPOSITORY = "Fel101/fels-npc-forge"
API = "https://api.github.com"
HOSTS = {"api.github.com", "github.com", "codeload.github.com"}      # every request goes to one of these over HTTPS, redirects too
TIMEOUT = 20
NOTES_LIMIT = 4000
USER_AGENT = f"npc-forge/{VERSION}"
NETWORK_ERRORS = (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError, ssl.SSLError)


class UpdateError(Exception):
    """A check or a download that could not be done; the message says why, for the person who asked."""


def parse_version(text):
    """A tag such as v1.0.1, or a version such as 1.0.1 or 1.1, as (1, 0, 1); None for anything but numbers between dots."""
    text = (text or "").strip()
    text = text[1:] if text[:1] in ("v", "V") else text
    if not re.fullmatch(r"\d+(\.\d+){0,3}", text):
        return None
    parts = tuple(int(part) for part in text.split("."))
    return parts + (0,) * (3 - len(parts))


def check_url(url):
    """The url when it is HTTPS to GitHub; UpdateError for anything else."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or (parts.hostname or "").lower() not in HOSTS:
        raise UpdateError(f"Refused to fetch {url}: NPC Forge takes updates only from GitHub, over HTTPS.")
    return url


class _GitHubOnly(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only when it leads to GitHub over HTTPS, as a download's redirect to codeload.github.com does."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url, *, timeout=TIMEOUT, opener=None):
    """Open a GitHub url for reading. HTTP errors are raised as urllib's HTTPError; the caller says what they mean."""
    request = urllib.request.Request(check_url(url), headers={
        "Accept": "application/vnd.github+json", "User-Agent": USER_AGENT, "X-GitHub-Api-Version": "2022-11-28"})
    return (opener or urllib.request.build_opener(_GitHubOnly())).open(request, timeout=timeout)


def network_problem(exc):
    reason = getattr(exc, "reason", None) or exc
    return f"GitHub could not be reached ({reason}). Check the internet connection and try again."


def fetch_json(url, *, opener=None):
    try:
        with open_url(url, opener=opener) as response:
            return json.loads(response.read(4_000_000).decode("utf-8"))
    except urllib.error.HTTPError:
        raise
    except NETWORK_ERRORS as exc:
        raise UpdateError(network_problem(exc)) from exc
    except (UnicodeDecodeError, ValueError) as exc:
        raise UpdateError("GitHub's answer could not be read.") from exc


def latest_release(*, repository=REPOSITORY, fetch=fetch_json):
    """The newest published release as {"tag", "version", "name", "url", "published_at", "notes"}, or None when the repository
    has published none (GitHub answers 404 then, and for a private repository too)."""
    try:
        found = fetch(f"{API}/repos/{repository}/releases/latest")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code in (403, 429):
            raise UpdateError("GitHub is limiting how often this computer can ask (60 times an hour). Try again in an hour.") from exc
        raise UpdateError(f"GitHub answered {exc.code} {exc.reason}. Try again later.") from exc
    if not isinstance(found, dict) or not found.get("tag_name"):
        raise UpdateError("GitHub's answer named no release.")
    tag = str(found["tag_name"])
    return {"tag": tag, "version": parse_version(tag), "name": str(found.get("name") or tag),
            "url": str(found.get("html_url") or f"https://github.com/{repository}/releases/tag/{tag}"),
            "published_at": found.get("published_at"), "notes": str(found.get("body") or "")[:NOTES_LIMIT]}


def check(*, current=VERSION, repository=REPOSITORY, fetch=fetch_json):
    """Whether GitHub has a newer release than this version: {"current", "latest", "newer", "message", "release"}."""
    release = latest_release(repository=repository, fetch=fetch)
    if release is None:
        return {"current": current, "latest": None, "newer": False, "release": None,
                "message": f"No release of NPC Forge has been published on GitHub yet. This is version {current}."}
    if release["version"] is None:
        raise UpdateError(f"The newest release's tag, {release['tag']}, is not a version number NPC Forge can compare.")
    latest = release["tag"][1:] if release["tag"][:1] in ("v", "V") else release["tag"]
    mine = parse_version(current)
    newer = mine is None or release["version"] > mine
    if newer:
        message = f"Version {latest} is available. This is version {current}."
    elif release["version"] == mine:
        message = f"This is the newest version, {current}."
    else:
        message = f"This copy, version {current}, is newer than the newest release, {latest}."
    return {"current": current, "latest": latest, "newer": newer, "message": message, "release": release}


def source_zip_url(tag, repository=REPOSITORY):
    """The release's source as GitHub packs it: the repository's files at that tag, in one folder."""
    return f"https://codeload.github.com/{repository}/zip/refs/tags/{urllib.parse.quote(tag)}"


class UpdateChecks:
    """The server's side of Check for updates: asks GitHub and keeps the last answer in local/update/last-check.json, so the
    About box can show it again after a restart (an answer about another version of NPC Forge is not used)."""

    def __init__(self, folder, *, run=check, clock=time.time):
        self.path = Path(folder) / "last-check.json"
        self._run, self._clock = run, clock
        self._lock = threading.Lock()

    def last(self):
        try:
            found = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return found if isinstance(found, dict) and found.get("current") == VERSION else None

    def check_now(self):
        """Ask GitHub now and keep the answer, or the reason there was none; UpdateError when it could not be asked."""
        with self._lock:
            now = self._clock()
            stamp = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")
            try:
                result = {**self._run(), "checked_at": stamp}
            except UpdateError as exc:
                self._save({"current": VERSION, "checked_at": stamp, "error": str(exc)})
                raise
            self._save(result)
            return result

    def _save(self, record):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self.path.parent, prefix="last-check-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as output:
                json.dump(record, output, indent=2)
            os.replace(temporary, self.path)
        except OSError:
            Path(temporary).unlink(missing_ok=True)
