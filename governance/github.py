import base64
import json
import re
import time
from pathlib import PurePosixPath
from urllib.parse import quote, urlencode, urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings

API = "https://api.github.com"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}\Z")
FIELDS = {
    "visibility": "Visibility",
    "default_branch": "Default branch",
    "archived": "Archived",
    "rules": "Active ruleset rules",
}


class CollectionError(Exception):
    def __init__(self, message, retry_seconds=0):
        super().__init__(message)
        self.retry_seconds = retry_seconds


def repo_name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value) or PurePosixPath(value).name == ".." or value.endswith("/."):
        raise CollectionError("Use a GitHub repository name in owner/repository format.")
    return value.lower()


def cipher():
    try:
        return Fernet(settings.GITHUB_KEY.encode())
    except (TypeError, ValueError) as error:
        raise CollectionError("GitHub App encryption is unavailable. Contact the service administrator.") from error


def signing_key(pem):
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError) as error:
        raise CollectionError("Provide a valid unencrypted GitHub App private key.") from error
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise CollectionError("A GitHub App RSA key of at least 2048 bits is required.")
    return key


def app_jwt(app_id, key):
    def b64(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=")

    now = int(time.time())
    claims = json.dumps({"iat": now - 60, "exp": now + 480, "iss": app_id}).encode()
    message = b".".join([b64(b'{"alg":"RS256","typ":"JWT"}'), b64(claims)])
    return b".".join([message, b64(key.sign(message, padding.PKCS1v15(), hashes.SHA256()))]).decode()


class GitHub:
    def __init__(self, client):
        self.client = client

    def request(self, path, token="", method="GET", body=None, deadline=None):
        if not path.startswith("/") or path.startswith("//"):
            raise CollectionError("GitHub returned an invalid pagination link.")
        if deadline is not None and time.monotonic() >= deadline:
            raise CollectionError("GitHub collection exceeded its time budget.")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            with self.client.stream(method, API + path, headers=headers, json=body) as response:
                code = response.status_code
                if code in (403, 429) and (
                    code == 429 or response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers
                ):
                    retry = response.headers.get("retry-after", "")
                    reset = response.headers.get("x-ratelimit-reset", "")
                    delay = int(retry) if retry.isdecimal() else int(reset) - int(time.time()) if reset.isdecimal() else 60
                    raise CollectionError("GitHub rate limited collection.", min(86400, max(60, delay)))
                if code == 404 or 300 <= code < 400:
                    raise CollectionError("GitHub resource unavailable. Check access, repository name and installation.")
                if code in (401, 403):
                    raise CollectionError("GitHub denied access. Check the app permissions and credentials.")
                if code >= 400:
                    raise CollectionError(f"GitHub returned HTTP {code}.")
                data = bytearray()
                for chunk in response.iter_bytes():
                    if deadline is not None and time.monotonic() >= deadline:
                        raise CollectionError("GitHub collection exceeded its time budget.")
                    data.extend(chunk)
                    if len(data) > 2_000_000:
                        raise CollectionError("GitHub response exceeded the collection size limit.")
                link = response.links.get("next", {}).get("url", "")
                if link:
                    target = urlsplit(link)
                    if target.scheme != "https" or target.netloc != "api.github.com":
                        raise CollectionError("GitHub returned an invalid pagination link.")
                    link = target.path + ("?" + target.query if target.query else "")
                return json.loads(data), link
        except (httpx.HTTPError, ValueError, RecursionError) as error:
            raise CollectionError("GitHub could not be reached or returned invalid JSON.") from error

    def pages(self, path, token, key=None, deadline=None):
        items = []
        for _ in range(10):
            value, path = self.request(path, token, deadline=deadline)
            if key:
                value = value.get(key) if isinstance(value, dict) else None
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise CollectionError("GitHub returned an unexpected collection format.")
            items.extend(value)
            if not path:
                return items
        raise CollectionError("GitHub collection exceeded 1,000 records. Select fewer repositories.")

    def installation(self, app_id, key, installation_id):
        jwt = app_jwt(app_id, key)
        install, _ = self.request(f"/app/installations/{installation_id}", jwt)
        if not isinstance(install, dict) or install.get("id") != installation_id or install.get("suspended_at"):
            raise CollectionError("Installation is unavailable or suspended.")
        account = install.get("account")
        if not isinstance(account, dict) or not isinstance(account.get("login"), str) or len(account["login"]) > 100:
            raise CollectionError("GitHub did not return a valid installation account.")
        return account["login"], self.token(app_id, key, installation_id)

    def token(self, app_id, key, installation_id, github_id=None):
        scope = {"permissions": {"metadata": "read"}}
        if github_id:
            scope["repository_ids"] = [github_id]
        value, _ = self.request(f"/app/installations/{installation_id}/access_tokens", app_jwt(app_id, key), "POST", scope)
        if not isinstance(value, dict) or not isinstance(value.get("token"), str) or not value["token"]:
            raise CollectionError("GitHub did not issue an installation token.")
        return value["token"]

    def inventory(self, token):
        return self.pages("/installation/repositories?per_page=100", token, "repositories", time.monotonic() + 15)

    def snapshot(self, monitor):
        token = ""
        deadline = time.monotonic() + 60
        if monitor.app_managed:
            integration = monitor.workspace.integration
            try:
                pem = cipher().decrypt(bytes(integration.encrypted_key))
            except InvalidToken as error:
                raise CollectionError("Installation key could not be decrypted.") from error
            token = self.token(integration.app_id, signing_key(pem), integration.installation_id, monitor.github_id)
        path = f"/repositories/{monitor.github_id}" if monitor.github_id else f"/repos/{monitor.full_name}"
        metadata, _ = self.request(path, token, deadline=deadline)
        if not isinstance(metadata, dict):
            raise CollectionError("GitHub returned unexpected repository metadata.")
        full_name = repo_name(metadata.get("full_name"))
        identifier = metadata.get("id")
        if type(identifier) is not int or not 0 < identifier < 2**63 or (monitor.github_id and identifier != monitor.github_id):
            raise CollectionError("GitHub returned a different repository identity.")
        values = {field: metadata.get(field) for field in FIELDS if field != "rules"}
        branch = values["default_branch"]
        if (not isinstance(branch, str) or not branch.isprintable() or not 0 < len(branch) <= 255
                or values["visibility"] not in ("public", "internal", "private") or type(values["archived"]) is not bool):
            raise CollectionError("GitHub omitted a required repository setting.")
        rules = self.pages(f"/repos/{full_name}/rules/branches/{quote(branch, safe='')}?{urlencode({'per_page': 100})}", token, deadline=deadline)
        values["rules"] = normalize_rules(rules)
        return identifier, full_name, values


def normalize_rules(rules):
    # API order and provider bookkeeping are irrelevant to effective rule configuration.
    def canonical(value):
        if isinstance(value, dict):
            return {key: canonical(item) for key, item in value.items()}
        if isinstance(value, list):
            return sorted((canonical(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
        return value

    distinct = set()
    for rule in rules:
        parameters = rule.get("parameters", {})
        if not isinstance(rule.get("type"), str) or not isinstance(parameters, dict):
            raise CollectionError("GitHub returned a rule with an invalid type or parameters.")
        count = parameters.get("required_approving_review_count", 0)
        if rule["type"] == "pull_request" and (type(count) is not int or not 0 <= count <= 100):
            raise CollectionError("GitHub returned an invalid approval count.")
        distinct.add(json.dumps(canonical({"type": rule["type"], "parameters": parameters}), sort_keys=True))
    return [json.loads(value) for value in sorted(distinct)]
