"""Shared, dependency-free validation for infrastructure observations."""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

MAX_BODY = 1_000_000
MAX_RESOURCES = 500
MAX_VALUE = 2048
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,239}\Z")
FIELD = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
PLATFORMS = {"terraform", "docker", "kubernetes", "aws", "snapshot"}
ERRORS = {"collection_failed", "invalid_snapshot", "collector_timeout"}


# Native adapters send these exact projections. A schema change requires coordinated
# collector and server versioning. Custom snapshots have a separate, flexible contract.
RULES = {
    "terraform": {"*": {"action": str, "resource_type": str}},
    "docker": {"container": {
        "image": str, "privileged": bool, "read_only_root": bool, "memory_bytes": int, "nano_cpus": int,
        "network_mode": str, "restart_policy": str, "capabilities_digest": str, "port_bindings_digest": str,
    }},
    "kubernetes": {
        "deployment": {"images_digest": str, "resources_digest": str, "privileged": bool, "host_network": bool, "replicas": int},
        "statefulset": {"images_digest": str, "resources_digest": str, "privileged": bool, "host_network": bool, "replicas": int},
        "daemonset": {"images_digest": str, "resources_digest": str, "privileged": bool, "host_network": bool},
        "service": {"service_type": str, "ports_digest": str},
        "horizontalpodautoscaler": {"min_size": int, "max_size": int},
    },
    "aws": {
        "ec2": {"instance_type": str, "state": str},
        "ebs": {"size": int, "volume_type": str, "encrypted": bool, "iops": (int, type(None)), "throughput": (int, type(None))},
        "nat": {"connectivity_type": str},
        "rds": {"instance_class": str, "engine": str, "multi_az": bool, "publicly_accessible": bool,
                "encrypted": bool, "allocated_storage": (int, type(None)), "iops": (int, type(None))},
        "asg": {"min_size": int, "max_size": int, "desired_capacity": int},
        "elb": {"load_balancer_type": str, "scheme": str, "availability_zones": int},
        "eip": {"associated": bool},
    },
}

_DIGEST = re.compile(r"[a-f0-9]{64}\Z")


def validate_native(platform, resources):
    if platform == "snapshot":
        return
    definitions = RULES[platform]
    for identifier, fields in resources.items():
        kind, separator, name = identifier.partition("/")
        expected = definitions.get(kind) or definitions.get("*")
        if not separator or not name or not expected or fields.keys() != expected.keys():
            raise ValueError("Unexpected or missing native resource fields.")
        if platform == "terraform" and (kind != fields["resource_type"] or not re.fullmatch(r"[a-f0-9]{24}", name)):
            raise ValueError("Invalid Terraform resource projection.")
        for key, types in expected.items():
            value = fields[key]
            allowed = types if isinstance(types, tuple) else (types,)
            if type(value) not in allowed or (key.endswith("_digest") and not _DIGEST.fullmatch(value)):
                raise ValueError("Invalid native resource field.")


def _unique(pairs):
    obj = {}
    for name, value in pairs:
        if name in obj:
            raise ValueError("Duplicate JSON property.")
        obj[name] = value
    return obj


def decode(raw):
    return json.loads(raw, object_pairs_hook=_unique, parse_constant=_bad_number)


def _bad_number(_):
    raise ValueError("Non-finite JSON number.")


def validate_values(resources):
    if not isinstance(resources, dict) or len(resources) > MAX_RESOURCES:
        raise ValueError("Expected at most 500 resources.")
    for key, fields in resources.items():
        if not IDENTIFIER.fullmatch(key) or not isinstance(fields, dict) or not 1 <= len(fields) <= 32:
            raise ValueError("Invalid resource identity or fields.")
        for field, value in fields.items():
            if not FIELD.fullmatch(field) or type(value) not in (str, int, bool, type(None)):
                raise ValueError("Expected a scalar field.")
            if isinstance(value, str) and (len(value) > MAX_VALUE or not value.isprintable()):
                raise ValueError("Invalid field value.")
            if type(value) is int and not -(2**53) < value < 2**53:
                raise ValueError("Invalid integer.")
    return resources


