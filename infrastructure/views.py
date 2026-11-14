from datetime import timedelta
import hmac
import json
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import RequestDataTooBig, ValidationError
from django.core.validators import URLValidator
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from collector.contract import IDENTIFIER, MAX_BODY, PLATFORMS, decode, envelope
from governance.domain import audit
from governance.models import Membership, Workspace
from governance.views import csv_download, locked_member, member_view
from .domain import Conflict, RateLimited, accept, approve, digest, fresh, issue_token, refresh_exceptions, same, status
from .models import Delivery, DriftException, EvidenceEvent, Finding, Target
from .triage import allowed_exception, assign, except_field, record, resource_changes

TOKEN = re.compile(r"Bearer ([A-Za-z0-9_-]{43})\Z")
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
GUIDANCE = {
    "aws": "Review the change with the resource owner. Use your approved infrastructure workflow to restore the expected configuration, then run the collector again. For unexpected resources, verify ownership and dependencies before changing anything.",
    "kubernetes": "Review the field against the source manifest and any controller that manages it. Restore the approved setting through your normal GitOps or deployment workflow, then run the collector again.",
    "docker": "Compare with the approved Compose or service definition. Redeploy through your usual change process, then run the collector again.",
    "terraform": "Review the plan in your existing Terraform or OpenTofu pipeline. Use your normal review process to reconcile the actual infrastructure or intentionally reconcile the source of truth, then generate a new refresh-only observation.",
    "snapshot": "Use the target's approved change procedure to restore the expected configuration, then collect a new snapshot.",
}


def token_valid(stored, expiry, candidate, now):
    return bool(stored and expiry and now < expiry and hmac.compare_digest(candidate, stored))


def token_response(request, member, target, token):
    path = reverse("infra_ingest", args=[target.pk])
    return render(request, "infra_token.html", {
        "workspace": member.workspace, "member": member, "target": target, "token": token, "endpoint": settings.APP_ORIGIN + path,
    })


@member_view()
def overview(request, member):
    targets = list(Target.objects.filter(workspace=member.workspace).annotate(
        open_count=Count("finding", filter=Q(finding__closed_at__isnull=True, finding__suppressed=False)),
        suppressed_count=Count("finding", filter=Q(finding__closed_at__isnull=True, finding__suppressed=True)),
    ).order_by("name"))
    for target in targets:
        target.label, target.tone = status(target, timezone.now())
    return render(request, "infra_overview.html", {
        "workspace": member.workspace, "member": member, "targets": targets, "platforms": Target.PLATFORMS,
    })


@member_view("owner")
@require_POST
def add(request, member):
    name, platform, scope = (request.POST.get(field, "").strip() for field in ("name", "platform", "scope"))
    try:
        interval = int(request.POST.get("interval", "30"))
        if (not SLUG.fullmatch(name) or platform not in PLATFORMS or len(scope) > 160
                or not IDENTIFIER.fullmatch(scope) or not 5 <= interval <= 1440):
            raise ValueError
        if platform == "aws" and not re.fullmatch(r"[0-9]{12}:[a-z]{2}(?:-[a-z0-9]+)+-\d+", scope):
            raise ValueError
    except ValueError:
        messages.error(request, "Check the name, platform, scope and scan interval. AWS scope must be ACCOUNT_ID:REGION.")
        return redirect("infra", slug=member.workspace.slug)
    token, token_hash = issue_token()
    try:
        with transaction.atomic():
            workspace = locked_member(member, "owner")
            if Target.objects.filter(workspace=workspace).count() >= 100:
                raise Conflict("Workspace limit of 100 infrastructure targets reached.")
            target = Target.objects.create(workspace=workspace, name=name, platform=platform, scope=scope,
                                           interval=interval, token_hash=token_hash,
                                           token_expires_at=timezone.now() + timedelta(days=90))
            audit(workspace, request.user.username, "infra_target_added", target=name, platform=platform)
    except (IntegrityError, Conflict):
        messages.error(request, "Target limit reached, or the name or platform scope is already in use.")
        return redirect("infra", slug=member.workspace.slug)
    return token_response(request, member, target, token)


