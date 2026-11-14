from datetime import timedelta, timezone as utc_timezone
import logging
import uuid
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.mail import EmailMessage
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from infrastructure.domain import maintenance as maintain_infrastructure
from infrastructure.models import Finding, Target

from .domain import complete_scan, notify
from .github import CollectionError
from .models import Audit, Incident, LoginAttempt, Monitor, Notification, Scan, WorkerHealth, Workspace

logger = logging.getLogger(__name__)


def claim(queryset):
    with transaction.atomic():
        query = queryset.select_for_update(skip_locked=True) if connection.features.has_select_for_update_skip_locked else queryset
        row = query.filter(Q(lease_until__isnull=True) | Q(lease_until__lte=timezone.now())).first()
        if row is None:
            return None
        row.lease = uuid.uuid4()
        row.lease_until = timezone.now() + timedelta(minutes=5)
        row.save(update_fields=["lease", "lease_until"])
        return row


def scan_one(github):
    monitor = claim(Monitor.objects.filter(paused=False, next_scan__lte=timezone.now()).order_by("next_scan", "id"))
    if not monitor:
        return False
    try:
        snapshot = github.snapshot(monitor)
    except CollectionError as error:
        snapshot = None
        failure = error
    else:
        failure = None
    try:
        complete_scan(monitor.pk, monitor.lease, snapshot=snapshot, error=failure)
    except (Monitor.DoesNotExist, Workspace.DoesNotExist):
        logger.info("Monitor %s was removed during collection", monitor.pk)
    return True


def deliver_one():
    if not settings.EMAIL_HOST or (settings.DS_PILOT_EBS and settings.DS_PILOT_RECOVERY_FENCED):
        return False
    pending = Notification.objects.filter(sent_at__isnull=True, failed_at__isnull=True, next_attempt__lte=timezone.now())
    item = claim(pending.order_by("next_attempt", "id"))
    if not item:
        return False
    if not Notification.objects.filter(pk=item.pk, lease=item.lease, workspace__recipient=item.recipient).exists():
        Notification.objects.filter(pk=item.pk, lease=item.lease).delete()
        return True
    now = timezone.now()
    changes = {"attempts": item.attempts + 1, "lease": None, "lease_until": None}
    try:
        domain = urlsplit(settings.APP_ORIGIN).hostname
        message_id = f"<driftshield-{item.pk}-{int(item.created_at.timestamp() * 1_000_000)}@{domain}>"
        message = EmailMessage(item.subject, item.body, to=[item.recipient], headers={"Message-ID": message_id})
        if message.send() != 1:
            raise RuntimeError("Email backend did not accept the message.")
    except Exception:
        logger.warning("Email delivery failed for notification %s", item.pk)
        if item.attempts >= 7:
            changes["failed_at"] = now
        else:
            changes["next_attempt"] = now + timedelta(minutes=min(120, 2 ** item.attempts))
    else:
        changes["sent_at"] = now
    Notification.objects.filter(pk=item.pk, lease=item.lease).update(**changes)
    return True


def enqueue_mail_canary(now):
    utc_now = now.astimezone(utc_timezone.utc)
    slot = utc_now.replace(minute=(utc_now.minute // 15) * 15, second=0, microsecond=0)
    if not settings.EMAIL_HOST:
        return None
    workspace = Workspace.objects.filter(slug=settings.DS_PILOT_WORKSPACE).first()
    if workspace is None or not workspace.recipient:
        return None
    with transaction.atomic():
        workspace = Workspace.objects.select_for_update().get(pk=workspace.pk)
        if not workspace.recipient:
            return None
        notify(workspace, f"DriftShield mailbox probe {slot:%Y%m%dT%H%MZ}",
               "Scheduled DriftShield mailbox probe.", canary_slot=slot)
    return slot



def check_overdue():
    now = timezone.now()
    candidates = Monitor.objects.filter(paused=False, overdue_notified=False, last_error="").exclude(
        next_scan__gt=now - timedelta(minutes=5),
    ).values_list("pk", "workspace_id")[:100]
    for identifier, workspace_id in candidates:
        with transaction.atomic():
            workspace = Workspace.objects.select_for_update().filter(pk=workspace_id).first()
            if not workspace:
                continue
            monitor = Monitor.objects.select_for_update().filter(pk=identifier).first()
            if (not monitor or monitor.paused or monitor.overdue_notified or monitor.last_error
                    or (monitor.last_success and now - monitor.last_success < timedelta(minutes=2 * monitor.interval_minutes + 5))):
                continue
            monitor.overdue_notified = True
            monitor.save(update_fields=["overdue_notified"])
            notify(workspace, f"Observation overdue: {monitor.full_name}",
                   "DriftShield has no recent observation for this repository. Check the worker and GitHub access.")


def weekly_digests():
    now = timezone.now()
    iso_year, iso_week, _ = now.isocalendar()
    week = f"{iso_year}-W{iso_week:02}"
    workspaces = Workspace.objects.filter(digest_weekly=True).exclude(last_digest_week=week).exclude(recipient="")
    for identifier in workspaces.values_list("pk", flat=True)[:100]:
        with transaction.atomic():
            workspace = Workspace.objects.select_for_update().get(pk=identifier)
            if workspace.last_digest_week == week or not workspace.digest_weekly or not workspace.recipient:
                continue
            monitors = list(Monitor.objects.filter(workspace=workspace, paused=False))
            finding_count = Incident.objects.filter(monitor__in=monitors, closed_at__isnull=True).count()
            unavailable = sum(bool(monitor.last_error) for monitor in monitors)
            overdue = sum(
                not monitor.last_error and (
                    now - monitor.last_success > timedelta(minutes=2 * monitor.interval_minutes + 5)
                    if monitor.last_success else now - monitor.next_scan > timedelta(minutes=5)
                ) for monitor in monitors
            )
            targets = Target.objects.filter(workspace=workspace)
            infrastructure_findings = Finding.objects.filter(target__in=targets, closed_at__isnull=True, suppressed=False).count()
            notify(workspace, f"DriftShield weekly summary: {workspace.name}",
                   f"Active monitors: {len(monitors)}\nOpen findings: {finding_count}\n"
                   f"Unavailable: {unavailable}\nOverdue: {overdue}\n"
                   f"Infrastructure targets: {targets.count()}\nOpen infrastructure findings: {infrastructure_findings}\n"
                   f"Workspace: {settings.APP_ORIGIN}/w/{workspace.slug}/")
            workspace.last_digest_week = week
            workspace.save(update_fields=["last_digest_week"])


def prune():
    now = timezone.now()
    for workspace in Workspace.objects.iterator():
        cutoff = now - timedelta(days=workspace.retention_days)
        maintain_infrastructure(workspace, cutoff)
        Scan.objects.filter(monitor__workspace=workspace, collected_at__lt=cutoff).delete()
        Audit.objects.filter(workspace=workspace, created_at__lt=cutoff).delete()
        Incident.objects.filter(monitor__workspace=workspace, closed_at__lt=cutoff).delete()
        old = Notification.objects.filter(workspace=workspace, created_at__lt=cutoff)
        if Target.objects.filter(workspace=workspace, profile=Target.Profile.EBS_PILOT).exists():
            old = old.exclude(sent_at__isnull=True, failed_at__isnull=True)
        old.delete()
    LoginAttempt.objects.filter(window_start__lt=now - timedelta(days=1)).delete()
    Session.objects.filter(expire_date__lt=now).delete()


def heartbeat(name="worker"):
    WorkerHealth.objects.update_or_create(name=name, defaults={"updated_at": timezone.now()})
