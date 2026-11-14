"""Run on a customer-controlled host or scheduled CI runner."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from . import adapters
from .contract import MAX_BODY, PLATFORMS, decode, envelope


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def load(path):
    with Path(path).open("rb") as stream:
        data = stream.read(32_000_001)
    if len(data) > 32_000_000:
        raise ValueError("Local input too large.")
    return decode(data)


def provider_output(args):
    # Platform CLIs and their subprocesses only need their own credentials.
    env = {key: value for key, value in os.environ.items() if key not in ("DRIFTSHIELD_TOKEN", "DRIFTSHIELD_ENDPOINT")}
    deadline = time.monotonic() + 90
    output = bytearray()
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env) as process:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise subprocess.TimeoutExpired(args, 90)
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > 32_000_000:
                        raise ValueError("Provider response too large.")
            code = process.wait(timeout=max(0, deadline - time.monotonic()))
            if code:
                raise subprocess.CalledProcessError(code, args)
        finally:
            if process.poll() is None:
                process.kill()
    return bytes(output)


def cli(args):
    return decode(provider_output(args))


def collect(args):
    if args.platform == "terraform":
        if not args.file:
            raise ValueError("Pass the refresh-only plan JSON with --file.")
        return adapters.terraform(load(args.file))
    if args.platform == "snapshot":
        if not args.file:
            raise ValueError("Pass the allowlisted JSON snapshot with --file.")
        return adapters.snapshot(load(args.file))
    if args.platform == "docker":
        if not args.context or args.context.startswith("-") or not args.container or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name) for name in args.container
        ):
            raise ValueError("Specify a Docker context and explicitly monitored containers.")
        prefix = ["docker", "--context", args.context]
        inventory = provider_output(prefix + ["container", "ls", "-a", "--format", "{{.Names}}"])
        names = set(inventory.decode().splitlines()).intersection(args.container)
        return adapters.docker(cli(prefix + ["inspect", "--type", "container", "--", *sorted(names)])) if names else {}
    if args.platform == "kubernetes":
        if not args.context or not args.namespace or args.context.startswith("-") or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", args.namespace):
            raise ValueError("Specify a context and namespace.")
        groups = "deployments.apps,statefulsets.apps,daemonsets.apps,services,horizontalpodautoscalers.autoscaling"
        document = cli(["kubectl", "--context", args.context, "--namespace", args.namespace, "--request-timeout=30s",
                        "get", groups, "-o", "json"])
        if document.get("kind") != "List" or document.get("metadata", {}).get("continue"):
            raise ValueError("Kubernetes inventory is incomplete.")
        return adapters.kubernetes([document], args.namespace)
    if not args.region or not re.fullmatch(r"[a-z]{2}(?:-[a-z0-9]+)+-\d+", args.region):
        raise ValueError("Specify an AWS region.")
    prefix = ["aws", "--no-cli-pager", "--output", "json", "--region", args.region]
    identity = cli(prefix + ["sts", "get-caller-identity"])
    expected_scope = f"{identity['Account']}:{args.region}"
    if args.scope != expected_scope:
        raise ValueError("AWS account and region do not match the target scope.")
    return adapters.aws({kind: cli(prefix + list(call)) for kind, call in adapters.AWS_CALLS.items()})


def send(url, token, body):
    endpoint = urlsplit(url)
    if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.query or endpoint.fragment:
        raise ValueError("Use the HTTPS ingestion endpoint from the dashboard.")
    opener = build_opener(NoRedirect())
    for attempt in range(3):
        request = Request(url, data=body, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with opener.open(request, timeout=15) as response:
                return decode(response.read(8192))["status"]
        except HTTPError as error:
            if error.code < 500 or attempt == 2:
                raise ValueError(f"Ingestion returned HTTP {error.code}.") from None
        except (URLError, OSError):
            if attempt == 2:
                raise ValueError("Ingestion endpoint unavailable.") from None
        time.sleep(2 ** attempt)
    raise AssertionError("Delivery retry exhausted.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("platform", choices=sorted(PLATFORMS))
    parser.add_argument("--scope", required=True)
    parser.add_argument("--file")
    parser.add_argument("--context")
    parser.add_argument("--namespace")
    parser.add_argument("--region")
    parser.add_argument("--container", action="append")
    parser.add_argument("--endpoint", default=os.environ.get("DRIFTSHIELD_ENDPOINT"))
    parser.add_argument("--preview", action="store_true", help="Print the projected envelope locally.")
    parser.add_argument("--collection-failed", action="store_true", help="Report an upstream collection failure.")
    args = parser.parse_args()
    token = os.environ.get("DRIFTSHIELD_TOKEN", "")
    if not args.preview and (not args.endpoint or not token):
        parser.error("Set DRIFTSHIELD_ENDPOINT and DRIFTSHIELD_TOKEN.")
    now = datetime.now(timezone.utc)
    payload = {"schema": 1, "platform": args.platform, "scope": args.scope, "delivery": str(uuid4()),
               "captured_at": now.isoformat()}
    try:
        if args.collection_failed:
            payload["error"] = "collection_failed"
        else:
            payload["resources"] = collect(args)
    except subprocess.TimeoutExpired:
        payload["error"] = "collector_timeout"
    except (subprocess.CalledProcessError, OSError):
        payload["error"] = "collection_failed"
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        payload["error"] = "invalid_snapshot"
    try:
        if "resources" in payload:
            try:
                envelope(payload, datetime.now(timezone.utc))
                if len(json.dumps(payload, separators=(",", ":")).encode()) > MAX_BODY:
                    raise ValueError("Observation is too large.")
            except (ValueError, TypeError, OverflowError):
                del payload["resources"]
                payload["error"] = "invalid_snapshot"
        envelope(payload, datetime.now(timezone.utc))
        body = json.dumps(payload, separators=(",", ":")).encode()
        if args.preview:
            print(body.decode())
        else:
            status = send(args.endpoint, token, body)
            print(f"DriftShield: {status}")
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        print(f"DriftShield delivery failed: {error}", file=sys.stderr)
        return 1
    return int("error" in payload)


if __name__ == "__main__":
    sys.exit(main())
