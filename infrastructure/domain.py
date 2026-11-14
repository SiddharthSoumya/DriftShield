from datetime import timedelta
import json
import hashlib
import secrets

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from collector.contract import MAX_RESOURCES
from governance.domain import audit, notify
from governance.models import Workspace
from .models import Baseline, Delivery, DriftException, EvidenceEvent, Finding, Target
from .triage import effective_changes, record, resource_changes


class Conflict(Exception):
    pass


class RateLimited(Exception):
    pass


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token():
    token = secrets.token_urlsafe(32)
    return token, digest(token)


def status(target, now):
    if not target.token_expires_at or target.token_expires_at <= now:
        return "Collector credential inactive", "danger"
    if target.last_error:
        return "Collection failed", "danger"
    last = target.last_success or target.created_at
    if now - last > timedelta(minutes=2 * target.interval + 5):
        return "Overdue", "warning"
    if not target.last_success:
        return "Awaiting observation", "muted"
    if getattr(target, "open_count", 0):
        return "Drift detected", "danger"
    if getattr(target, "suppressed_count", 0):
        return "Excepted drift", "warning"
    if not target.version:
        return "Needs baseline", "warning"
    return "Matches baseline", "good"


def risk(before, after):
    if before is None or after is None:
        return "high"
    increases = ("replicas", "desired_capacity", "max_size", "allocated_storage", "size", "iops", "throughput")
    if any(type(after.get(key)) is int and type(before.get(key)) is int and after[key] > before[key] for key in increases):
        return "high"
    if any(after.get(key) is True and before.get(key) is not True for key in ("privileged", "publicly_accessible")):
        return "high"
    if any(key in before and before[key] != after.get(key) for key in ("instance_type", "instance_class", "image", "volume_type", "images_digest", "resources_digest")):
        return "high"
    return "medium"


def alert(target, subject, detail):
    link = f"{settings.APP_ORIGIN}/w/{target.workspace.slug}/infra/{target.pk}/"
    notify(target.workspace, f"{subject}: {target.name}", f"{detail}\n\n{link}")


