import csv
from datetime import timedelta
from functools import wraps
import hashlib
import json
from ipaddress import ip_address
import unicodedata

import httpx
from cryptography.fernet import InvalidToken
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .domain import Conflict, INCIDENT_LABELS, approve, audit, notify
from .github import CollectionError, FIELDS, GitHub, cipher, repo_name, signing_key
from .models import Audit, Baseline, Incident, Integration, LoginAttempt, Membership, Monitor, Notification, Scan, WorkerHealth, Workspace


def login_client(request):
    peer = request.META.get("REMOTE_ADDR", "")
    if settings.DS_TRUST_PROXY_CLIENT_IP and peer in ("127.0.0.1", "::1"):
        try:
            return str(ip_address(request.headers.get("X-DriftShield-Client-IP", "")))
        except ValueError:
            pass
    return peer


class WorkspaceLogin(LoginView):
    template_name = "login.html"
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        username = unicodedata.normalize("NFKC", request.POST.get("username", "").strip()).casefold()[:150]
        ip = login_client(request)
        user_key = hashlib.sha256(("user:" + username).encode()).hexdigest()
        ip_key = hashlib.sha256(("ip:" + ip).encode()).hexdigest()
        limits = {user_key: 20, ip_key: 100}
        now = timezone.now()
        with transaction.atomic():
            attempts = []
            for key in sorted(limits):
                attempt, _ = LoginAttempt.objects.select_for_update().get_or_create(
                    key=key, defaults={"window_start": now},
                )
                if now - attempt.window_start >= timedelta(minutes=10):
                    attempt.window_start = now
                    attempt.attempts = 0
                if attempt.attempts >= limits[key]:
                    return render(request, self.template_name, {"form": self.get_form(), "throttled": True}, status=429)
                attempts.append(attempt)
            for attempt in attempts:
                attempt.attempts += 1
                attempt.save()
        return super().post(request, *args, **kwargs)


@login_required
def index(request):
    memberships = request.user.membership_set.select_related("workspace").order_by("workspace__name")
    if memberships.count() == 1:
        return redirect("overview", slug=memberships[0].workspace.slug)
    return render(request, "index.html", {"memberships": memberships})


@require_POST
def sign_out(request):
    logout(request)
    return redirect("login")


def member_view(*roles):
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(request, slug, *args, **kwargs):
            membership = get_object_or_404(Membership.objects.select_related("workspace"), workspace__slug=slug, user=request.user)
            if roles and membership.role not in roles:
                return HttpResponse("This action requires a different workspace role.", status=403)
            return view(request, membership, *args, **kwargs)
        return wrapped
    return decorator


def locked_member(member, *roles):
    """Acquire workspace then membership locks before an authorized mutation."""
    workspace = get_object_or_404(Workspace.objects.select_for_update(), pk=member.workspace_id)
    current = Membership.objects.select_for_update().filter(pk=member.pk, workspace=workspace, user_id=member.user_id).first()
    if not current or current.role not in roles:
        raise PermissionDenied
    return workspace


def state(monitor, now):
    if monitor.paused:
        return "Paused", "muted"
    if monitor.last_error:
        return "Unavailable", "danger"
    if not monitor.last_success:
        return ("Overdue", "warning") if now - monitor.next_scan > timedelta(minutes=5) else ("Awaiting scan", "muted")
    if now - monitor.last_success > timedelta(minutes=2 * monitor.interval_minutes + 5):
        return "Overdue", "warning"
    if monitor.open_count:
        return "Findings open", "danger"
    if not monitor.baseline_version:
        return "Needs baseline", "warning"
    return "Matches baseline", "good"


@member_view()
def overview(request, member):
    workspace = member.workspace
    monitors = list(Monitor.objects.filter(workspace=workspace).annotate(
        open_count=Count("incident", filter=Q(incident__closed_at__isnull=True)),
    ).order_by("full_name"))
    now = timezone.now()
    for monitor in monitors:
        monitor.state, monitor.tone = state(monitor, now)
    recent = list(Audit.objects.filter(workspace=workspace).select_related("monitor").order_by("-created_at")[:6])
    for entry in recent:
        entry.label = entry.action.replace("_", " ").capitalize()
    return render(request, "overview.html", {
        "workspace": workspace, "member": member, "monitors": monitors,
        "integrated": Integration.objects.filter(workspace=workspace).exists(),
        "incident_count": sum(monitor.open_count for monitor in monitors),
        "attention_count": sum(monitor.tone in ("danger", "warning") for monitor in monitors),
        "fresh_count": sum(monitor.tone == "good" for monitor in monitors),
        "recent": recent,
    })