@csrf_exempt
@require_POST
def ingest(request, pk):
    token = TOKEN.fullmatch(request.headers.get("Authorization", ""))
    if not token:
        return JsonResponse({"error": "Invalid collector credential."}, status=401)
    candidate = digest(token[1])
    initial = Target.objects.filter(pk=pk).values("workspace_id", "token_hash", "token_expires_at").first()
    if not initial or not token_valid(initial["token_hash"], initial["token_expires_at"], candidate, timezone.now()):
        return JsonResponse({"error": "Invalid collector credential."}, status=401)
    if request.content_type != "application/json":
        return JsonResponse({"error": "Expected application/json."}, status=415)
    try:
        length = int(request.META.get("CONTENT_LENGTH", "0"))
        if not 0 < length <= MAX_BODY:
            raise RequestDataTooBig
        raw = request.read(MAX_BODY + 1)
        if len(raw) != length:
            raise ValueError("Incomplete body.")
        data = decode(raw)
        now = timezone.now()
        delivery, captured = envelope(data, now)
    except RequestDataTooBig:
        return JsonResponse({"error": "Missing or oversized observation."}, status=413)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return JsonResponse({"error": "Invalid or expired observation."}, status=422)
    with transaction.atomic():
        workspace = Workspace.objects.select_for_update().filter(pk=initial["workspace_id"]).first()
        target = Target.objects.select_for_update().filter(pk=pk, workspace=workspace).first() if workspace else None
        if not target or not token_valid(target.token_hash, target.token_expires_at, candidate, timezone.now()):
            return JsonResponse({"error": "Invalid collector credential."}, status=401)
        if data["platform"] != target.platform or data["scope"] != target.scope:
            return JsonResponse({"error": "Observation scope does not match target."}, status=422)
        try:
            result = accept(target, delivery, captured, data, now)
        except Conflict as error:
            return JsonResponse({"error": str(error)}, status=409)
        except RateLimited:
            return JsonResponse({"error": "Target delivery rate exceeded."}, status=429,
                                headers={"Retry-After": str(settings.DS_INGEST_MIN_SECONDS)})
    return JsonResponse({"status": result})


@member_view()
def detail(request, member, pk):
    with transaction.atomic():
        target = get_object_or_404(Target.objects.select_for_update(), workspace=member.workspace, pk=pk)
        active = list(Finding.objects.filter(target=target, closed_at__isnull=True).select_related("owner").order_by("suppressed", "resource"))
        recent = list(Finding.objects.filter(target=target, closed_at__isnull=False).order_by("-closed_at")[:20])
        try:
            selected = int(request.GET.get("finding", "0"))
        except ValueError:
            selected = 0
        if 0 < selected < 2**63 and all(item.pk != selected for item in recent):
            older = Finding.objects.filter(target=target, closed_at__isnull=False, pk=selected).first()
            if older:
                recent.insert(0, older)
        target.open_count = sum(not finding.suppressed for finding in active)
        target.suppressed_count = len(active) - target.open_count
        observations = list(Delivery.objects.filter(target=target).order_by("-received_at")[:12])
        versions = list(target.baseline_set.order_by("-version")[:12])
        exceptions = list(DriftException.objects.filter(
            target=target, ended_at__isnull=True, expires_at__gt=timezone.now(),
        ).order_by("expires_at"))
        events = list(EvidenceEvent.objects.filter(target=target).order_by("-created_at", "-pk")[:30])
    operators = list(Membership.objects.filter(
        workspace=member.workspace, role__in=("owner", "operator"), user__is_active=True,
    ).select_related("user").order_by("user__username"))
    target.label, target.tone = status(target, timezone.now())
    for finding in active:
        finding.expected_text = json.dumps(finding.expected, indent=2) if finding.expected is not None else "Absent"
        finding.actual_text = json.dumps(finding.actual, indent=2) if finding.actual is not None else "Absent"
        finding.changes = resource_changes(finding.expected, finding.actual, same)
        finding.observation_changed = not same(target.latest.get(finding.resource), finding.actual)
        finding.exception_fields = [
            change["field"] for change in finding.changes
            if target.platform != "terraform" and allowed_exception(change)
        ]
        if finding.owner_id and all(operator.user_id != finding.owner_id for operator in operators):
            finding.owner = None
    for event in events:
        event.label = event.kind.replace("_", " ").capitalize()
        event.formatted = json.dumps(event.detail, indent=2, ensure_ascii=False)
    return render(request, "infra_detail.html", {
        "workspace": member.workspace, "member": member, "target": target, "active": active, "recent": recent,
        "observations": observations, "versions": versions, "fresh": fresh(target, timezone.now()),
        "snapshot_text": json.dumps(target.latest, indent=2, sort_keys=True),
        "can_initial": fresh(target, timezone.now()) and not target.version and (target.platform != "terraform" or not target.latest), "guidance": GUIDANCE[target.platform],
        "endpoint": settings.APP_ORIGIN + reverse("infra_ingest", args=[pk]),
        "operators": operators, "exceptions": exceptions, "events": events,
    })


