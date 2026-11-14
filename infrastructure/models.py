from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from governance.models import Workspace


class Target(models.Model):
    PLATFORMS = [(name, label) for name, label in (
        ("terraform", "Terraform / OpenTofu"), ("docker", "Docker"), ("kubernetes", "Kubernetes"),
        ("aws", "AWS"), ("snapshot", "Custom snapshot"),
    )]
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
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
            models.UniqueConstraint(fields=["workspace", "name"], name="infra_unique_name"),
            models.UniqueConstraint(fields=["workspace", "platform", "scope"], name="infra_unique_scope"),
        ]


class Delivery(models.Model):
    target = models.ForeignKey(Target, on_delete=models.CASCADE)
    identifier = models.UUIDField()
    received_at = models.DateTimeField(default=timezone.now, db_index=True)
    captured_at = models.DateTimeField()
    status = models.CharField(max_length=16)
    payload_hash = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["target", "identifier"], name="infra_unique_delivery")]


class Baseline(models.Model):
    target = models.ForeignKey(Target, on_delete=models.CASCADE)
    version = models.PositiveIntegerField()
    values = models.JSONField()
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
    resolution = models.CharField(max_length=16, blank=True)
    review_at = models.DateTimeField(null=True)
    reviewer = models.CharField(max_length=150, blank=True)
    reason = models.CharField(max_length=240, blank=True)
    baseline_version = models.PositiveIntegerField(null=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="drift_findings")
    review_revision = models.PositiveIntegerField(null=True)
    suppressed = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["target", "resource"], condition=Q(closed_at__isnull=True), name="infra_open_resource")]


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