@member_view("owner", "operator")
@require_POST
def add_public(request, member):
    workspace = member.workspace
    try:
        name = repo_name(request.POST.get("repository", "").strip())
        with transaction.atomic():
            workspace = locked_member(member, "owner", "operator")
            if Monitor.objects.filter(workspace=workspace).count() >= workspace.repo_limit:
                raise Conflict("Workspace repository limit reached.")
            monitor = Monitor.objects.create(workspace=workspace, full_name=name, interval_minutes=60)
            audit(workspace, request.user.username, "monitor_added", monitor)
    except (CollectionError, Conflict) as error:
        messages.error(request, str(error))
    except IntegrityError:
        messages.error(request, "This repository is already monitored.")
    else:
        return redirect("monitor", slug=workspace.slug, pk=monitor.pk)
    return redirect("overview", slug=workspace.slug)


@member_view("owner")
@require_POST
def import_repos(request, member):
    workspace = member.workspace
    try:
        integration = Integration.objects.get(workspace=workspace)
        pem = cipher().decrypt(bytes(integration.encrypted_key))
        with httpx.Client(timeout=8, follow_redirects=False, trust_env=False) as client:
            github = GitHub(client)
            token = github.token(integration.app_id, signing_key(pem), integration.installation_id)
            records = github.inventory(token)
        with transaction.atomic():
            workspace = locked_member(member, "owner")
            current = Integration.objects.get(workspace=workspace)
            if current.pk != integration.pk or bytes(current.encrypted_key) != bytes(integration.encrypted_key):
                raise Conflict("GitHub installation changed during import. Try again.")
            monitors = list(Monitor.objects.select_for_update().filter(workspace=workspace).order_by("pk"))
            ids = {monitor.github_id: monitor for monitor in monitors if monitor.github_id}
            names = {monitor.full_name: monitor for monitor in monitors}
            total, added, linked = len(monitors), 0, 0
            for record in sorted(records, key=lambda item: str(item.get("full_name", ""))):
                name = repo_name(record.get("full_name"))
                identifier = record.get("id")
                if type(identifier) is not int or not 0 < identifier < 2**63:
                    raise CollectionError("GitHub returned an invalid repository identity.")
                existing = ids.get(identifier) or names.get(name)
                if existing:
                    if existing.github_id and existing.github_id != identifier:
                        continue
                    if not existing.app_managed:
                        existing.github_id, existing.app_managed = identifier, True
                        existing.lease = existing.lease_until = None
                        existing.next_scan = timezone.now()
                        existing.save()
                        linked += 1
                    continue
                if total >= workspace.repo_limit:
                    continue
                Monitor.objects.create(workspace=workspace, full_name=name, github_id=identifier, app_managed=True)
                total += 1
                added += 1
            audit(workspace, request.user.username, "repositories_imported", added=added, linked=linked)
        messages.success(request, f"Imported {added} repositories and linked {linked} existing monitors. Workspace capacity: {total}/{workspace.repo_limit}.")
    except (CollectionError, Conflict, Integration.DoesNotExist, IntegrityError):
        messages.error(request, "Import failed. Check the GitHub App installation, repository access, and workspace settings.")
    except InvalidToken:
        messages.error(request, "The GitHub App key could not be decrypted.")
    return redirect("overview", slug=workspace.slug)


def format_value(value):
    if isinstance(value, list):
        return json.dumps(value, indent=2, ensure_ascii=False)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return value if value is not None else "—"


@member_view()
def monitor_detail(request, member, pk):
    workspace = member.workspace
    with transaction.atomic():
        monitor = get_object_or_404(Monitor.objects.select_for_update(), workspace=workspace, pk=pk)
        baseline = Baseline.objects.filter(monitor=monitor, version=monitor.baseline_version).first()
        expected = baseline.values if baseline else {}
        incidents = list(Incident.objects.filter(monitor=monitor).order_by("-first_seen")[:50])
        scans = list(Scan.objects.filter(monitor=monitor).order_by("-collected_at")[:15])
        versions = list(Baseline.objects.filter(monitor=monitor).order_by("-version")[:15])
        monitor.open_count = Incident.objects.filter(monitor=monitor, closed_at__isnull=True).count()
    for incident in incidents:
        incident.label = INCIDENT_LABELS.get(incident.field, incident.field)
    for scan in scans:
        scan.label = scan.status.replace("_", " ").capitalize()
    for version in versions:
        version.formatted = json.dumps(version.values, indent=2, ensure_ascii=False)
    monitor.state, monitor.tone = state(monitor, timezone.now())
    rows = [
        {"field": field, "label": label, "expected": format_value(expected.get(field)),
         "observed": format_value(monitor.latest.get(field)), "changed": field in monitor.latest and expected.get(field) != monitor.latest[field]}
        for field, label in FIELDS.items()
    ]
    return render(request, "monitor.html", {
        "workspace": workspace, "member": member, "monitor": monitor, "rows": rows,
        "incidents": incidents, "scans": scans, "versions": versions,
        "can_approve": bool(monitor.latest) and monitor.tone not in ("muted",) and monitor.state in ("Needs baseline", "Findings open"),
    })


