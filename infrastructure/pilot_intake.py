"""Trusted snapshot, runner and coverage transitions. Mutations hold the target lock."""
from datetime import timedelta
from math import ceil

from django.conf import settings
from django.db import transaction

from collector.contract import EBS_ROLE, EBS_SCOPE, pilot_envelope
from governance.domain import audit
from governance.models import Membership, Workspace
from .domain import Conflict, RateLimited, alert, compare_pilot, fingerprint, issue_token
from .models import CoverageGap, Delivery, Finding, RunnerEnrollment, Target
from .triage import record

GRACE = timedelta(minutes=5)


class IntakeConflict(Conflict):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def current_delivery(target, enrollment):
    delivery = target.last_complete_delivery
    if (delivery and enrollment and delivery.enrollment_id == enrollment.pk
            and delivery.credential_epoch == enrollment.credential_epoch):
        return delivery
    return None


def deadline(target, enrollment):
    delivery = current_delivery(target, enrollment)
    anchor = delivery.captured_at if delivery else enrollment.issued_at
    return anchor + timedelta(minutes=target.interval) + GRACE if anchor else None


def coverage_state(target, now):
    enrollment = getattr(target, "runnerenrollment", None)
    if target.retired_at:
        return "retired"
    if enrollment is None or enrollment.status == RunnerEnrollment.Status.PENDING:
        return "unknown"
    if (enrollment.status != RunnerEnrollment.Status.ACTIVE or not target.token_expires_at
            or target.token_expires_at <= now):
        return "inactive"
    if target.last_error:
        if target.last_error.startswith("partial:"):
            return "partial"
        if target.last_error.startswith("failed:"):
            return "failed"
    expires = deadline(target, enrollment)
    if expires is not None and now >= expires:
        return "stale"
    if current_delivery(target, enrollment) is None or CoverageGap.objects.filter(target=target, ended_at=None).exists():
        return "unknown"
    return "complete"


def display_state(target, now):
    return {
        "retired": ("Retired", "muted"), "inactive": ("Collector credential inactive", "danger"),
        "unknown": ("Coverage unknown", "warning"), "partial": ("Coverage unknown · partial scan", "warning"),
        "failed": ("Collection failed", "danger"), "stale": ("Coverage stale", "warning"),
        "complete": ("Complete coverage", "good"),
    }[coverage_state(target, now)]


def open_gap(target, started, reason, delivery=None, actor="worker"):
    gap, created = CoverageGap.objects.get_or_create(
        target=target, ended_at=None,
        defaults={"started_at": started, "opened_reason": reason, "opening_delivery": delivery},
    )
    if created:
        record(target, "coverage_gap_opened", actor, gap=gap.pk, reason=reason, started_at=started.isoformat())
        alert(target, "Infrastructure coverage gap", f"Complete EBS coverage is unavailable ({reason}).")
    return gap


def end_gap(target, ended, reason, delivery=None, actor="collector"):
    gap = CoverageGap.objects.filter(target=target, ended_at=None).first()
    if gap is None:
        return
    if reason == "restored" and ended <= gap.started_at:
        return
    gap.ended_at = max(ended, gap.started_at)
    gap.ended_reason = reason
    gap.closing_delivery = delivery
    gap.save(update_fields=["ended_at", "ended_reason", "closing_delivery"])
    record(target, "coverage_gap_ended", actor, gap=gap.pk, reason=reason, ended_at=gap.ended_at.isoformat(),
           delivery=str(delivery.identifier) if delivery else None)


def active_runner_operator(target, user_id):
    return Membership.objects.filter(
        workspace=target.workspace, user_id=user_id, user__is_active=True, role__in=("owner", "operator"),
    ).exists()


class RunnerOperatorInactive(Exception):
    """The runner credential no longer has an eligible operator."""


def assert_enrolled_scope(target, payload, enrollment_id):
    enrollment = getattr(target, "runnerenrollment", None)
    if (enrollment is None or enrollment.status != RunnerEnrollment.Status.ACTIVE
            or enrollment.pk != enrollment_id or enrollment.scope != target.scope
            or payload["scope"] != enrollment.scope or payload["credential_epoch"] != enrollment.credential_epoch
            or payload["source_id"] != target.source_id):
        raise ValueError("Observation scope does not match the enrolled runner.")
    if not active_runner_operator(target, enrollment.primary_operator_id):
        raise RunnerOperatorInactive
    return enrollment