def fingerprint(payload):
    return digest(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def same(left, right):
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def compare(target, expected, now, captured, count_observation=True):
    current = {
        item.resource: item
        for item in Finding.objects.filter(target=target, closed_at__isnull=True)
    }
    exceptions = {}
    for item in DriftException.objects.filter(target=target, ended_at__isnull=True, expires_at__gt=now):
        exceptions.setdefault(item.resource, {})[item.field] = item
    actionable = 0
    excepted = 0
    newly_actionable = []
    for key in sorted(expected.keys() | target.latest.keys() | current.keys()):
        before, after, finding = expected.get(key), target.latest.get(key), current.get(key)
        different = not same(before, after)
        suppressed = different and before is not None and after is not None and not effective_changes(
            before, after, exceptions.get(key, {}), same,
        )
        if different:
            if suppressed:
                excepted += 1
            else:
                actionable += 1
        if finding and different and same(finding.expected, before) and same(finding.actual, after):
            if count_observation:
                finding.count += 1
                finding.last_seen = now
            if finding.suppressed != suppressed:
                finding.suppressed = suppressed
                record(target, "excepted" if suppressed else "reopened", finding=finding,
                       revision=target.revision)
                if not suppressed:
                    newly_actionable.append(finding)
            finding.save(update_fields=["count", "last_seen", "suppressed"])
            continue
        if finding and not different and finding.review_at and captured <= finding.review_at:
            # A delivery captured before the human review cannot confirm the reviewed fix.
            if not finding.suppressed:
                actionable += 1
            else:
                excepted += 1
            continue
        inherited_owner_id = finding.owner_id if finding else None
        if finding:
            finding.closed_at = now
            if different:
                finding.resolution = "changed"
            elif target.platform == "terraform":
                finding.resolution = "reconciled"
            else:
                finding.resolution = "verified" if finding.review_at else "restored"
            finding.save(update_fields=["closed_at", "resolution"])
            record(target, "closed", finding=finding, resolution=finding.resolution, revision=target.revision)
        if different:
            current_finding = Finding.objects.create(
                target=target, resource=key, expected=before, actual=after, priority=risk(before, after),
                first_seen=now, last_seen=now, suppressed=suppressed, baseline_version=target.version,
                owner_id=inherited_owner_id,
            )
            record(target, "opened", finding=current_finding, revision=target.revision,
                   baseline_version=target.version, priority=current_finding.priority, suppressed=suppressed)
            if not suppressed:
                newly_actionable.append(current_finding)
    if target.workspace.infra_high_priority_only:
        newly_actionable = [item for item in newly_actionable if item.priority == "high"]
    if newly_actionable:
        highest = "high" if any(item.priority == "high" for item in newly_actionable) else "medium"
        alert(target, f"{highest.capitalize()} priority infrastructure drift",
              f"{len(newly_actionable)} new, changed, or newly actionable infrastructure findings. "
              f"Source observation: {captured.isoformat()}.")
    return "drift" if actionable else "excepted" if excepted else "match"


def compare_pilot(target, delivery, now):
    from .pilot_review import compare_complete

    return compare_complete(target, delivery, now)


def accept(target, delivery, captured, payload, now):
    """Caller holds workspace and target locks."""
    if target.profile != Target.Profile.LEGACY:
        raise Conflict("This target requires the pilot protocol.")
    previous = Delivery.objects.filter(target=target, identifier=delivery).first()
    if previous:
        if previous.captured_at != captured or (previous.payload_hash and previous.payload_hash != fingerprint(payload)):
            raise Conflict("Delivery ID has already been used.")
        return previous.status
    if "resources" in payload and target.source_id and payload.get("source_id") != target.source_id:
        raise Conflict("Observation source changed or is missing. Check runner scope or create a new target.")
    if target.last_attempt and now - target.last_attempt < timedelta(seconds=settings.DS_INGEST_MIN_SECONDS):
        raise RateLimited
    last = Delivery.objects.filter(target=target).order_by("-captured_at").values_list("captured_at", flat=True).first()
    if last and captured <= last:
        raise Conflict("Observation arrived out of order.")
    target.last_attempt = now
    target.overdue_alerted = False
    if "error" in payload:
        if not target.last_error:
            alert(target, "Collection failed", "The collector reported an error. The last successful observation remains available.")
        target.last_error = payload["error"]
        result = "error"
    else:
        target.last_success = captured
        target.last_error = ""
        target.revision += 1
        target.latest = payload["resources"]
        if payload.get("source_id") and not target.source_id:
            target.source_id = payload["source_id"]
        result = "unbaselined"
        if target.platform == "terraform":
            result = compare(target, {}, now, captured)
            if result == "match" and not target.version:
                result = "unbaselined"
        elif target.version:
            baseline = Baseline.objects.get(target=target, version=target.version)
            result = compare(target, baseline.values, now, captured)
    target.save()
    Delivery.objects.create(target=target, identifier=delivery, captured_at=captured, received_at=now,
                            status=result, payload_hash=fingerprint(payload))
    return result


def fresh(target, now):
    if target.profile != Target.Profile.LEGACY:
        return False
    return (target.token_expires_at and now < target.token_expires_at and target.last_success
            and not target.last_error and now - target.last_success <= timedelta(minutes=2 * target.interval + 5))


def approve(target, revision, version, selected, actor, reason):
    """Caller holds workspace and target locks."""
    now = timezone.now()
    if not fresh(target, now) or target.revision != revision or target.version != version:
        raise Conflict("The observation or baseline changed or expired. Refresh the page.")
    if not reason.strip() or len(reason) > 240:
        raise Conflict("Enter an approval reason.")
    if target.platform == "terraform" and (version or target.latest):
        raise Conflict("Terraform's refreshed state must match the recorded state before a baseline can be approved. Review this plan in your IaC workflow.")
    if any(not 0 < pk < 2**63 for pk in selected):
        raise Conflict("Select current findings to approve.")
    if version:
        previous = Baseline.objects.get(target=target, version=version).values
        findings = Finding.objects.filter(target=target, pk__in=selected, closed_at__isnull=True)
        if not selected or findings.count() != len(set(selected)):
            raise Conflict("Select current findings to approve.")
        values = previous.copy()
        for finding in findings:
            if finding.suppressed or not same(target.latest.get(finding.resource), finding.actual):
                raise Conflict("This finding is excepted or has a newer observation. Review the current configuration.")
            active_exceptions = DriftException.objects.filter(
                target=target, resource=finding.resource, ended_at__isnull=True, expires_at__gt=now,
            )
            changed_fields = resource_changes(finding.expected, finding.actual, same)
            if active_exceptions.filter(field__in=[change["field"] for change in changed_fields]).exists():
                raise Conflict("End active exceptions for these fields before accepting this resource.")
            if finding.actual is None:
                values.pop(finding.resource, None)
            else:
                values[finding.resource] = finding.actual
        if len(values) > MAX_RESOURCES:
            raise Conflict("The resulting baseline exceeds the resource limit. Accept removals first or split the target.")
        for finding in findings:
            record(target, "closed", actor, finding, resolution="accepted", revision=target.revision)
        findings.update(closed_at=now, resolution="accepted")
    else:
        values = target.latest
    target.version += 1
    Baseline.objects.create(target=target, version=target.version, values=values, actor=actor, reason=reason.strip())
    target.save(update_fields=["version"])
    record(target, "baseline_approved", actor, version=target.version, revision=target.revision, reason=reason.strip())
    audit(target.workspace, actor, "infra_baseline_approved", target=target.name, version=target.version, reason=reason.strip())


def refresh_exceptions(target, now):
    if target.profile == Target.Profile.LEGACY and target.version and target.last_success and target.platform != "terraform":
        baseline = Baseline.objects.get(target=target, version=target.version)
        compare(target, baseline.values, now, target.last_success, count_observation=False)


def expire_exceptions(now):
    target_ids = DriftException.objects.filter(target__profile=Target.Profile.LEGACY, ended_at__isnull=True, expires_at__lte=now).values_list(
        "target_id", "target__workspace_id",
    ).order_by("target_id").distinct()[:100]
    for target_id, workspace_id in list(target_ids):
        with transaction.atomic():
            workspace = Workspace.objects.select_for_update().filter(pk=workspace_id).first()
            if not workspace:
                continue
            target = Target.objects.select_for_update().filter(pk=target_id, workspace=workspace).first()
            if not target:
                continue
            expired = DriftException.objects.filter(target=target, ended_at__isnull=True, expires_at__lte=now)
            for item in expired:
                record(target, "exception_expired", "worker", field=item.field, resource_id=item.resource, exception=item.pk)
            expired.update(ended_at=now)
            refresh_exceptions(target, now)


def maintenance(workspace, cutoff):
    legacy = Target.objects.filter(workspace=workspace, profile=Target.Profile.LEGACY)
    Delivery.objects.filter(target__in=legacy, received_at__lt=cutoff).delete()
    Finding.objects.filter(target__in=legacy, closed_at__lt=cutoff).delete()
    EvidenceEvent.objects.filter(target__in=legacy, created_at__lt=cutoff).delete()
    DriftException.objects.filter(target__in=legacy, ended_at__isnull=False, expires_at__lt=cutoff).delete()


def check_overdue(now):
    for pk, workspace_id in Target.objects.filter(profile=Target.Profile.LEGACY, overdue_alerted=False).values_list("pk", "workspace_id").iterator():
        with transaction.atomic():
            workspace = Workspace.objects.select_for_update().filter(pk=workspace_id).first()
            if not workspace:
                continue
            target = Target.objects.select_for_update().filter(pk=pk, workspace=workspace, overdue_alerted=False).first()
            if not target:
                continue
            inactive = not target.token_expires_at or target.token_expires_at <= now
            last = target.last_success or target.created_at
            if not inactive and (target.last_error or now - last <= timedelta(minutes=2 * target.interval + 5)):
                continue
            target.overdue_alerted = True
            target.save(update_fields=["overdue_alerted"])
            if inactive:
                alert(target, "Infrastructure credential inactive", "Rotate or reconnect the collector credential if monitoring is still required.")
            else:
                alert(target, "Infrastructure observation overdue", "Check the collector schedule and upstream access.")
