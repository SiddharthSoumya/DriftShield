import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from governance.models import Workspace


class Target(models.Model):
    class Profile(models.TextChoices):
        LEGACY = "legacy", "Legacy"
        EBS_PILOT = "ebs_pilot", "AWS EBS pilot"

    PLATFORMS = [(name, label) for name, label in (
        ("terraform", "Terraform / OpenTofu"), ("docker", "Docker"), ("kubernetes", "Kubernetes"),
        ("aws", "AWS"), ("snapshot", "Custom snapshot"),
    )]
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    profile = models.CharField(max_length=12, choices=Profile, default=Profile.LEGACY)
    last_complete_delivery = models.ForeignKey("Delivery", on_delete=models.RESTRICT, null=True, related_name="+")
    last_contact_at = models.DateTimeField(null=True)
    retired_at = models.DateTimeField(null=True)
    name = models.SlugField(max_length=64)
    platform = models.CharField(max_length=12, choices=PLATFORMS)
    scope = models.CharField(max_length=160)
    token_hash = models.CharField(max_length=64, blank=True)
    token_expires_at = models.DateTimeField(null=True)
    interval = models.PositiveSmallIntegerField(default=30)
    created_at = models.DateTimeField(default=timezone.now)
    revision = models.PositiveIntegerField(default=0)
    version = models.PositiveIntegerField(default=0)
    latest = models.JSONField(default=dict)
    source_id = models.CharField(max_length=160, blank=True)
    last_success = models.DateTimeField(null=True)
    last_attempt = models.DateTimeField(null=True)
    last_error = models.CharField(max_length=32, blank=True)
    overdue_alerted = models.BooleanField(default=False)
    runbook_url = models.URLField(max_length=500, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(profile="legacy", last_complete_delivery__isnull=True, last_contact_at__isnull=True)
                | Q(profile="ebs_pilot", platform="aws", interval=30),
                name="infra_target_profile",
            ),
            models.UniqueConstraint(fields=["profile"], condition=Q(profile="ebs_pilot", retired_at__isnull=True), name="infra_single_pilot"),
            models.UniqueConstraint(fields=["workspace", "name"], name="infra_unique_name"),
            models.UniqueConstraint(fields=["workspace", "platform", "scope"], name="infra_unique_scope"),
        ]


class Delivery(models.Model):
    target = models.ForeignKey(Target, on_delete=models.CASCADE)
    identifier = models.UUIDField()
    received_at = models.DateTimeField(default=timezone.now, db_index=True)
    captured_at = models.DateTimeField()
    status = models.CharField(max_length=24)
    payload_hash = models.CharField(max_length=64, blank=True, default="")

    coverage = models.CharField(max_length=10, default="legacy")
    failure_code = models.CharField(max_length=32, blank=True)
    started_at = models.DateTimeField(null=True)
    projected_count = models.PositiveIntegerField(null=True)
    snapshot = models.JSONField(null=True)
    enrollment = models.ForeignKey("RunnerEnrollment", on_delete=models.PROTECT, null=True)
    credential_epoch = models.PositiveIntegerField(null=True)

    class Meta:
        indexes = [
            models.Index(fields=["target", "-captured_at", "-id"], name="infra_delivery_capture"),
            models.Index(fields=["target", "-received_at", "-id"], name="infra_delivery_receipt"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["target", "identifier"], name="infra_unique_delivery"),
            models.CheckConstraint(condition=Q(coverage__in=["legacy", "complete", "partial", "failed"]),
                                   name="infra_delivery_coverage"),
            models.CheckConstraint(
                condition=Q(coverage="legacy")
                | Q(enrollment__isnull=False, credential_epoch__isnull=False, credential_epoch__gte=1,
                    started_at__isnull=False, started_at__lte=models.F("captured_at")),
                name="infra_delivery_v2_context",
            ),
            models.CheckConstraint(
                condition=~Q(coverage="complete")
                | Q(projected_count__isnull=False, projected_count__lte=500, snapshot__isnull=False, failure_code=""),
                name="infra_delivery_complete",
            ),
            models.CheckConstraint(
                condition=~Q(coverage__in=["partial", "failed"])
                | (Q(projected_count__isnull=True, snapshot__isnull=True) & ~Q(failure_code="")),
                name="infra_delivery_incomplete",
            ),
        ]