@member_view("owner", "operator")
@require_POST
def action(request, member, pk):
    actor, command = request.user.username, request.POST.get("action")
    try:
        with transaction.atomic():
            required_roles = ("owner",) if command in ("rotate", "revoke", "remove", "runbook", "except", "end_exception") else ("owner", "operator")
            workspace = locked_member(member, *required_roles)
            target = get_object_or_404(Target.objects.select_for_update(), workspace=workspace, pk=pk)
            if command == "rotate":
                token, target.token_hash = issue_token()
                target.token_expires_at = timezone.now() + timedelta(days=90)
                # Require evidence collected with the new credential before permitting review.
                target.last_error = "credential_rotated"
                target.overdue_alerted = False
                target.save(update_fields=["token_hash", "token_expires_at", "last_error", "overdue_alerted"])
                audit(workspace, actor, "infra_token_rotated", target=target.name)
                return token_response(request, member, target, token)
            if command == "revoke":
                target.token_hash = ""
                target.token_expires_at = None
                target.overdue_alerted = True
                target.save(update_fields=["token_hash", "token_expires_at", "overdue_alerted"])
                audit(workspace, actor, "infra_token_revoked", target=target.name)
                messages.success(request, "Collector credential revoked. Rotate to reconnect.")
                return redirect("infra_detail", slug=workspace.slug, pk=pk)
            if command == "remove":
                if request.POST.get("confirm") != target.name:
                    raise Conflict("Enter the exact target name to remove it.")
                audit(workspace, actor, "infra_target_removed", target=target.name)
                target.delete()
                return redirect("infra", slug=workspace.slug)
            if command in ("approve", "review", "except") and (
                int(request.POST.get("revision", "")) != target.revision or int(request.POST.get("version", "")) != target.version
            ):
                raise Conflict("The observation or baseline changed. Refresh the page.")
            if command == "approve":
                approve(target, int(request.POST.get("revision", "")), int(request.POST.get("version", "")),
                        [int(pk) for pk in request.POST.getlist("finding")], actor, request.POST.get("reason", ""))
            elif command in ("review", "assign", "except"):
                finding_id = int(request.POST.get("finding", ""))
                if not 0 < finding_id < 2**63:
                    raise ValueError
                finding = get_object_or_404(Finding, target=target, closed_at__isnull=True, pk=finding_id)
                reason = request.POST.get("reason", "").strip()
                if command == "assign":
                    owner_id = int(request.POST.get("owner") or "0")
                    if not 0 <= owner_id < 2**63:
                        raise ValueError
                    assign(target, finding, actor, owner_id or None)
                elif command == "except":
                    if not fresh(target, timezone.now()) or not same(target.latest.get(finding.resource), finding.actual):
                        raise Conflict("Collect a fresh observation of this finding before creating an exception.")
                    except_field(target, finding, actor, request.POST.get("field", ""),
                                 int(request.POST.get("hours", "")), reason)
                    refresh_exceptions(target, timezone.now())
                else:
                    if (finding.suppressed or not fresh(target, timezone.now()) or not reason or len(reason) > 240
                            or not same(target.latest.get(finding.resource), finding.actual)):
                        raise Conflict("A current, actionable observation and a change ticket or review note are required.")
                    finding.review_at, finding.reviewer, finding.reason = timezone.now(), actor, reason
                    finding.review_revision = target.revision
                    finding.save(update_fields=["review_at", "reviewer", "reason", "review_revision"])
                    record(target, "reviewed", actor, finding, reason=reason, revision=target.revision)
                    audit(workspace, actor, "infra_remediation_reviewed", target=target.name, resource=finding.resource, reason=reason)
            elif command == "end_exception":
                exception_id = int(request.POST.get("exception", ""))
                if not 0 < exception_id < 2**63:
                    raise ValueError
                item = get_object_or_404(DriftException, target=target, ended_at__isnull=True, pk=exception_id)
                item.ended_at = timezone.now()
                item.save(update_fields=["ended_at"])
                record(target, "exception_ended", actor, field=item.field, resource_id=item.resource, exception=item.pk)
                audit(workspace, actor, "infra_exception_ended", target=target.name, resource=item.resource,
                      field=item.field, exception=item.pk)
                refresh_exceptions(target, item.ended_at)
            elif command == "runbook":
                url = request.POST.get("url", "").strip()
                if url:
                    try:
                        URLValidator(schemes=["https"])(url)
                        parts = urlsplit(url)
                        if len(url) > 500 or parts.username is not None or parts.password is not None or parts.query or parts.fragment:
                            raise ValueError
                    except (ValidationError, ValueError) as error:
                        raise Conflict("Use an HTTPS runbook link without credentials, query parameters, or a fragment.") from error
                target.runbook_url = url
                target.save(update_fields=["runbook_url"])
                record(target, "runbook_updated", actor, configured=bool(url))
                audit(workspace, actor, "infra_runbook_updated", target=target.name, configured=bool(url))
            else:
                return HttpResponse("Unknown or unauthorized action.", status=403)
    except (Conflict, ValueError, OverflowError) as error:
        messages.error(request, str(error) if isinstance(error, Conflict) else "Invalid form values.")
    else:
        messages.success(request, "Change recorded.")
    return redirect("infra_detail", slug=member.workspace.slug, pk=pk)


