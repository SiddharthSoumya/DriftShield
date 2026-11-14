from datetime import timedelta
import hmac
import json
import re

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import RequestDataTooBig
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
from governance.models import Workspace
from governance.views import csv_download, locked_member, member_view
from .domain import Conflict, RateLimited, accept, approve, digest, fresh, issue_token, status
from .models import Delivery, Finding, Target

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
        open_count=Count("finding", filter=Q(finding__closed_at__isnull=True)),
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
        active = list(Finding.objects.filter(target=target, closed_at__isnull=True).order_by("resource"))
        recent = list(Finding.objects.filter(target=target, closed_at__isnull=False).order_by("-closed_at")[:20])
        target.open_count = len(active)
        observations = list(Delivery.objects.filter(target=target).order_by("-received_at")[:12])
        versions = list(target.baseline_set.order_by("-version")[:12])
    target.label, target.tone = status(target, timezone.now())
    for finding in active:
        finding.expected_text = json.dumps(finding.expected, indent=2) if finding.expected is not None else "Absent"
        finding.actual_text = json.dumps(finding.actual, indent=2) if finding.actual is not None else "Absent"
    return render(request, "infra_detail.html", {
        "workspace": member.workspace, "member": member, "target": target, "active": active, "recent": recent,
        "observations": observations, "versions": versions, "fresh": fresh(target, timezone.now()),
        "snapshot_text": json.dumps(target.latest, indent=2, sort_keys=True),
        "can_initial": fresh(target, timezone.now()) and not target.version and (target.platform != "terraform" or not target.latest), "guidance": GUIDANCE[target.platform],
        "endpoint": settings.APP_ORIGIN + reverse("infra_ingest", args=[pk]),
    })


@member_view("owner", "operator")
@require_POST
def action(request, member, pk):
    actor, command = request.user.username, request.POST.get("action")
    try:
        with transaction.atomic():
            required_roles = ("owner",) if command in ("rotate", "revoke", "remove") else ("owner", "operator")
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
            if command in ("approve", "review") and (
                int(request.POST.get("revision", "")) != target.revision or int(request.POST.get("version", "")) != target.version
            ):
                raise Conflict("The observation or baseline changed. Refresh the page.")
            if command == "approve":
                approve(target, int(request.POST.get("revision", "")), int(request.POST.get("version", "")),
                        [int(pk) for pk in request.POST.getlist("finding")], actor, request.POST.get("reason", ""))
            elif command == "review":
                finding_id = int(request.POST.get("finding", ""))
                if not 0 < finding_id < 2**63:
                    raise ValueError
                finding = get_object_or_404(Finding, target=target, closed_at__isnull=True, pk=finding_id)
                reason = request.POST.get("reason", "").strip()
                if not fresh(target, timezone.now()) or not reason or len(reason) > 240:
                    raise Conflict("A fresh observation and a change ticket or review note are required.")
                finding.review_at, finding.reviewer, finding.reason = timezone.now(), actor, reason
                finding.save(update_fields=["review_at", "reviewer", "reason"])
                audit(workspace, actor, "infra_remediation_reviewed", target=target.name, resource=finding.resource, reason=reason)
            else:
                return HttpResponse("Unknown or unauthorized action.", status=403)
    except (Conflict, ValueError, OverflowError) as error:
        messages.error(request, str(error) if isinstance(error, Conflict) else "Invalid form values.")
    else:
        messages.success(request, "Change recorded.")
    return redirect("infra_detail", slug=member.workspace.slug, pk=pk)


@member_view()
def evidence(request, member):
    queryset = Finding.objects.filter(target__workspace=member.workspace).select_related("target").order_by("-first_seen", "-pk")
    rows = (
        (row.target.name, row.target.platform, row.target.scope, row.resource, row.priority,
         json.dumps(row.expected), json.dumps(row.actual), row.first_seen.isoformat(), row.last_seen.isoformat(),
         row.count, row.reviewer, row.reason, row.closed_at.isoformat() if row.closed_at else "", row.resolution)
        for row in queryset[:10000].iterator(chunk_size=100)
    )
    return csv_download("driftshield-infrastructure.csv",
                        ["Target", "Platform", "Scope", "Resource", "Priority", "Expected", "Observed", "First seen",
                         "Last seen", "Observations", "Reviewer", "Change reference", "Closed", "Resolution"], rows)
