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
    if not isinstance(data, dict) or set(data) not in (
        {"schema", "platform", "scope", "delivery", "captured_at", "resources"},
        {"schema", "platform", "scope", "delivery", "captured_at", "error"},
    ):
        raise ValueError("Unknown observation envelope.")
    if type(data["schema"]) is not int or data["schema"] != 1 or data["platform"] not in tuple(PLATFORMS):
        raise ValueError("Unsupported schema or platform.")
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
