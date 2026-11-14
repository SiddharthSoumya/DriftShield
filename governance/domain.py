from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from django.conf import settings

from .github import CollectionError, FIELDS
from .models import Audit, Baseline, Incident, Monitor, Notification, Scan, Workspace


POLICY_LABELS = {
    "policy_visibility": "Policy · Restrict public visibility",
    "policy_reviews": "Policy · Minimum pull-request approvals",
    "policy_signatures": "Policy · Require signed commits",
}
INCIDENT_LABELS = {**FIELDS, **POLICY_LABELS}


class Conflict(Exception):
    pass


def audit(workspace, actor, action, monitor=None, **detail):
    Audit.objects.create(workspace=workspace, monitor=monitor, actor=actor, action=action, detail=detail)


def notify(workspace, subject, body, *, canary_slot=None):
    if not workspace.recipient:
        return None
    if canary_slot is not None:
        notification, _ = Notification.objects.exclude(
            disposition=Notification.Disposition.ROUTE_CHANGED,
        ).get_or_create(
            workspace=workspace, canary_slot=canary_slot,
            defaults={"recipient": workspace.recipient, "subject": subject[:200], "body": body},
        )
        return notification
    return Notification.objects.create(workspace=workspace, recipient=workspace.recipient,
                                       subject=subject[:200], body=body)


def severity(field, expected, observed):
    if field == "visibility" and observed == "public" and expected != "public":
        return "critical"
    if field in ("rules", "default_branch"):
        return "high"
    return "medium"


def update_findings(monitor, comparisons, timestamp):
    incidents = {item.field: item for item in Incident.objects.filter(monitor=monitor, closed_at__isnull=True)}
    for field, (expected, observed) in comparisons.items():
        current = incidents.get(field)
        violates = expected is not None and expected != observed
        if current and current.expected == expected and current.observed == observed and violates:
            current.last_seen = timestamp
            current.occurrences += 1
            current.save(update_fields=["last_seen", "occurrences"])
            continue
        if current:
            current.closed_at = timestamp
            current.resolution = "changed" if violates else "policy_disabled" if expected is None else "restored"
            current.save(update_fields=["closed_at", "resolution"])
        if violates:
            level = severity(field.removeprefix("policy_"), expected, observed)
            if field == "policy_visibility":
                level = "critical"
            elif field in POLICY_LABELS:
                level = "high"
            item = Incident.objects.create(
                monitor=monitor, field=field, expected=expected, observed=observed, severity=level,
                first_seen=timestamp, last_seen=timestamp, baseline_version=monitor.baseline_version,
            )
            url = f"{settings.APP_ORIGIN}/w/{monitor.workspace.slug}/repos/{monitor.pk}/"
            notify(monitor.workspace, f"[{level.upper()}] {monitor.full_name}: {INCIDENT_LABELS[field]}",
                   f"New incident #{item.pk}. Review the finding in DriftShield: {url}")


def policy_comparisons(workspace, values):
    approvals = max(
        (rule["parameters"].get("required_approving_review_count", 0) for rule in values["rules"] if rule["type"] == "pull_request"),
        default=0,
    )
    if type(approvals) is not int or not 0 <= approvals <= 100:
        raise CollectionError("GitHub returned an invalid approval count.")
    return {
        "policy_visibility": ("non-public" if workspace.require_private else None,
                              "public" if values["visibility"] == "public" else "non-public"),
        "policy_reviews": (workspace.min_reviews or None, min(approvals, workspace.min_reviews)),
        "policy_signatures": (True if workspace.require_signatures else None,
                              any(rule["type"] == "required_signatures" for rule in values["rules"])),
    }


