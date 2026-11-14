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
        validate_values(data["resources"])
    elif data["error"] not in tuple(ERRORS):
        raise ValueError("Invalid collection error.")
    return delivery, captured