@member_view("owner", "operator")
@require_POST
def monitor_action(request, member, pk):
    workspace = member.workspace
    action = request.POST.get("action")
    if action == "approve":
        try:
            with transaction.atomic():
                workspace = locked_member(member, "owner", "operator")
                approve(workspace, pk, request.user.username, int(request.POST.get("revision", "")),
                        int(request.POST.get("version", "")), request.POST.getlist("field"), request.POST.get("reason", ""))
        except Monitor.DoesNotExist as error:
            raise Http404 from error
        except Conflict as error:
            messages.error(request, str(error))
        except ValueError:
            messages.error(request, "Invalid observation or baseline version.")
        else:
            messages.success(request, "Baseline version recorded.")
    else:
        with transaction.atomic():
            workspace = locked_member(member, "owner", "operator")
            monitor = get_object_or_404(Monitor.objects.select_for_update(), pk=pk, workspace=workspace)
            if action == "scan":
                if not monitor.paused and monitor.next_scan > timezone.now() and (
                    not monitor.last_attempt or timezone.now() - monitor.last_attempt >= timedelta(minutes=2)
                ) and not monitor.last_error:
                    monitor.next_scan = timezone.now()
                    monitor.save(update_fields=["next_scan"])
                    messages.success(request, "Scan queued.")
                elif monitor.last_error:
                    messages.info(request, "The worker will retry at the scheduled time.")
                else:
                    messages.info(request, "A scan is queued, was recently attempted, or monitoring is paused.")
            elif action in ("pause", "resume"):
                monitor.paused = action == "pause"
                monitor.lease = monitor.lease_until = None
                if not monitor.paused:
                    monitor.next_scan = timezone.now()
                monitor.save(update_fields=["paused", "lease", "lease_until", "next_scan"])
                audit(workspace, request.user.username, action, monitor)
            elif action == "ack":
                try:
                    incident_id = int(request.POST.get("incident", ""))
                    if not 0 < incident_id < 2**63:
                        raise ValueError
                except ValueError:
                    return HttpResponse("Invalid incident.", status=400)
                incident = get_object_or_404(Incident, pk=incident_id, monitor=monitor)
                if incident.closed_at is None and incident.acknowledged_at is None:
                    incident.acknowledged_at = timezone.now()
                    incident.save(update_fields=["acknowledged_at"])
                    audit(workspace, request.user.username, "incident_acknowledged", monitor, incident=incident.pk)
            else:
                return HttpResponse("Unknown monitor action.", status=400)
    return redirect("monitor", slug=workspace.slug, pk=pk)


@member_view("owner")
@require_POST
def remove_monitor(request, member, pk):
    workspace = member.workspace
    with transaction.atomic():
        workspace = locked_member(member, "owner")
        monitor = get_object_or_404(Monitor.objects.select_for_update(), pk=pk, workspace=workspace)
        if request.POST.get("confirm") != monitor.full_name:
            messages.error(request, "Enter the exact repository name to remove the monitor.")
            return redirect("monitor", slug=workspace.slug, pk=pk)
        name = monitor.full_name
        monitor.delete()
        audit(workspace, request.user.username, "monitor_removed", repository=name)
    messages.success(request, f"Removed {name}.")
    return redirect("overview", slug=workspace.slug)