class RunnerEnrollment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        REVOKED = "revoked", "Revoked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    target = models.OneToOneField(Target, on_delete=models.RESTRICT)
    account = models.CharField(max_length=12)
    region = models.CharField(max_length=32)
    scope = models.CharField(max_length=160)
    expected_role_prefix = models.CharField(max_length=240)
    runner_label = models.CharField(max_length=100)
    primary_operator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    credential_epoch = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, choices=Status, default=Status.PENDING)
    issued_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(account__regex=r"^[0-9]{12}$", region__regex=r"^[a-z]{2}(-[a-z0-9]+)+-[0-9]+$")
                & Q(scope=models.functions.Concat("account", models.Value(":"), "region", models.Value(":ebs"))),
                name="infra_enrollment_scope",
            ),
            models.CheckConstraint(
                condition=Q(status="pending", credential_epoch=0, issued_at__isnull=True, revoked_at__isnull=True)
                | Q(status="active", credential_epoch__gte=1, issued_at__isnull=False, revoked_at__isnull=True)
                | Q(status="revoked", credential_epoch__gte=1, issued_at__isnull=False, revoked_at__isnull=False),
                name="infra_enrollment_status",
            ),
        ]


class CoverageGap(models.Model):
    target = models.ForeignKey(Target, on_delete=models.RESTRICT)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True)
    opened_reason = models.CharField(max_length=24)
    ended_reason = models.CharField(max_length=24, blank=True)
    opening_delivery = models.ForeignKey(Delivery, on_delete=models.RESTRICT, null=True, related_name="+")
    closing_delivery = models.ForeignKey(Delivery, on_delete=models.RESTRICT, null=True, related_name="+")

    class Meta:
        indexes = [models.Index(fields=["target", "-started_at"], name="infra_gap_history")]
        constraints = [
            models.UniqueConstraint(fields=["target"], condition=Q(ended_at__isnull=True), name="infra_one_open_gap"),
            models.CheckConstraint(
                condition=Q(ended_at__isnull=True, ended_reason="", closing_delivery__isnull=True)
                | (Q(ended_at__isnull=False, ended_at__gte=models.F("started_at"))
                   & (Q(ended_reason="restored", closing_delivery__isnull=False)
                      | Q(ended_reason="retired", closing_delivery__isnull=True))),
                name="infra_gap_end",
            ),
        ]


class Baseline(models.Model):
    target = models.ForeignKey(Target, on_delete=models.CASCADE)
    version = models.PositiveIntegerField()
    values = models.JSONField()
    pilot_approval = models.OneToOneField("PilotApproval", on_delete=models.RESTRICT, null=True)
    actor = models.CharField(max_length=150)
    reason = models.CharField(max_length=240)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["target", "version"], name="infra_unique_version")]


class Finding(models.Model):
    target = models.ForeignKey(Target, on_delete=models.CASCADE)
    resource = models.CharField(max_length=240)
    expected = models.JSONField(null=True)
    actual = models.JSONField(null=True)
    priority = models.CharField(max_length=10)
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()
    count = models.PositiveIntegerField(default=1)
    closed_at = models.DateTimeField(null=True, db_index=True)
    resolution = models.CharField(max_length=24, blank=True)
    closure_delivery = models.ForeignKey(Delivery, on_delete=models.RESTRICT, null=True, related_name="+")
    closure_event = models.ForeignKey("EvidenceEvent", on_delete=models.RESTRICT, null=True, related_name="+")
    verified_review = models.ForeignKey("PilotApproval", on_delete=models.RESTRICT, null=True, related_name="+")
    absence_delivery = models.ForeignKey(Delivery, on_delete=models.RESTRICT, null=True, related_name="+")
    absence_confirmation = models.ForeignKey("EvidenceEvent", on_delete=models.RESTRICT, null=True, related_name="+")
    review_at = models.DateTimeField(null=True)
    reviewer = models.CharField(max_length=150, blank=True)
    reason = models.CharField(max_length=240, blank=True)
    baseline_version = models.PositiveIntegerField(null=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="drift_findings")
    review_revision = models.PositiveIntegerField(null=True)
    suppressed = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["target", "resource"], condition=Q(closed_at__isnull=True), name="infra_open_resource"),
            models.CheckConstraint(
                condition=~Q(resolution__in=["observed_restoration", "verified_remediation"])
                | (Q(closed_at__isnull=False, closure_delivery__isnull=False, closure_event__isnull=False)
                   & (Q(resolution="observed_restoration", verified_review__isnull=True)
                      | Q(resolution="verified_remediation", verified_review__isnull=False))),
                name="infra_closure_evidence",
            ),
        ]