def filtered_findings(request, member, default):
    state = request.GET.get("state", default)
    priority = request.GET.get("priority", "")
    if state not in ("open", "excepted", "closed", "all"):
        state = default
    if priority not in ("high", "medium"):
        priority = ""
    queryset = Finding.objects.filter(target__workspace=member.workspace).select_related("target", "owner")
    if state == "open":
        queryset = queryset.filter(closed_at__isnull=True, suppressed=False)
    elif state == "excepted":
        queryset = queryset.filter(closed_at__isnull=True, suppressed=True)
    elif state == "closed":
        queryset = queryset.filter(closed_at__isnull=False)
    if priority:
        queryset = queryset.filter(priority=priority)
    mine = request.GET.get("mine") == "1" and member.role in ("owner", "operator")
    if mine:
        queryset = queryset.filter(owner_id=member.user_id)
    return queryset.order_by("-first_seen", "-pk"), {"state": state, "priority": priority, "mine": mine}


@member_view()
def inbox(request, member):
    findings, filters = filtered_findings(request, member, "open")
    page = Paginator(findings, 50).get_page(request.GET.get("page"))
    operators = set(Membership.objects.filter(
        workspace=member.workspace, role__in=("owner", "operator"), user__is_active=True,
    ).values_list("user_id", flat=True))
    for item in page:
        if item.closed_at is None and item.owner_id not in operators:
            item.owner = None
    return render(request, "infra_inbox.html", {
        "workspace": member.workspace, "member": member, "filters": filters, "page": page,
    })


@member_view()
def evidence(request, member):
    queryset, _ = filtered_findings(request, member, "all")
    rows = (
        (row.target.name, row.target.platform, row.target.scope, row.resource, row.priority,
         json.dumps(row.expected), json.dumps(row.actual), row.first_seen.isoformat(), row.last_seen.isoformat(),
         row.count, row.reviewer, row.reason, row.closed_at.isoformat() if row.closed_at else "", row.resolution,
         row.owner.username if row.owner else "", row.baseline_version if row.baseline_version is not None else "",
         row.review_revision if row.review_revision is not None else "", "yes" if row.suppressed else "no")
        for row in queryset[:10000].iterator(chunk_size=100)
    )
    return csv_download("driftshield-infrastructure.csv",
                        ["Target", "Platform", "Scope", "Resource", "Priority", "Expected", "Observed", "First seen",
                         "Last seen", "Observations", "Reviewer", "Change reference", "Closed", "Resolution",
                         "Assignee", "Opening baseline version", "Reviewed revision", "Excepted"], rows)


@member_view()
def timeline_export(request, member, pk):
    target = get_object_or_404(Target, workspace=member.workspace, pk=pk)
    rows = (
        (target.name, event.created_at.isoformat(), event.kind, event.actor,
         event.resource or event.detail.get("resource_id", ""), event.finding_id or "", json.dumps(event.detail))
        for event in EvidenceEvent.objects.filter(target=target).order_by("-created_at", "-pk")[:10000].iterator(chunk_size=100)
    )
    return csv_download("driftshield-timeline.csv", ["Target", "Time", "Event", "Actor", "Resource", "Finding", "Details"], rows)