@member_view("owner")
def settings_view(request, member):
    workspace = member.workspace
    if request.method == "POST":
        try:
            recipient = request.POST.get("recipient", "").strip()
            if recipient:
                validate_email(recipient)
            days = int(request.POST.get("retention", ""))
            reviews = int(request.POST.get("reviews", "0"))
            if not 7 <= days <= 365 or not 0 <= reviews <= 10 or len(recipient) > 254:
                raise ValueError
        except (ValidationError, ValueError):
            messages.error(request, "Enter a valid email, a retention period from 7 to 365 days, and 0–10 required approvals.")
        else:
            with transaction.atomic():
                workspace = locked_member(member, "owner")
                if workspace.recipient != recipient:
                    Notification.objects.filter(workspace=workspace, sent_at__isnull=True, failed_at__isnull=True).delete()
                workspace.recipient, workspace.retention_days = recipient, days
                workspace.require_private = request.POST.get("private") == "1"
                workspace.require_signatures = request.POST.get("signatures") == "1"
                workspace.min_reviews = reviews
                workspace.digest_weekly = request.POST.get("digest") == "1"
                workspace.infra_high_priority_only = request.POST.get("infra_high_only") == "1"
                workspace.save(update_fields=["recipient", "retention_days", "require_private", "require_signatures", "min_reviews", "digest_weekly", "infra_high_priority_only"])
                audit(workspace, request.user.username, "workspace_settings_updated", retention=days, email_enabled=bool(recipient), min_reviews=reviews,
                      require_private=workspace.require_private, require_signatures=workspace.require_signatures,
                      digest_weekly=workspace.digest_weekly, infra_high_priority_only=workspace.infra_high_priority_only)
                if request.POST.get("test") and recipient and settings.EMAIL_HOST:
                    recent_test = Audit.objects.filter(workspace=workspace, action="test_email_queued",
                                                       created_at__gte=timezone.now() - timedelta(minutes=10)).exists()
                    if recent_test:
                        messages.warning(request, "A test email was already queued in the last ten minutes.")
                    else:
                        notify(workspace, "DriftShield test notification", "Your workspace notification route is working.")
                        audit(workspace, request.user.username, "test_email_queued")
            messages.success(request, "Workspace settings saved.")
            return redirect("settings", slug=workspace.slug)
    return render(request, "settings.html", {
        "workspace": workspace, "member": member,
        "integration": Integration.objects.filter(workspace=workspace).first(),
        "smtp": bool(settings.EMAIL_HOST),
        "deliveries": Notification.objects.filter(workspace=workspace).order_by("-created_at")[:15],
        "members": Membership.objects.filter(workspace=workspace).select_related("user").order_by("user__username"),
    })


def csv_value(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value


@member_view()
def audit_log(request, member):
    entries = Audit.objects.filter(workspace=member.workspace).select_related("monitor").order_by("-created_at", "-pk")[:100]
    for entry in entries:
        entry.label = entry.action.replace("_", " ").capitalize()
        entry.formatted = json.dumps(entry.detail, indent=2, ensure_ascii=False)
    return render(request, "audit.html", {"workspace": member.workspace, "member": member, "entries": entries})


class CsvLine:
    def write(self, value):
        return value


def csv_download(filename, headers, rows):
    def stream():
        writer = csv.writer(CsvLine())
        yield writer.writerow(headers)
        for row in rows:
            yield writer.writerow([csv_value(value) for value in row])

    response = StreamingHttpResponse(stream(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-DriftShield-Export-Limit"] = "10000"
    return response


@member_view()
def evidence(request, member):
    queryset = Incident.objects.filter(monitor__workspace=member.workspace).select_related("monitor").order_by("-first_seen", "-pk")
    rows = (
        (item.monitor.full_name, item.field, item.severity, json.dumps(item.expected), json.dumps(item.observed),
         item.baseline_version, item.first_seen.isoformat(), item.last_seen.isoformat(), item.occurrences,
         item.acknowledged_at.isoformat() if item.acknowledged_at else "",
         item.closed_at.isoformat() if item.closed_at else "", item.resolution)
        for item in queryset[:10000].iterator(chunk_size=100)
    )
    return csv_download("driftshield-evidence.csv",
                        ["Repository", "Field", "Severity", "Expected", "Observed", "Baseline", "First seen",
                         "Last seen", "Occurrences", "Acknowledged at", "Closed at", "Resolution"], rows)


@never_cache
def live(request):
    return JsonResponse({"status": "ok"})


@never_cache
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


@member_view("owner")
def worker_health(request, member):
    thresholds = {"worker": 5, "email": 5, "github": 5, "freshness": 5, "retention": 90}
    updated = dict(WorkerHealth.objects.filter(name__in=thresholds).values_list("name", "updated_at"))
    now = timezone.now()
    stages = {
        name: {"last_successful_tick": updated.get(name),
               "healthy": name in updated and now - updated[name] < timedelta(minutes=minutes)}
        for name, minutes in thresholds.items()
    }
    healthy = all(stage["healthy"] for stage in stages.values())
    return JsonResponse({"worker": "healthy" if healthy else "overdue", "last_heartbeat": updated.get("worker"),
                         "stages": stages}, status=200 if healthy else 503)
