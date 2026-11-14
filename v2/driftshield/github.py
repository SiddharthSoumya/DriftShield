import re
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import json

REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}\Z")
FIELDS = {
    "visibility": ("Repository visibility", "high"),
    "default_branch": ("Default branch", "high"),
    "archived": ("Archived", "medium"),
    "has_issues": ("Issues enabled", "low"),
    "has_wiki": ("Wiki enabled", "low"),
}


class ScanError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def collect(repository):
    owner, name = repository.split("/")
    request = Request(
        f"https://api.github.com/repos/{owner}/{name}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "DriftShield/2",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=8) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            raise ScanError("GitHub returned a response above the size limit.")
        metadata = json.loads(raw)
    except HTTPError as error:
        if error.code in (301, 302, 307, 308, 404):
            raise ScanError("Repository unavailable. It may have been renamed, moved, deleted, or made private.") from error
        if error.code in (403, 429):
            raise ScanError("GitHub denied the request or its rate limit was reached.") from error
        raise ScanError(f"GitHub returned HTTP {error.code}.") from error
    except (URLError, OSError, ValueError) as error:
        raise ScanError("GitHub could not be reached or returned an invalid response.") from error

    if not isinstance(metadata, dict) or metadata.get("visibility") != "public":
        raise ScanError("GitHub did not return a public repository.")
    return metadata
