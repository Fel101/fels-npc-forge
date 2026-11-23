"""URLs and identity probes for the Forge server."""
import base64
import http.client
import json
import urllib.error
import urllib.request


def local_url(host, port):
    """Use a reachable address for a local client of a wildcard listener."""
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}"


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def ask_identity(url, token=None):
    """Ask Forge's lightweight health variant. A 1.0 server returns its full health report."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects())
    try:
        request = urllib.request.Request(url.rstrip("/") + "/api/health?identity=1")
        try:
            response = opener.open(request, timeout=8)
        except urllib.error.HTTPError as exc:
            with exc:
                if not (exc.code == 401 and token and
                        exc.headers.get("WWW-Authenticate", "").startswith('Basic realm="NPC Forge"')):
                    return None
            encoded = base64.b64encode(f"npcforge:{token}".encode()).decode("ascii")
            request.add_header("Authorization", f"Basic {encoded}")
            response = opener.open(request, timeout=8)
        with response:
            answer = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, http.client.HTTPException):
        return None
    return answer if isinstance(answer, dict) else None
