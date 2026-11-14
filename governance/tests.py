from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
import uuid

import httpx
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .domain import Conflict, approve, complete_scan
from .github import CollectionError, FIELDS, GitHub, app_jwt, normalize_rules
from .models import Audit, Baseline, Incident, Membership, Monitor, Notification, Scan, Workspace
from .worker import check_overdue, claim, deliver_one, prune, scan_one, weekly_digests


VALUES = {"visibility": "private", "default_branch": "main", "archived": False, "rules": []}


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class WorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.owner = users.objects.create_user("owner", password="long-test-password")
        cls.viewer = users.objects.create_user("viewer")
        cls.outsider = users.objects.create_user("outsider")
        cls.workspace = Workspace.objects.create(name="Pilot", slug="pilot", recipient="dev@example.test")
        Membership.objects.create(workspace=cls.workspace, user=cls.owner, role="owner")
        Membership.objects.create(workspace=cls.workspace, user=cls.viewer, role="viewer")
        cls.monitor = Monitor.objects.create(workspace=cls.workspace, full_name="example/api", github_id=123, app_managed=True)
        cls.other = Workspace.objects.create(name="Other", slug="other")
        cls.other_monitor = Monitor.objects.create(workspace=cls.other, full_name="private/secret")
        Membership.objects.create(workspace=cls.other, user=cls.outsider, role="owner")

    def setUp(self):
        self.client.force_login(self.owner)

    def observe(self, values=None, error=None, name="example/api"):
        lease = uuid.uuid4()
        Monitor.objects.filter(pk=self.monitor.pk).update(lease=lease)
        snapshot = (123, name, values) if values else None
        self.assertTrue(complete_scan(self.monitor.pk, lease, snapshot=snapshot, error=error))
        self.monitor.refresh_from_db()

    def baseline(self):
        self.observe(VALUES)
        approve(self.workspace, self.monitor.pk, "owner", self.monitor.revision, 0, list(FIELDS), "Initial review")
        self.monitor.refresh_from_db()

    def test_role_isolation_and_csrf(self):
        url = reverse("monitor_action", args=["pilot", self.monitor.pk])
        self.assertEqual(self.client.get(reverse("monitor", args=["pilot", self.other_monitor.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("evidence", args=["other"])).status_code, 404)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("overview", args=["pilot"])).status_code, 200)
        self.assertEqual(self.client.post(url, {"action": "pause"}).status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.owner)
        self.assertEqual(csrf.post(url, {"action": "pause"}).status_code, 403)

    def test_dashboard_and_scan_workflow(self):
        self.assertContains(self.client.get("/w/pilot/"), "Repository overview")
        self.observe(VALUES)
        url = reverse("monitor_action", args=["pilot", self.monitor.pk])
        self.assertContains(self.client.get(reverse("monitor", args=["pilot", self.monitor.pk])), "Approve selected settings")
        response = self.client.post(url, {"action": "approve", "field": list(FIELDS), "reason": "Initial review",
                                          "revision": self.monitor.revision, "version": 0}, follow=True)
        self.assertContains(response, "Matches baseline")
        self.observe({**VALUES, "visibility": "public"})
        incident = Incident.objects.get(monitor=self.monitor)
        self.assertEqual(incident.severity, "critical")
        self.assertContains(self.client.post(url, {"action": "ack", "incident": incident.pk}, follow=True), "Acknowledged")
        self.assertContains(self.client.get(reverse("evidence", args=["pilot"])), "visibility")
        self.observe(VALUES)
        incident.refresh_from_db()
        self.assertEqual(incident.resolution, "restored")

    def test_selective_baseline_retains_unapproved_drift(self):
        self.baseline()
        changes = {**VALUES, "archived": True, "default_branch": "release"}
        self.observe(changes)
        self.observe(changes)
        self.assertEqual(Incident.objects.get(monitor=self.monitor, field="archived").occurrences, 2)
        self.assertEqual(Notification.objects.filter(workspace=self.workspace).count(), 2)
        with self.assertRaises(Conflict):
            approve(self.workspace, self.monitor.pk, "owner", self.monitor.revision - 1, 1, ["archived"], "Expected")
        approve(self.workspace, self.monitor.pk, "owner", self.monitor.revision, 1, ["archived"], "Expected")
        baseline = Baseline.objects.get(monitor=self.monitor, version=2)
        self.assertEqual(baseline.values["default_branch"], "main")
        self.assertEqual(Incident.objects.get(monitor=self.monitor, field="archived").resolution, "approved")
        self.assertTrue(Incident.objects.filter(monitor=self.monitor, field="default_branch", closed_at__isnull=True).exists())

    def test_policy_applies_before_baseline_and_resolves(self):
        self.workspace.min_reviews = 2
        self.workspace.require_private = True
        self.workspace.save()
        self.observe({**VALUES, "visibility": "public"})
        self.assertEqual(set(Incident.objects.values_list("field", flat=True)), {"policy_reviews", "policy_visibility"})
        rules = [{"type": "pull_request", "parameters": {"required_approving_review_count": 3}}]
        self.observe({**VALUES, "rules": rules})
        self.assertFalse(Incident.objects.filter(closed_at__isnull=True).exists())

    def test_scan_failure_preserves_evidence_and_honors_backoff(self):
        self.baseline()
        self.observe({**VALUES, "archived": True})
        self.observe(error=CollectionError("Temporary upstream error", 5000))
        self.assertGreater(self.monitor.next_scan - self.monitor.last_attempt, timedelta(seconds=4999))
        self.assertTrue(self.monitor.latest["archived"])
        self.assertTrue(Incident.objects.filter(monitor=self.monitor, closed_at__isnull=True).exists())
        with self.assertRaises(Conflict):
            approve(self.workspace, self.monitor.pk, "owner", self.monitor.revision, 1, ["archived"], "Expected")
        self.observe(error=CollectionError("Still down"))
        self.assertEqual(Notification.objects.filter(subject__startswith="Collection unavailable").count(), 1)

    @override_settings(EMAIL_HOST="smtp.example.test", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_notification_outbox_retry_and_retention(self):
        self.baseline()
        self.observe({**VALUES, "archived": True})
        with patch("governance.worker.EmailMessage.send", side_effect=OSError):
            self.assertTrue(deliver_one())
        notification = Notification.objects.get()
        self.assertEqual(notification.attempts, 1)
        self.assertIsNone(notification.sent_at)
        Notification.objects.update(next_attempt=timezone.now())
        self.assertTrue(deliver_one())
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(deliver_one())
        self.assertTrue(Notification.objects.filter(sent_at__isnull=False).exists())
        old = timezone.now() - timedelta(days=100)
        Audit.objects.update(created_at=old)
        Scan.objects.update(collected_at=old)
        prune()
        self.assertFalse(Audit.objects.exists())
        self.assertFalse(Scan.objects.exists())
        self.assertEqual(Incident.objects.count(), 1)
        self.assertEqual(Baseline.objects.count(), 1)

    def test_worker_keeps_identity_after_rename(self):
        Monitor.objects.exclude(pk=self.monitor.pk).update(paused=True)
        fake = type("FakeGitHub", (), {"snapshot": lambda _, monitor: (123, "example/renamed", VALUES)})()
        self.assertTrue(scan_one(fake))
        self.monitor.refresh_from_db()
        self.assertEqual(self.monitor.full_name, "example/renamed")

    def test_weekly_digest_and_overdue_are_deduplicated(self):
        self.workspace.digest_weekly = True
        self.workspace.save()
        Monitor.objects.filter(pk=self.monitor.pk).update(next_scan=timezone.now() - timedelta(minutes=10))
        for _ in range(2):
            weekly_digests()
            check_overdue()
        self.assertEqual(Notification.objects.filter(subject__startswith="DriftShield weekly summary").count(), 1)
        self.assertEqual(Notification.objects.filter(subject__startswith="Observation overdue").count(), 1)

    def test_owner_can_remove_monitor_and_keep_audit(self):
        self.baseline()
        url = reverse("remove_monitor", args=["pilot", self.monitor.pk])
        self.client.post(url, {"confirm": "incorrect"})
        self.assertTrue(Monitor.objects.filter(pk=self.monitor.pk).exists())
        response = self.client.post(url, {"confirm": "example/api"}, follow=True)
        self.assertContains(response, "Removed example/api")
        self.assertFalse(Baseline.objects.filter(monitor=self.monitor).exists())
        self.assertContains(self.client.get(reverse("audit", args=["pilot"])), "Monitor removed")

    def test_login_throttle(self):
        client = Client()
        for _ in range(20):
            self.assertEqual(client.post("/login/", {"username": "owner", "password": "incorrect"}).status_code, 200)
        self.assertEqual(client.post("/login/", {"username": "owner", "password": "incorrect"}).status_code, 429)

    def test_github_default_branch_rules_and_rate_limit(self):
        requests = []

        def transport(request):
            requests.append(request)
            if request.url.path == "/repos/example/api":
                return httpx.Response(200, json={"id": 123, "full_name": "example/api", **VALUES, "default_branch": "release/next"})
            return httpx.Response(200, json=[{"type": "required_signatures", "ruleset_id": 42}])

        self.monitor.github_id = None
        self.monitor.app_managed = False
        with httpx.Client(transport=httpx.MockTransport(transport)) as client:
            snapshot = GitHub(client).snapshot(self.monitor)
        self.assertEqual(snapshot[2]["rules"], [{"type": "required_signatures", "parameters": {}}])
        self.assertIn("release%2Fnext", str(requests[-1].url))
        self.assertNotIn("Authorization", requests[-1].headers)
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(429, headers={"Retry-After": "75"}))) as client:
            with self.assertRaises(CollectionError) as result:
                GitHub(client).request("/rate_limit")
        self.assertEqual(result.exception.retry_seconds, 75)

    def test_app_token_is_scoped_and_rules_are_canonical(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.assertEqual(len(app_jwt("app123", key).split(".")), 3)

        def transport(request):
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/app/installations/4/access_tokens")
            self.assertIn(b'"repository_ids":[123]', request.content)
            self.assertIn(b'"metadata":"read"', request.content)
            return httpx.Response(201, json={"token": "mock-short-lived"})

        with httpx.Client(transport=httpx.MockTransport(transport)) as client:
            self.assertEqual(GitHub(client).token("app123", key, 4, 123), "mock-short-lived")
        left = {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "B"}, {"context": "A"}]}}
        right = {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "A"}, {"context": "B"}]}}
        self.assertEqual(normalize_rules([left, right]), normalize_rules([right]))


class ConcurrencyTests(TransactionTestCase):
    def test_postgres_claim_is_single_and_expired_lease_is_recoverable(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL required for row-lock concurrency")
        workspace = Workspace.objects.create(slug="lease", name="Lease")
        monitor = Monitor.objects.create(workspace=workspace, full_name="example/api")

        def claim_id():
            try:
                result = claim(Monitor.objects.filter(pk=monitor.pk))
                return result.lease if result else None
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: claim_id(), range(4)))
        leases = [result for result in results if result is not None]
        self.assertEqual(len(leases), 1)
        Monitor.objects.filter(pk=monitor.pk).update(lease_until=timezone.now() - timedelta(minutes=1))
        replacement = claim_id()
        self.assertIsNotNone(replacement)
        self.assertNotEqual(replacement, leases[0])
        self.assertFalse(complete_scan(monitor.pk, leases[0], (123, "example/api", VALUES)))
        self.assertTrue(complete_scan(monitor.pk, replacement, (123, "example/api", VALUES)))