@transaction.atomic
def complete_scan(monitor_id, lease, snapshot=None, error=None):
    # Lock order matches workspace import and policy configuration.
    workspace_id = Monitor.objects.values_list("workspace_id", flat=True).get(pk=monitor_id)
    workspace = Workspace.objects.select_for_update().get(pk=workspace_id)
    monitor = Monitor.objects.select_for_update().get(pk=monitor_id)
    monitor.workspace = workspace
    if monitor.lease != lease or monitor.paused:
        return False
    timestamp = timezone.now()
    prior_failures = monitor.failures
    monitor.last_attempt = timestamp
    if snapshot:
        github_id, name, values = snapshot
        collision = Monitor.objects.filter(workspace=monitor.workspace).exclude(pk=monitor.pk).filter(github_id=github_id).exists()
        collision |= Monitor.objects.filter(workspace=monitor.workspace, full_name=name).exclude(pk=monitor.pk).exists()
        if collision:
            error = CollectionError("Repository identity conflicts with another monitor in this workspace.")
    comparisons = {}
    if not error and not snapshot:
        raise ValueError("A scan needs a snapshot or a collection error.")
    if not error:
        try:
            comparisons = policy_comparisons(workspace, values)
        except (CollectionError, TypeError):
            error = CollectionError("GitHub returned an invalid rule parameter.")
    if error:
        monitor.failures = min(monitor.failures + 1, 30)
        monitor.last_error = str(error)[:240]
        backoff = min(6 * 3600, monitor.interval_minutes * 60 * 2 ** min(monitor.failures - 1, 6))
        delay = max(error.retry_seconds, backoff)
        status = "error"
        if prior_failures == 0:
            notify(monitor.workspace, f"Collection unavailable: {monitor.full_name}", monitor.last_error)
    else:
        monitor.github_id = github_id
        monitor.full_name = name
        monitor.latest = values
        monitor.last_success = timestamp
        monitor.last_error = ""
        monitor.failures = 0
        monitor.overdue_notified = False
        monitor.revision += 1
        delay = monitor.interval_minutes * 60
        status = "unbaselined"
        if monitor.baseline_version:
            expected = Baseline.objects.get(monitor=monitor, version=monitor.baseline_version).values
            comparisons.update({field: (expected[field], values[field]) for field in FIELDS})
            status = "match" if values == expected else "drift"
        if any(comparisons[field][0] is not None and comparisons[field][0] != comparisons[field][1] for field in POLICY_LABELS):
            status = "policy_violation" if status != "drift" else status
        update_findings(monitor, comparisons, timestamp)
    monitor.lease = monitor.lease_until = None
    monitor.next_scan = timestamp + timedelta(seconds=delay)
    monitor.save()
    Scan.objects.create(monitor=monitor, collected_at=timestamp, status=status, error=monitor.last_error)
    return True


@transaction.atomic
def approve(workspace, monitor_id, actor, revision, version, fields, reason):
    workspace = Workspace.objects.select_for_update().get(pk=workspace.pk)
    monitor = Monitor.objects.select_for_update().get(workspace=workspace, pk=monitor_id)
    timestamp = timezone.now()
    if (monitor.paused or not monitor.last_success or monitor.last_error
            or timestamp - monitor.last_success > timedelta(minutes=2 * monitor.interval_minutes + 5)
            or monitor.revision != revision or monitor.baseline_version != version):
        raise Conflict("This observation or baseline changed, failed, or expired. Review a fresh scan.")
    if not fields or not set(fields) <= FIELDS.keys() or not reason.strip() or len(reason) > 240:
        raise Conflict("Select monitored fields and enter an approval reason.")
    previous = Baseline.objects.get(monitor=monitor, version=version).values if version else {}
    if not version and set(fields) != FIELDS.keys():
        raise Conflict("The first baseline must include every monitored field.")
    changes = {field: monitor.latest[field] for field in fields if previous.get(field) != monitor.latest[field]}
    if not changes:
        raise Conflict("Selected settings already match the baseline.")
    values = {**previous, **changes}
    monitor.baseline_version += 1
    Baseline.objects.create(monitor=monitor, version=monitor.baseline_version, values=values, reason=reason.strip(), actor=actor)
    monitor.save(update_fields=["baseline_version"])
    Incident.objects.filter(monitor=monitor, field__in=changes, closed_at__isnull=True).update(
        closed_at=timestamp, resolution="approved",
    )
    audit(workspace, actor, "baseline_approved", monitor, fields=sorted(changes), version=monitor.baseline_version, reason=reason.strip())
