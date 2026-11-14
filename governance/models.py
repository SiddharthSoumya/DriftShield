from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Workspace(models.Model):
    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    recipient = models.EmailField(blank=True)
    infra_high_priority_only = models.BooleanField(default=False)
    retention_days = models.PositiveSmallIntegerField(default=90)
    repo_limit = models.PositiveSmallIntegerField(default=25)
    require_private = models.BooleanField(default=False)
    min_reviews = models.PositiveSmallIntegerField(default=0)
    digest_weekly = models.BooleanField(default=False)
    last_digest_week = models.CharField(max_length=8, blank=True)
    require_signatures = models.BooleanField(default=False)


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        OPERATOR = "operator", "Operator"
        VIEWER = "viewer", "Viewer"

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=8, choices=Role)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "user"], name="unique_workspace_member")]


class Integration(models.Model):
    workspace = models.OneToOneField(Workspace, on_delete=models.CASCADE)
    installation_id = models.PositiveBigIntegerField(unique=True)
    app_id = models.CharField(max_length=64)
    encrypted_key = models.BinaryField()
    account = models.CharField(max_length=100)


class Monitor(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    full_name = models.CharField(max_length=150)
    github_id = models.PositiveBigIntegerField(null=True)
    app_managed = models.BooleanField(default=False)
    paused = models.BooleanField(default=False)
    interval_minutes = models.PositiveSmallIntegerField(default=30)
    next_scan = models.DateTimeField(default=timezone.now, db_index=True)
    lease = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True)
    latest = models.JSONField(default=dict)
    last_success = models.DateTimeField(null=True)
    last_attempt = models.DateTimeField(null=True)
    last_error = models.CharField(max_length=240, blank=True)
    failures = models.PositiveSmallIntegerField(default=0)
    overdue_notified = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=0)
    baseline_version = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["workspace", "full_name"], name="unique_workspace_repo_name"),
            models.UniqueConstraint(fields=["workspace", "github_id"], name="unique_workspace_repo_id"),
        ]


class Baseline(models.Model):
    monitor = models.ForeignKey(Monitor, on_delete=models.CASCADE)
    version = models.PositiveIntegerField()
    values = models.JSONField()
    reason = models.CharField(max_length=240)
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.CharField(max_length=150)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["monitor", "version"], name="unique_monitor_baseline")]


class Scan(models.Model):
    monitor = models.ForeignKey(Monitor, on_delete=models.CASCADE)
    collected_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=20)
    error = models.CharField(max_length=240, blank=True)


class Incident(models.Model):
    monitor = models.ForeignKey(Monitor, on_delete=models.CASCADE)
    field = models.CharField(max_length=80)
    expected = models.JSONField(null=True)
    observed = models.JSONField(null=True)
    severity = models.CharField(max_length=8)
    baseline_version = models.PositiveIntegerField()
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()
    occurrences = models.PositiveIntegerField(default=1)
    acknowledged_at = models.DateTimeField(null=True)
    closed_at = models.DateTimeField(null=True, db_index=True)
    resolution = models.CharField(max_length=30, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["monitor", "field"], condition=Q(closed_at__isnull=True), name="one_open_field")]


class Audit(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    monitor = models.ForeignKey(Monitor, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=40)
    actor = models.CharField(max_length=150)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class Notification(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    recipient = models.EmailField()
    subject = models.CharField(max_length=200)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    next_attempt = models.DateTimeField(default=timezone.now, db_index=True)
    lease = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    sent_at = models.DateTimeField(null=True)
    failed_at = models.DateTimeField(null=True)


class WorkerHealth(models.Model):
    name = models.CharField(max_length=20, primary_key=True)
    updated_at = models.DateTimeField()


class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    attempts = models.PositiveIntegerField(default=0)
    window_start = models.DateTimeField()