class DriftException(models.Model):
    target = models.ForeignKey(Target, on_delete=models.CASCADE)
    resource = models.CharField(max_length=240)
    field = models.CharField(max_length=64)
    observed = models.JSONField(null=True)
    expected = models.JSONField(null=True)
    expires_at = models.DateTimeField(db_index=True)
    ended_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.CharField(max_length=150)
    reason = models.CharField(max_length=240)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["target", "resource", "field"],
                                               condition=Q(ended_at__isnull=True), name="infra_one_drift_exception")]


class EvidenceEvent(models.Model):
    target = models.ForeignKey(Target, on_delete=models.CASCADE)
    finding = models.ForeignKey(Finding, on_delete=models.SET_NULL, null=True)
    resource = models.CharField(max_length=240, blank=True)
    kind = models.CharField(max_length=32)
    actor = models.CharField(max_length=150)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["target", "id"], name="infra_event_target_id")]


class PilotApproval(models.Model):
    target = models.ForeignKey(Target, on_delete=models.RESTRICT)
    finding = models.ForeignKey(Finding, on_delete=models.RESTRICT, null=True)
    kind = models.CharField(max_length=12)
    state = models.CharField(max_length=10, default="pending")
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+")
    requested_at = models.DateTimeField(default=timezone.now)
    decided_at = models.DateTimeField(null=True)
    ticket = models.CharField(max_length=120)
    decision_reason = models.CharField(max_length=240, blank=True)
    execution_ref = models.CharField(max_length=240, blank=True)
    execution_completed_at = models.DateTimeField(null=True)
    delivery = models.ForeignKey(Delivery, on_delete=models.RESTRICT)
    base_version = models.PositiveIntegerField()
    proposal = models.JSONField()
    proposal_hash = models.CharField(max_length=64)

    class Meta:
        indexes = [models.Index(fields=["target", "state", "-requested_at"], name="infra_approval_queue")]
        constraints = [
            models.UniqueConstraint(fields=["target"], condition=Q(state="pending", kind="baseline"),
                                    name="infra_pending_baseline"),
            models.UniqueConstraint(fields=["finding"], condition=Q(state="pending", kind="remediation"),
                                    name="infra_pending_review"),
            models.CheckConstraint(
                condition=(Q(kind="baseline", finding__isnull=True, execution_ref="", execution_completed_at__isnull=True)
                           | (Q(kind="remediation", finding__isnull=False, execution_completed_at__isnull=False)
                              & ~Q(execution_ref=""))) & ~Q(ticket=""),
                name="infra_approval_kind",
            ),
            models.CheckConstraint(
                condition=(Q(state="pending", approver__isnull=True, decided_at__isnull=True)
                           | Q(state__in=["approved", "rejected"], approver__isnull=False, decided_at__isnull=False)
                           | Q(state="stale", decided_at__isnull=False))
                & (Q(approver__isnull=True) | ~Q(approver=models.F("requester")))
                & (Q(decided_at__isnull=True) | Q(decided_at__gte=models.F("requested_at"))),
                name="infra_approval_decision",
            ),
        ]
