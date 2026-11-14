"""Dedicated pilot topology and independently consumable health checks."""
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.db import connection
from django.db.models import Min
from django.utils import timezone

from infrastructure.models import Target
from infrastructure.pilot_intake import coverage_state
from .models import Integration, Membership, Monitor, Notification, WorkerHealth, Workspace

WORKSPACE_LOCK = 4951313607235522897


def topology_issues(slug, *, activate=False, bootstrap=False):
    issues = []
    workspace = Workspace.objects.filter(slug=slug).first()
    if not settings.DS_PILOT_EBS or slug != settings.DS_PILOT_WORKSPACE:
        issues.append("Pilot mode and the configured workspace must match.")
    if connection.vendor != "postgresql":
        issues.append("PostgreSQL is required.")
    if urlsplit(settings.APP_ORIGIN).scheme != "https":
        issues.append("The origin must use HTTPS.")
    if Workspace.objects.exclude(slug=slug).exists() or (workspace is None and not bootstrap):
        issues.append("Exactly one dedicated workspace is required.")
    if (Monitor.objects.exists() or Integration.objects.exists()
            or Target.objects.exclude(profile=Target.Profile.EBS_PILOT).exists()
            or Target.objects.exclude(workspace__slug=slug).exists()):
        issues.append("Dedicated deployment contains legacy integrations or targets.")
    if activate:
        active = Target.objects.filter(workspace=workspace, profile=Target.Profile.EBS_PILOT, retired_at=None)
        if active.count() != 1 or not active.filter(
            runnerenrollment__status="active", runnerenrollment__primary_operator__is_active=True,
            token_expires_at__gt=timezone.now(),
        ).exists():
            issues.append("One active EBS target with an enrolled runner is required.")
        elif not active.filter(runnerenrollment__primary_operator__membership__workspace=workspace,
                               runnerenrollment__primary_operator__membership__role__in=("owner", "operator")).exists():
            issues.append("The enrolled runner operator must be a current workspace member.")
        if workspace is None or not workspace.recipient or not settings.EMAIL_HOST:
            issues.append("Configure SMTP and the customer mailbox.")
        if not settings.DS_PILOT_PROBE_TOKEN:
            issues.append("Configure the independent probe token.")
        if not settings.DS_RELEASE_SHA:
            issues.append("Install a release with a verified Git commit SHA.")
        owners = Membership.objects.filter(workspace=workspace, role="owner", user__is_active=True)
        if owners.count() < 2:
            issues.append("Two named owners are required for approval and offboarding.")
    return issues


def health_payload(now):
    workspace = Workspace.objects.filter(slug=settings.DS_PILOT_WORKSPACE).first()
    target = Target.objects.filter(workspace=workspace, profile=Target.Profile.EBS_PILOT, retired_at=None).first()
    notifications = Notification.objects.filter(workspace=workspace)
    worker_tick = WorkerHealth.objects.filter(name="worker").values_list("updated_at", flat=True).first()
    oldest = notifications.filter(sent_at=None, failed_at=None, canary_slot=None).aggregate(value=Min("created_at"))["value"]
    canary = notifications.exclude(canary_slot=None).order_by("-canary_slot").first()
    failures = notifications.filter(failed_at__gte=now - timedelta(minutes=10)).count()

    def age(timestamp):
        return max(0, int((now - timestamp).total_seconds())) if timestamp else None

    worker_age, pending_age = age(worker_tick), age(oldest)
    collector = coverage_state(target, now) if target else "unknown"
    if target:
        enrollment = getattr(target, "runnerenrollment", None)
        if enrollment is None or enrollment.status == "pending":
            collector = "awaiting_enrollment"
    relay = None
    if canary:
        relay = "accepted" if canary.sent_at else "failed" if canary.failed_at else "queued"
    status = "healthy"
    if settings.DS_PILOT_RECOVERY_FENCED:
        status = "recovery_fenced"
    elif (workspace is None or not settings.EMAIL_HOST or not workspace.recipient
          or worker_age is None or worker_age >= 300
          or collector in ("unknown", "failed", "partial", "stale", "inactive")
          or (pending_age is not None and pending_age >= 600) or failures):
        status = "unhealthy"
    return {
        "status": status, "worker_age_seconds": worker_age, "collector_state": collector,
        "last_complete_age_seconds": age(target.last_success) if target else None,
        "oldest_pending_age_seconds": pending_age, "recent_terminal_failures": failures,
        "latest_canary_slot": canary.canary_slot if canary else None, "latest_canary_relay_state": relay,
    }
