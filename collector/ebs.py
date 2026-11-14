"""Collection of an enrolled regional EBS inventory."""
from datetime import datetime, timezone
import json
import re
import subprocess
from uuid import uuid4

from .adapters import ebs
from .contract import (
    EBS_PARTIAL, EBS_ROLE, EBS_SCOPE, MAX_BODY, MAX_RESOURCES, pilot_envelope, pilot_uuid, validate_ebs,
)


def validate_config(scope, region, role, enrollment, epoch):
    scope_match = EBS_SCOPE.fullmatch(scope)
    role_match = EBS_ROLE.fullmatch(role or "")
    if (not scope_match or len(scope) > 160 or scope_match["region"] != region or not role_match
            or scope_match["account"] != role_match["account"]):
        raise ValueError("Expected the enrolled AWS account, region and assumed-role prefix.")
    pilot_uuid(enrollment)
    if epoch is None or not 1 <= epoch <= 2**31 - 1:
        raise ValueError("Expected the current runner credential epoch.")
    return scope_match["account"]


def collect_inventory(args, cli, account):
    prefix = ["aws", "--no-cli-pager", "--output", "json", "--region", args.region]
    try:
        identity = cli(prefix + ["sts", "get-caller-identity"])
        arn = identity["Arn"]
        prefix_length = len(args.expected_role_prefix)
        if (identity["Account"] != account or not isinstance(arn, str)
                or not arn.startswith(args.expected_role_prefix)
                or not re.fullmatch(r"[A-Za-z0-9+=,.@_-]{2,64}", arn[prefix_length:])):
            return "identity_unavailable", None
    except subprocess.TimeoutExpired:
        return "collector_timeout", None
    except (subprocess.CalledProcessError, OSError, ValueError, KeyError, TypeError, RecursionError):
        return "identity_unavailable", None

    try:
        document = cli(prefix + ["ec2", "describe-volumes"])
    except subprocess.TimeoutExpired:
        return "collector_timeout", None
    except subprocess.CalledProcessError as error:
        stderr = error.stderr or b""
        if any(code in stderr for code in (b"(UnauthorizedOperation)", b"(AccessDenied)", b"(AccessDeniedException)")):
            return "provider_denied", None
        return "provider_unavailable", None
    except OSError:
        return "provider_unavailable", None
    except (ValueError, RecursionError):
        return "invalid_inventory", None

    try:
        if not isinstance(document, dict):
            return "invalid_inventory", None
        if any(document.get(key) is not None for key in ("NextToken", "nextToken", "Marker", "NextMarker")):
            return "incomplete_inventory", None
        rows = document["Volumes"]
        if isinstance(rows, list) and len(rows) > MAX_RESOURCES:
            return "inventory_limit", None
        resources = ebs(document)
        validate_ebs(resources)
        return None, resources
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        return "invalid_inventory", None


def build_report(args, cli):
    account = validate_config(args.scope, args.region, args.expected_role_prefix,
                              args.enrollment_id, args.credential_epoch)
    started = datetime.now(timezone.utc)
    if args.collection_failed:
        error, resources = "provider_unavailable", None
    else:
        error, resources = collect_inventory(args, cli, account)
    captured = datetime.now(timezone.utc)
    payload = {
        "schema": 2, "platform": "aws", "scope": args.scope, "source_id": f"aws:{args.scope}",
        "enrollment_id": args.enrollment_id, "credential_epoch": args.credential_epoch,
        "delivery": str(uuid4()), "started_at": started.isoformat(), "captured_at": captured.isoformat(),
    }
    if error is None:
        payload.update(coverage="complete", projected_count=len(resources), resources=resources)
        if len(json.dumps(payload, separators=(",", ":")).encode()) > MAX_BODY:
            del payload["resources"], payload["projected_count"]
            error = "inventory_limit"
    if error is not None:
        payload.update(coverage="partial" if error in EBS_PARTIAL else "failed", error=error)
    pilot_envelope(payload, captured)
    return payload, captured