def accept_pilot(target, payload, now):
    delivery, captured, started, enrollment_id = pilot_envelope(payload, now)
    enrollment = assert_enrolled_scope(target, payload, enrollment_id)
    payload_hash = fingerprint(payload)
    previous = Delivery.objects.filter(target=target, identifier=delivery).first()
    if previous is not None:
        if previous.payload_hash != payload_hash or previous.credential_epoch != enrollment.credential_epoch:
            raise IntakeConflict("delivery_conflict", "Delivery ID has already been used.")
        return previous
    if started < enrollment.issued_at:
        raise IntakeConflict("before_enrollment", "Collection started before credential activation.")
    last = Delivery.objects.filter(target=target).exclude(coverage="legacy").order_by("-captured_at", "-pk").first()
    if last is not None and captured <= last.captured_at:
        raise IntakeConflict("out_of_order", "Observation arrived out of order.")
    if target.last_attempt:
        remaining = settings.DS_INGEST_MIN_SECONDS - (now - target.last_attempt).total_seconds()
        if remaining > 0:
            raise RateLimited(ceil(remaining))

    coverage = payload["coverage"]
    item = Delivery.objects.create(
        target=target, identifier=delivery, captured_at=captured, received_at=now, started_at=started,
        status=coverage, coverage=coverage, payload_hash=payload_hash, failure_code=payload.get("error", ""),
        projected_count=payload.get("projected_count"), snapshot=payload.get("resources"),
        enrollment=enrollment, credential_epoch=enrollment.credential_epoch,
    )
    previous_error = target.last_error
    target.last_attempt = target.last_contact_at = now
    if coverage == "complete":
        missed = deadline(target, enrollment)
        if missed is not None and captured > missed:
            open_gap(target, missed, "stale")
        open_interval = CoverageGap.objects.filter(target=target, ended_at=None).first()
        if open_interval and captured <= open_interval.started_at:
            raise IntakeConflict("before_coverage_gap", "Collect a new observation after the coverage gap began.")
        target.last_complete_delivery = item
        target.last_success = captured
        target.last_error = ""
        target.latest = item.snapshot
        target.revision += 1
        target.save(update_fields=["last_attempt", "last_contact_at", "last_complete_delivery", "last_success",
                                   "last_error", "latest", "revision"])
        end_gap(target, captured, "restored", item)
        item.status = compare_pilot(target, item, now)
        item.save(update_fields=["status"])
    else:
        target.last_error = f"{coverage}:{item.failure_code}"
        target.save(update_fields=["last_attempt", "last_contact_at", "last_error"])
        missed = deadline(target, enrollment)
        if missed is not None and missed < min(captured, now):
            open_gap(target, missed, "stale")
        open_gap(target, min(captured, now), coverage, item, actor="collector")
        if previous_error != target.last_error:
            record(target, "coverage_attempt_changed", coverage=coverage, reason=item.failure_code,
                   delivery=str(item.identifier))
    return item


def enroll_runner(target, actor, label, role, operator_id, ticket, now):
    scope = EBS_SCOPE.fullmatch(target.scope)
    role_match = EBS_ROLE.fullmatch(role)
    if (scope is None or role_match is None or role_match["account"] != scope["account"]
            or not 1 <= len(label) <= 100 or not label.isprintable()
            or not 1 <= len(ticket) <= 120 or not ticket.isprintable()):
        raise Conflict("Enter the enrolled AWS role prefix, runner label and change ticket.")
    if not 0 < operator_id < 2**63 or not active_runner_operator(target, operator_id):
        raise Conflict("Choose an active workspace owner or operator.")
    enrollment = getattr(target, "runnerenrollment", None)
    if enrollment is not None and enrollment.status == RunnerEnrollment.Status.ACTIVE:
        raise Conflict("Revoke the current credential before re-enrolling.")
    old_role = enrollment.expected_role_prefix if enrollment else ""
    if enrollment is None:
        enrollment = RunnerEnrollment(target=target, account=scope["account"], region=scope["region"], scope=target.scope)
    enrollment.expected_role_prefix = role
    enrollment.runner_label = label
    enrollment.primary_operator_id = operator_id
    enrollment.save()
    return rotate_runner(target, actor, now, enrollment, ticket=ticket, old_role=old_role)


