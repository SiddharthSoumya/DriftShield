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
from .models import Baseline, Delivery, Finding, Target


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
    if target.last_error:
        return "Collection failed", "danger"
    last = target.last_success or target.created_at
    if now - last > timedelta(minutes=2 * target.interval + 5):
        return "Overdue", "warning"
    if not target.last_success:
        return "Awaiting observation", "muted"
    if getattr(target, "open_count", 0):
        return "Drift detected", "danger"
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


def compare(target, expected, now):
    current = {item.resource: item for item in Finding.objects.filter(target=target, closed_at__isnull=True)}
    opened = 0
    changed = False
    for key in sorted(expected.keys() | target.latest.keys() | current.keys()):
        before, after, finding = expected.get(key), target.latest.get(key), current.get(key)
        different = not same(before, after)
        changed |= different
        if finding and different and same(finding.expected, before) and same(finding.actual, after):
            finding.count += 1
            finding.last_seen = now
            finding.save(update_fields=["count", "last_seen"])
            continue
        if finding:
            finding.closed_at = now
            if different:
                finding.resolution = "changed"
            elif target.platform == "terraform":
                finding.resolution = "reconciled"
            else:
                finding.resolution = "verified" if finding.review_at else "restored"
            finding.save(update_fields=["closed_at", "resolution"])
        if different:
            Finding.objects.create(target=target, resource=key, expected=before, actual=after, priority=risk(before, after),
                                   first_seen=now, last_seen=now)
            opened += 1
    if opened:
        alert(target, "Infrastructure drift", f"{opened} new or changed infrastructure findings.")
    return changed


def accept(target, delivery, captured, payload, now):
    """Caller holds workspace and target locks."""
    previous = Delivery.objects.filter(target=target, identifier=delivery).first()
    if previous:
        if previous.captured_at != captured or (previous.payload_hash and previous.payload_hash != fingerprint(payload)):
            raise Conflict("Delivery ID has already been used.")
        return previous.status
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
        result = "unbaselined"
        if target.platform == "terraform":
            changed = compare(target, {}, now)
            result = "drift" if changed else "match" if target.version else "unbaselined"
        elif target.version:
            baseline = Baseline.objects.get(target=target, version=target.version)
            result = "drift" if compare(target, baseline.values, now) else "match"
    target.save()
    Delivery.objects.create(target=target, identifier=delivery, captured_at=captured, received_at=now,
                            status=result, payload_hash=fingerprint(payload))
    return result


def fresh(target, now):
    return target.last_success and not target.last_error and now - target.last_success <= timedelta(minutes=2 * target.interval + 5)


def approve(target, revision, version, selected, actor, reason):
    """Caller holds workspace and target locks."""
    now = timezone.now()
    if not fresh(target, now) or target.revision != revision or target.version != version:
        raise Conflict("The observation or baseline changed or expired. Refresh the page.")
    if not reason.strip() or len(reason) > 240:
        raise Conflict("Enter an approval reason.")
    if target.platform == "terraform" and (version or target.latest):
        raise Conflict("Terraform's refreshed state must match the recorded state before a baseline can be approved. Review this plan in your IaC workflow.")
    if version:
        previous = Baseline.objects.get(target=target, version=version).values
        findings = Finding.objects.filter(target=target, pk__in=selected, closed_at__isnull=True)
        if not selected or findings.count() != len(set(selected)):
            raise Conflict("Select current findings to approve.")
        values = previous.copy()
        for finding in findings:
            if finding.actual is None:
                values.pop(finding.resource, None)
            else:
                values[finding.resource] = finding.actual
        if len(values) > MAX_RESOURCES:
            raise Conflict("The resulting baseline exceeds the resource limit. Accept removals first or split the target.")
        findings.update(closed_at=now, resolution="accepted")
    else:
        values = target.latest
    target.version += 1
    Baseline.objects.create(target=target, version=target.version, values=values, actor=actor, reason=reason.strip())
    target.save(update_fields=["version"])
    audit(target.workspace, actor, "infra_baseline_approved", target=target.name, version=target.version, reason=reason.strip())


def maintenance(workspace, cutoff, now):
    Delivery.objects.filter(target__workspace=workspace, received_at__lt=cutoff).delete()
    Finding.objects.filter(target__workspace=workspace, closed_at__lt=cutoff).delete()
    due = Target.objects.filter(workspace=workspace, overdue_alerted=False, last_error="")
    for pk in due.values_list("pk", flat=True):
        with transaction.atomic():
            locked_workspace = Workspace.objects.select_for_update().filter(pk=workspace.pk).first()
            if not locked_workspace:
                return
            target = Target.objects.select_for_update().filter(pk=pk, overdue_alerted=False, last_error="").first()
            if target and status(target, now)[0] == "Overdue":
                target.overdue_alerted = True
                target.save(update_fields=["overdue_alerted"])
                alert(target, "Infrastructure observation overdue", "Check the collector schedule and upstream access.")
