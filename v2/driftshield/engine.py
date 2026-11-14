from datetime import datetime, timedelta, timezone
import hashlib
import json

from .github import FIELDS, ScanError
from .store import audit, encode, now


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def observation(metadata):
    if not isinstance(metadata, dict):
        raise ScanError("Observation must be an object.")
    repo_id = metadata.get("id")
    if type(repo_id) is not int or not 0 < repo_id < 2**63:
        raise ScanError("Observation requires a valid GitHub repository ID.")
    values = {field: metadata.get(field) for field in FIELDS}
    if values["visibility"] not in ("private", "internal", "public"):
        raise ScanError("Visibility was missing or unsupported.")
    branch = values["default_branch"]
    if not isinstance(branch, str) or not 0 < len(branch) <= 255 or not branch.isprintable():
        raise ScanError("Default branch was missing or invalid.")
    if any(type(values[key]) is not bool for key in ("archived", "has_issues", "has_wiki")):
        raise ScanError("A monitored setting was missing or had the wrong type.")
    return repo_id, values


def ingest(db, monitor, metadata, observed_at, delivery_id=None):
    monitor_id = monitor["id"]
    if delivery_id and db.execute(
        "SELECT 1 FROM scans WHERE monitor_id=? AND delivery_id=?", (monitor_id, delivery_id)
    ).fetchone():
        return "duplicate"
    if monitor["last_attempt"] and observed_at <= monitor["last_attempt"]:
        return "stale"
    repo_id, values = observation(metadata)
    if monitor["repo_id"] is not None and monitor["repo_id"] != repo_id:
        raise ScanError("The repository ID changed. Review the monitor before trusting this observation.")
    db.execute(
        """UPDATE monitors SET repo_id=?, latest=?, last_attempt=?, last_success=?, last_error=NULL
           WHERE id=?""",
        (repo_id, encode(values), observed_at, observed_at, monitor_id),
    )
    version = monitor["baseline_version"]
    if version is None:
        status = "unbaselined"
    else:
        row = db.execute(
            "SELECT values_json FROM baselines WHERE monitor_id=? AND version=?", (monitor_id, version)
        ).fetchone()
        baseline = json.loads(row["values_json"])
        status = "clean" if values == baseline else "drift"
        update_incidents(db, monitor_id, version, baseline, values, observed_at)
    db.execute(
        "INSERT INTO scans(monitor_id, observed_at, delivery_id, status) VALUES (?, ?, ?, ?)",
        (monitor_id, observed_at, delivery_id, status),
    )
    return status


def update_incidents(db, monitor_id, version, baseline, values, observed_at):
    current = {
        row["field"]: row
        for row in db.execute("SELECT * FROM incidents WHERE monitor_id=? AND closed_at IS NULL", (monitor_id,))
    }
    for field, value in values.items():
        expected = baseline[field]
        actual = encode(value)
        incident = current.get(field)
        if incident and value != expected and incident["actual"] == actual:
            db.execute(
                "UPDATE incidents SET last_seen=?, occurrences=occurrences+1 WHERE id=?",
                (observed_at, incident["id"]),
            )
            continue
        if incident:
            resolution = "restored" if value == expected else "changed_again"
            db.execute(
                "UPDATE incidents SET closed_at=?, resolution=? WHERE id=?",
                (observed_at, resolution, incident["id"]),
            )
        if value != expected:
            db.execute(
                """INSERT INTO incidents(monitor_id, baseline_version, field, expected, actual,
                   severity, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (monitor_id, version, field, encode(expected), actual, FIELDS[field][1], observed_at, observed_at),
            )


def record_error(db, monitor, message, observed_at, delivery_id=None):
    monitor_id = monitor["id"]
    if delivery_id and db.execute(
        "SELECT 1 FROM scans WHERE monitor_id=? AND delivery_id=?", (monitor_id, delivery_id)
    ).fetchone():
        return "duplicate"
    if monitor["last_attempt"] and observed_at <= monitor["last_attempt"]:
        return "stale"
    db.execute("UPDATE monitors SET last_attempt=?, last_error=? WHERE id=?", (observed_at, message, monitor_id))
    db.execute(
        "INSERT INTO scans(monitor_id, observed_at, delivery_id, status, error) VALUES (?, ?, ?, 'error', ?)",
        (monitor_id, observed_at, delivery_id, message),
    )
    return "error"


def accept_baseline(db, monitor, observed_at):
    if not monitor["latest"] or monitor["last_error"] or monitor["last_success"] != observed_at:
        raise ScanError("The displayed observation is no longer current. Refresh and review the latest scan.")
    version = (monitor["baseline_version"] or 0) + 1
    accepted_at = now()
    db.execute(
        "INSERT INTO baselines(monitor_id, version, values_json, created_at) VALUES (?, ?, ?, ?)",
        (monitor["id"], version, monitor["latest"], accepted_at),
    )
    db.execute("UPDATE monitors SET baseline_version=? WHERE id=?", (version, monitor["id"]))
    db.execute(
        """UPDATE incidents SET closed_at=?, resolution='baseline_accepted'
           WHERE monitor_id=? AND closed_at IS NULL""",
        (accepted_at, monitor["id"]),
    )
    audit(db, monitor["id"], "baseline_accepted", f"Approved baseline v{version}")


def parse_time(value):
    try:
        observed = datetime.fromisoformat(value)
        if observed.tzinfo is None:
            raise ValueError
        observed = observed.astimezone(timezone.utc)
        if observed > datetime.now(timezone.utc) + timedelta(seconds=30):
            raise ValueError
        return observed.isoformat(timespec="microseconds")
    except (TypeError, ValueError, OverflowError) as error:
        raise ScanError("Observation requires a valid timestamp with timezone.") from error