def rotate_runner(target, actor, now, enrollment=None, **detail):
    enrollment = enrollment or getattr(target, "runnerenrollment", None)
    if enrollment is None:
        raise Conflict("Enroll a runner first.")
    if enrollment.credential_epoch >= 2**31 - 1:
        raise Conflict("Runner credential epoch exhausted.")
    if not active_runner_operator(target, enrollment.primary_operator_id):
        raise Conflict("Re-enroll with an active primary runner operator.")
    initial = enrollment.credential_epoch == 0
    enrollment.credential_epoch += 1
    enrollment.issued_at = now
    enrollment.revoked_at = None
    enrollment.status = RunnerEnrollment.Status.ACTIVE
    enrollment.save(update_fields=["credential_epoch", "issued_at", "revoked_at", "status"])
    token, target.token_hash = issue_token()
    target.token_expires_at = now + timedelta(days=90)
    target.source_id = f"aws:{enrollment.scope}"
    target.last_error = ""
    target.last_attempt = None
    target.save(update_fields=["token_hash", "token_expires_at", "source_id", "last_error", "last_attempt"])
    if not initial:
        invalidate_epoch(target, now)
        open_gap(target, now, "credential_rotated", actor=actor)
    event = "runner_enrolled" if initial or detail else "runner_rotated"
    record(target, event, actor, enrollment=str(enrollment.pk), epoch=enrollment.credential_epoch,
           role=enrollment.expected_role_prefix, **detail)
    audit(target.workspace, getattr(actor, "username", actor), event, target=target.name, epoch=enrollment.credential_epoch)
    return token, enrollment


def revoke_runner(target, actor, now):
    enrollment = getattr(target, "runnerenrollment", None)
    if enrollment is None or enrollment.status != RunnerEnrollment.Status.ACTIVE:
        return
    enrollment.status = RunnerEnrollment.Status.REVOKED
    enrollment.revoked_at = now
    enrollment.save(update_fields=["status", "revoked_at"])
    target.token_hash = ""
    target.token_expires_at = None
    target.save(update_fields=["token_hash", "token_expires_at"])
    invalidate_epoch(target, now)
    open_gap(target, now, "credential_revoked", actor=actor)
    record(target, "runner_revoked", actor, epoch=enrollment.credential_epoch)
    audit(target.workspace, getattr(actor, "username", actor), "runner_revoked", target=target.name, epoch=enrollment.credential_epoch)


def invalidate_epoch(target, now):
    from .pilot_review import stale_reviews

    Finding.objects.filter(target=target, closed_at__isnull=True).update(
        absence_delivery=None, absence_confirmation=None,
    )
    stale_reviews(target, now, "Runner credential changed.")



def check_pilot_gaps(now):
    if not settings.DS_PILOT_EBS:
        return
    candidates = Target.objects.filter(
        profile=Target.Profile.EBS_PILOT, retired_at__isnull=True, workspace__slug=settings.DS_PILOT_WORKSPACE,
    ).values_list("pk", "workspace_id")
    for pk, workspace_id in list(candidates):
        with transaction.atomic():
            if not Workspace.objects.select_for_update().filter(pk=workspace_id).exists():
                continue
            target = Target.objects.select_for_update().filter(pk=pk, workspace_id=workspace_id).first()
            if target is None:
                continue
            state = coverage_state(target, now)
            enrollment = getattr(target, "runnerenrollment", None)
            if state == "stale":
                open_gap(target, deadline(target, enrollment), "stale")
            elif state == "inactive" and enrollment and enrollment.status == RunnerEnrollment.Status.ACTIVE:
                open_gap(target, target.token_expires_at or now, "credential_expired")