def envelope(data, now):
    if not isinstance(data, dict):
        raise ValueError("Unknown observation envelope.")
    core_fields = set(data) - {"source_id"}
    if core_fields not in (
        {"schema", "platform", "scope", "delivery", "captured_at", "resources"},
        {"schema", "platform", "scope", "delivery", "captured_at", "error"},
    ):
        raise ValueError("Unknown observation envelope.")
    if type(data["schema"]) is not int or data["schema"] != 1 or data["platform"] not in tuple(PLATFORMS):
        raise ValueError("Unsupported schema or platform.")
    if "source_id" in data:
        source_id = data["source_id"]
        if not isinstance(source_id, str) or len(source_id) > 160 or not IDENTIFIER.fullmatch(source_id) or "error" in data:
            raise ValueError("Invalid source identifier.")
    scope = data["scope"]
    if not isinstance(scope, str) or len(scope) > 160 or not IDENTIFIER.fullmatch(scope):
        raise ValueError("Invalid scope.")
    if not isinstance(data["delivery"], str) or len(data["delivery"]) != 36:
        raise ValueError("Invalid delivery ID.")
    delivery = uuid.UUID(data["delivery"])
    captured = datetime.fromisoformat(data["captured_at"])
    if captured.tzinfo is None:
        raise ValueError("Capture time requires a timezone.")
    captured = captured.astimezone(timezone.utc)
    if not now - timedelta(minutes=15) <= captured <= now + timedelta(seconds=30):
        raise ValueError("Observation is outside the delivery window.")
    if "resources" in data:
        validate_native(data["platform"], validate_values(data["resources"]))
    elif data["error"] not in tuple(ERRORS):
        raise ValueError("Invalid collection error.")
    return delivery, captured


EBS_SCOPE = re.compile(r"(?P<account>[0-9]{12}):(?P<region>[a-z]{2}(?:-[a-z0-9]+)+-[0-9]+):ebs\Z")
EBS_ID = re.compile(r"ebs/vol-(?:[0-9a-f]{8}|[0-9a-f]{17})\Z")
EBS_ROLE = re.compile(r"arn:aws:sts::(?P<account>[0-9]{12}):assumed-role/[A-Za-z0-9+=,.@_/-]{1,192}/\Z")
EBS_FAILURES = {
    "incomplete_inventory", "inventory_limit", "invalid_inventory", "provider_denied",
    "provider_unavailable", "collector_timeout", "identity_unavailable",
}
EBS_PARTIAL = {"incomplete_inventory", "inventory_limit", "invalid_inventory"}


def pilot_uuid(value):
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError("Expected a canonical UUID.")
    parsed = uuid.UUID(value)
    if str(parsed) != value or not parsed.int:
        raise ValueError("Expected a canonical UUID.")
    return parsed


def pilot_time(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("Invalid observation time.")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Observation time requires a timezone.")
    return parsed.astimezone(timezone.utc)


def validate_ebs(resources):
    validate_native("aws", validate_values(resources))
    for key, fields in resources.items():
        if not EBS_ID.fullmatch(key):
            raise ValueError("Expected an EBS volume ID.")
        if fields["size"] <= 0 or not fields["volume_type"]:
            raise ValueError("Invalid EBS configuration.")
        if any(fields[name] is not None and fields[name] < 0 for name in ("iops", "throughput")):
            raise ValueError("Invalid EBS performance settings.")


def pilot_envelope(data, now):
    fields = {"schema", "platform", "scope", "source_id", "enrollment_id",
              "credential_epoch", "delivery", "started_at", "captured_at", "coverage"}
    if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 2:
        raise ValueError("Unsupported EBS observation schema.")
    coverage = data.get("coverage")
    if coverage == "complete":
        fields |= {"resources", "projected_count"}
    elif coverage in ("partial", "failed"):
        fields.add("error")
    else:
        raise ValueError("Invalid coverage.")
    if data.keys() != fields or data["platform"] != "aws":
        raise ValueError("Unexpected EBS observation fields.")
    scope = data["scope"]
    if not isinstance(scope, str) or len(scope) > 160 or not EBS_SCOPE.fullmatch(scope):
        raise ValueError("Invalid EBS scope.")
    if data["source_id"] != f"aws:{scope}":
        raise ValueError("Invalid source.")
    if type(data["credential_epoch"]) is not int or not 1 <= data["credential_epoch"] <= 2**31 - 1:
        raise ValueError("Invalid credential epoch.")
    started, captured = pilot_time(data["started_at"]), pilot_time(data["captured_at"])
    if not timedelta(0) <= captured - started <= timedelta(minutes=5):
        raise ValueError("Invalid observation window.")
    if not now - timedelta(minutes=15) <= captured <= now + timedelta(seconds=30):
        raise ValueError("Observation is outside the delivery window.")
    if coverage == "complete":
        validate_ebs(data["resources"])
        if type(data["projected_count"]) is not int or data["projected_count"] != len(data["resources"]):
            raise ValueError("Incorrect EBS resource count.")
    elif (not isinstance(data["error"], str) or data["error"] not in EBS_FAILURES
          or (data["error"] in EBS_PARTIAL) != (coverage == "partial")):
        raise ValueError("Invalid coverage error.")
    return pilot_uuid(data["delivery"]), captured, started, pilot_uuid(data["enrollment_id"])
