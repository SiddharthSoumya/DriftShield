from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from hashlib import sha256
from io import BytesIO, StringIO
from http.client import IncompleteRead
import json
import subprocess
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import Mock, patch
from uuid import uuid4
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from collector import ebs
from collector.__main__ import send
from collector.contract import pilot_envelope
from governance.models import Membership, Notification, WorkerHealth, Workspace
from governance.worker import deliver_one, enqueue_mail_canary
from . import pilot_export, pilot_intake
from .models import Baseline, CoverageGap, Delivery, EvidenceEvent, Finding, PilotApproval, Target

SCOPE = "123456789012:ap-south-1:ebs"
ROLE = "arn:aws:sts::123456789012:assumed-role/DriftShield/"
A = "ebs/vol-0123456789abcdef0"
B = "ebs/vol-0123456789abcdef1"
PROBE_TOKEN = "a" * 43


def volume(size=100):
    return {"size": size, "volume_type": "gp3", "encrypted": True, "iops": 3000, "throughput": 125}


class PilotFixture:
    def setUp(self):
        super().setUp()
        self.workspace = Workspace.objects.create(slug="pilot", name="Pilot", recipient="team@example.test")
        user = get_user_model()
        self.requester = user.objects.create_user("runner-operator")
        self.approver = user.objects.create_user("review-owner")
        self.viewer = user.objects.create_user("viewer")
        for actor, role in ((self.requester, "owner"), (self.approver, "owner"), (self.viewer, "viewer")):
            Membership.objects.create(workspace=self.workspace, user=actor, role=role)
        self.target = Target.objects.create(workspace=self.workspace, name="regional-ebs", platform="aws",
                                            profile=Target.Profile.EBS_PILOT, scope=SCOPE)
        self.now = timezone.now()
        self.token, self.enrollment = pilot_intake.enroll_runner(
            self.target, self.requester, "runner", ROLE, self.requester.pk, "CHG-1", self.now - timedelta(minutes=1),
        )
        self.endpoint = f"/api/v2/infrastructure/{self.target.pk}/observations/"
        self.detail = f"/w/pilot/infra/{self.target.pk}/"

    def payload(self, second, resources=None, *, coverage="complete", error="provider_denied"):
        time = self.now + timedelta(seconds=second)
        data = {
            "schema": 2, "platform": "aws", "scope": SCOPE, "source_id": f"aws:{SCOPE}",
            "enrollment_id": str(self.enrollment.pk), "credential_epoch": self.enrollment.credential_epoch,
            "delivery": str(uuid4()), "started_at": time.isoformat(), "captured_at": time.isoformat(),
            "coverage": coverage,
        }
        if coverage == "complete":
            data.update(resources=resources if resources is not None else {A: volume()},
                        projected_count=len(resources) if resources is not None else 1)
        else:
            data["error"] = error
        return data

    def report(self, payload, *, client=None, token=None, second=20, endpoint=None):
        with patch("infrastructure.views.timezone.now", return_value=self.now + timedelta(seconds=second)):
            return (client or self.client).post(endpoint or self.endpoint, payload, content_type="application/json",
                                                HTTP_AUTHORIZATION=f"Bearer {token or self.token}")

    def action(self, action, *, actor=None, at=20, **fields):
        self.target.refresh_from_db()
        self.client.force_login(actor or self.requester)
        with patch("infrastructure.views.timezone.now", return_value=self.now + timedelta(seconds=at)):
            return self.client.post(self.detail + "action/", {
                "action": action, "revision": self.target.revision, "version": self.target.version,
                "ticket": "CHG-123", **fields,
            })

    def baseline(self):
        self.assertEqual(self.report(self.payload(1)).json()["status"], "unbaselined")
        self.action("request_baseline", at=21)
        proposal = PilotApproval.objects.get(kind="baseline")
        self.action("approve_baseline", actor=self.approver, at=22, approval_id=proposal.pk)
        self.target.refresh_from_db()
        self.assertEqual(self.target.version, 1)
        self.assertEqual(Baseline.objects.get().pilot_approval.approver, self.approver)


@override_settings(DS_PILOT_EBS=True, DS_PILOT_STAGING=True, DS_PILOT_WORKSPACE="pilot", DS_INGEST_MIN_SECONDS=0)
class PilotWorkflowTests(PilotFixture, TestCase):
    @override_settings(DS_RELEASE_SHA="a" * 40)
    def test_failed_drill_keeps_evidence_and_exits_nonzero(self):
        args = self.drill_args(checks_pass=False)
        with self.assertRaisesMessage(CommandError, "Recovery drill failed"):
            call_command(*args, stdout=StringIO())
        self.assertIs(EvidenceEvent.objects.get(kind="recovery_drill").detail["passed"], False)

    def drill_args(self, *, checks_pass=True):
        now = timezone.now()
        return (
            "pilot_recovery", "record", "--workspace", "pilot", "--target", str(self.target.pk),
            "--ticket", "REC-1", "--operator", "operator", "--started", (now - timedelta(minutes=2)).isoformat(),
            "--ended", (now - timedelta(minutes=1)).isoformat(),
            "--recovery-point", (now - timedelta(minutes=3)).isoformat(), "--backup-ref", "backup-1",
            "--checksum", "f" * 64, "--release", "a" * 40, "--rto-target-seconds", "120",
            "--rpo-target-seconds", "120", "--loss-seconds", "60", "--checks",
            json.dumps(dict(access=True, credentials=True, evidence=True, collector=True, mailbox=checks_pass,
                            rollback=True)),
        )

    @override_settings(DS_PILOT_STAGING=False, DS_PILOT_ACCEPTANCE_TICKET="CHG-1", DS_RELEASE_SHA="a" * 40,
                       DS_PILOT_PROBE_TOKEN=PROBE_TOKEN, EMAIL_HOST="relay.test", APP_ORIGIN="https://pilot.test")
    def test_production_needs_activation_and_reconciliation_invalidates_it(self):
        if connection.vendor != "postgresql":
            self.skipTest("Activation requires PostgreSQL.")
        self.assertEqual(self.report(self.payload(1)).json()["code"], "activation_required")
        self.assertEqual(self.action("rotate").status_code, 503)
        with patch("governance.management.commands.work.close_old_connections"), self.assertRaisesMessage(
            CommandError, "activation has not been approved",
        ):
            call_command("work", "--once", stdout=StringIO())
        args = ("pilot_check", "--workspace", "pilot", "--activate", "--ticket", "CHG-1",
                "--operator", "operator", "--recovery-ticket", "REC-1", "--acceptance-ref", "SIGNED-1")
        with override_settings(DS_PILOT_RECOVERY_FENCED=True):
            with self.assertRaisesMessage(CommandError, "passing recovery drill"):
                call_command(*args, stdout=StringIO())
            call_command(*self.drill_args(), stdout=StringIO())
            call_command(*args, stdout=StringIO())
            self.assertEqual(self.report(self.payload(1)).json()["code"], "recovery_fenced")
        self.assertEqual(self.report(self.payload(1)).status_code, 200)
        with override_settings(DS_RELEASE_SHA="b" * 40):
            self.assertEqual(self.report(self.payload(2)).json()["code"], "activation_required")
        with self.assertRaisesMessage(CommandError, "Recovery drill failed"):
            call_command(*self.drill_args(checks_pass=False), stdout=StringIO())
        self.assertEqual(self.report(self.payload(2)).json()["code"], "activation_required")
        with override_settings(DS_PILOT_RECOVERY_FENCED=True):
            call_command("pilot_recovery", "reconcile", "--workspace", "pilot", "--target", str(self.target.pk),
                         "--ticket", "REC-2", "--operator", "operator", stdout=StringIO())
        from governance.pilot import activation_required

        self.assertTrue(activation_required(self.workspace.pk))

    def test_partial_cannot_seed_and_replay_is_idempotent(self):
        partial = self.payload(1, coverage="partial", error="incomplete_inventory")
        self.assertEqual(self.report(partial).json()["coverage"], "partial")
        self.assertEqual(self.report(partial).status_code, 200)
        self.action("request_baseline")
        self.target.refresh_from_db()
        self.assertEqual((self.target.revision, self.target.version, self.target.latest), (0, 0, {}))
        self.assertEqual(Delivery.objects.count(), 1)
        self.assertEqual(CoverageGap.objects.filter(ended_at=None).count(), 1)
        self.assertFalse(PilotApproval.objects.exists())
        self.assertEqual(self.report(self.payload(2, {})).json()["coverage"], "complete")
        self.assertEqual(CoverageGap.objects.get().ended_reason, "restored")

    def test_unknown_scope_legacy_and_revoked_epoch_cannot_write(self):
        data = self.payload(1)
        wrong_scope = "123456789012:us-east-1:ebs"
        self.assertEqual(self.report(dict(data, scope=wrong_scope, source_id=f"aws:{wrong_scope}")).status_code, 422)
        self.assertEqual(self.report(dict(data, credential_epoch=2)).status_code, 422)
        self.assertEqual(self.report(dict(data, enrollment_id=str(uuid4()))).status_code, 422)
        self.assertEqual(self.report(data, endpoint=self.endpoint.replace("/v2/", "/v1/")).json()["code"],
                         "pilot_v2_required")
        self.action("rotate", at=25)
        self.assertEqual(self.report(data, second=30).status_code, 401)
        self.assertEqual(Delivery.objects.count(), 0)
        self.assertEqual(CoverageGap.objects.filter(ended_at=None).count(), 1)

    @override_settings(DS_INGEST_MIN_SECONDS=60)
    def test_retry_after_and_conflicting_replay(self):
        first = self.payload(1)
        self.assertEqual(self.report(first, second=2).status_code, 200)
        self.assertEqual(self.report(first, second=10).status_code, 200)
        changed = dict(first, resources={A: volume(120)})
        self.assertEqual(self.report(changed, second=10).json()["code"], "delivery_conflict")
        limited = self.report(self.payload(11), second=12)
        self.assertEqual((limited.status_code, limited["Retry-After"]), (429, "50"))
        self.assertEqual(Delivery.objects.count(), 1)

    def test_stale_baseline_is_recorded_without_version_change(self):
        self.report(self.payload(1))
        self.action("request_baseline", at=21)
        proposal = PilotApproval.objects.get()
        self.report(self.payload(22, {A: volume(110)}), second=23)
        self.action("approve_baseline", actor=self.approver, at=24, approval_id=proposal.pk)
        proposal.refresh_from_db()
        self.assertEqual(proposal.state, "stale")
        self.assertFalse(Baseline.objects.exists())

    def test_revoked_membership_blocks_old_browser_session(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(self.detail).status_code, 200)
        call_command("revoke", "pilot", self.viewer.username, stdout=StringIO())
        self.assertEqual(self.client.get(self.detail).status_code, 404)

    def test_offboarding_primary_operator_revokes_runner_and_session(self):
        self.client.force_login(self.requester)
        self.assertEqual(self.client.get(self.detail).status_code, 200)
        call_command("revoke", "pilot", self.requester.username, stdout=StringIO())
        self.enrollment.refresh_from_db()
        self.target.refresh_from_db()
        self.assertEqual(self.enrollment.status, "revoked")
        self.assertEqual(self.target.token_hash, "")
        self.assertEqual(self.client.get(self.detail).status_code, 404)
        self.assertEqual(self.report(self.payload(1)).status_code, 401)
        self.assertTrue(CoverageGap.objects.filter(target=self.target, ended_at=None).exists())

    def test_inactive_operator_blocks_intake_even_without_revoke_command(self):
        Membership.objects.filter(workspace=self.workspace, user=self.requester).update(role="viewer")
        self.assertEqual(self.report(self.payload(1)).status_code, 401)
        self.assertFalse(Delivery.objects.exists())

    @override_settings(EMAIL_HOST="relay.test")
    def test_route_change_keeps_dispositions_and_replaces_pending_canary(self):
        now = timezone.now()
        slot = enqueue_mail_canary(now)
        queued = Notification.objects.create(workspace=self.workspace, recipient=self.workspace.recipient,
                                              subject="Drift", body="Pending alert")
        self.client.force_login(self.approver)
        self.client.post("/w/pilot/settings/", {"recipient": "new@example.test", "retention": 90})
        queued.refresh_from_db()
        self.assertEqual(queued.disposition, Notification.Disposition.ROUTE_CHANGED)
        self.assertIsNotNone(queued.failed_at)
        enqueue_mail_canary(now)
        enqueue_mail_canary(now)
        self.assertEqual(Notification.objects.filter(canary_slot=slot).count(), 2)
        replacement = Notification.objects.get(canary_slot=slot, failed_at=None)
        self.assertEqual(replacement.recipient, "new@example.test")

    def test_route_change_waits_for_claimed_delivery(self):
        Notification.objects.create(workspace=self.workspace, recipient=self.workspace.recipient,
                                    subject="Drift", body="Claimed", lease=uuid4(),
                                    lease_until=timezone.now() + timedelta(minutes=5))
        self.client.force_login(self.approver)
        response = self.client.post("/w/pilot/settings/", {"recipient": "new@example.test", "retention": 90}, follow=True)
        self.assertContains(response, "Wait for the active mail delivery")
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.recipient, "team@example.test")
        self.assertIsNone(Notification.objects.get().failed_at)

    @override_settings(EMAIL_HOST="relay.test")
    def test_worker_keeps_a_stale_route_row_as_canceled(self):
        item = Notification.objects.create(workspace=self.workspace, recipient="old@example.test",
                                           subject="Drift", body="Previous route")
        with patch("governance.worker.EmailMessage.send") as send_message:
            self.assertTrue(deliver_one())
        send_message.assert_not_called()
        item.refresh_from_db()
        self.assertEqual(item.disposition, Notification.Disposition.ROUTE_CHANGED)

    def test_cadence_and_failed_then_silent_gap(self):
        self.report(self.payload(1, coverage="failed"))
        self.report(self.payload(0))
        self.target.refresh_from_db()
        self.assertEqual(self.target.last_error, "failed:provider_denied")
        pilot_intake.check_pilot_gaps(self.now + timedelta(hours=1))
        self.assertEqual(CoverageGap.objects.count(), 1)
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(pilot_intake.coverage_state(self.target, self.now + timedelta(hours=1)), "failed")

    def test_stale_state_without_worker_and_inferred_missed_gap(self):
        self.report(self.payload(1))
        self.target.refresh_from_db()
        self.assertEqual(pilot_intake.coverage_state(self.target, self.now + timedelta(minutes=36)), "stale")
        delayed = self.payload(36 * 60)
        self.assertEqual(self.report(delayed, second=36 * 60 + 2).status_code, 200)
        self.assertEqual(CoverageGap.objects.get().opened_reason, "stale")
        self.assertEqual(CoverageGap.objects.get().ended_reason, "restored")

    def test_deadline_intake_is_independent_of_sweep_order(self):
        self.assertEqual(self.report(self.payload(1)).status_code, 200)
        elapsed = 35 * 60 + 1
        deadline = self.now + timedelta(seconds=elapsed)
        at_deadline = self.payload(elapsed)
        early_capture = self.payload(elapsed - 1)
        for payload in (at_deadline, early_capture):
            self.assertEqual(self.report(payload, second=elapsed).json()["code"], "before_coverage_gap")
        self.assertFalse(CoverageGap.objects.exists())
        pilot_intake.check_pilot_gaps(deadline)
        for payload in (at_deadline, early_capture):
            self.assertEqual(self.report(payload, second=elapsed).json()["code"], "before_coverage_gap")
        self.assertEqual(Delivery.objects.count(), 1)
        self.assertEqual(self.report(self.payload(elapsed + 1), second=elapsed + 2).status_code, 200)
        gap = CoverageGap.objects.get()
        self.assertEqual(gap.started_at, deadline)
        self.assertEqual(gap.ended_at, deadline + timedelta(seconds=1))
        self.assertEqual(Notification.objects.count(), 1)

    def test_baseline_requires_distinct_owner_and_complete_current_data(self):
        self.report(self.payload(1))
        self.action("request_baseline", at=21)
        approval = PilotApproval.objects.get()
        self.action("approve_baseline", approval_id=approval.pk, at=22)
        self.assertFalse(Baseline.objects.exists())
        self.report(self.payload(23, coverage="partial", error="inventory_limit"), second=24)
        self.action("approve_baseline", actor=self.approver, approval_id=approval.pk, at=25)
        self.assertFalse(Baseline.objects.exists())
        self.report(self.payload(26), second=27)
        self.action("approve_baseline", actor=self.approver, approval_id=approval.pk, at=28)
        self.assertEqual(Baseline.objects.get().pilot_approval.approver, self.approver)

    def test_review_and_disappearance_need_independent_confirmation(self):
        self.baseline()
        self.report(self.payload(23, {A: volume(), B: volume(150)}), second=24)
        finding = Finding.objects.get()
        self.action("request_review", at=28, finding=finding.pk, execution_ref="aws-change-23",
                    execution_completed_at=(self.now + timedelta(seconds=26)).isoformat())
        review = PilotApproval.objects.get(kind="remediation")
        self.action("approve_review", at=29, actor=self.approver, approval_id=review.pk)
        self.report(self.payload(30, coverage="partial", error="invalid_inventory"), second=31)
        finding.refresh_from_db()
        self.assertIsNone(finding.closed_at)
        self.assertEqual(self.report(self.payload(32), second=33).json()["status"], "awaiting_confirmation")
        finding.refresh_from_db()
        self.assertIsNone(finding.closed_at)
        fields = {"finding": finding.pk, "candidate_delivery_id": finding.absence_delivery_id,
                  "external_ref": "aws-request-123", "checked_at": (self.now + timedelta(seconds=34)).isoformat(),
                  "provider_absence": "1"}
        self.action("confirm_absence", at=35, **fields)
        finding.refresh_from_db()
        self.assertIsNone(finding.closed_at)
        self.action("confirm_absence", actor=self.approver, at=36, **fields)
        finding.refresh_from_db()
        self.assertEqual(finding.resolution, "verified_remediation")
        self.assertEqual(finding.verified_review_id, review.pk)
        self.assertIsNotNone(finding.closure_event_id)

    def test_matching_snapshot_without_review_is_observed_restoration(self):
        self.baseline()
        self.report(self.payload(23, {A: volume(150)}), second=24)
        self.report(self.payload(25), second=26)
        item = Finding.objects.get()
        self.assertEqual(item.resolution, "observed_restoration")
        self.assertIsNone(item.verified_review_id)
        self.assertIsNotNone(item.closure_event_id)

    def test_observation_captured_before_approval_does_not_verify(self):
        self.baseline()
        self.report(self.payload(23, {A: volume(150)}), second=24)
        item = Finding.objects.get()
        self.action("request_review", at=26, finding=item.pk, execution_ref="execution-1",
                    execution_completed_at=(self.now + timedelta(seconds=25)).isoformat())
        approval = PilotApproval.objects.get(kind="remediation")
        self.action("approve_review", actor=self.approver, approval_id=approval.pk, at=28)
        data = self.payload(29)
        data["started_at"] = (self.now + timedelta(seconds=27)).isoformat()
        self.report(data, second=30)
        item.refresh_from_db()
        self.assertEqual(item.resolution, "observed_restoration")

    def test_confirmed_baseline_disappearance_stays_open_until_approved(self):
        self.baseline()
        self.report(self.payload(23, {}), second=24)
        finding = Finding.objects.get()
        self.action("confirm_absence", actor=self.approver, at=26, finding=finding.pk,
                    candidate_delivery_id=finding.absence_delivery_id, provider_absence="1",
                    external_ref="aws-read-1", checked_at=(self.now + timedelta(seconds=25)).isoformat())
        finding.refresh_from_db()
        self.assertIsNone(finding.closed_at)
        self.assertEqual(finding.priority, "high")
        self.action("request_baseline", at=27, finding=finding.pk)
        approval = PilotApproval.objects.get(kind="baseline", state="pending")
        self.action("approve_baseline", actor=self.approver, approval_id=approval.pk, at=28)
        self.assertEqual(Baseline.objects.latest("version").values, {})

    def test_closure_write_failure_rolls_back_delivery_snapshot_and_event(self):
        self.baseline()
        self.report(self.payload(23, {A: volume(150)}), second=24)
        event_count = EvidenceEvent.objects.count()
        with patch("infrastructure.pilot_review.record", side_effect=DatabaseError("injected")):
            with self.assertRaises(DatabaseError):
                self.report(self.payload(25), second=26)
        self.target.refresh_from_db()
        self.assertEqual(self.target.latest, {A: volume(150)})
        self.assertEqual(Delivery.objects.count(), 2)
        self.assertEqual(EvidenceEvent.objects.count(), event_count)
        self.assertIsNone(Finding.objects.get().closed_at)

    def test_target_detail_and_archive_render(self):
        self.baseline()
        self.client.force_login(self.approver)
        self.assertContains(self.client.get(self.detail), "Baseline approvals")
        self.action("retire", actor=self.approver, confirm=self.target.name)
        self.assertContains(self.client.get(self.detail), "Evidence remains available")
        self.assertContains(self.client.get("/w/pilot/infra/?archived=1"), self.target.name)

    @override_settings(DS_PILOT_EBS=False)
    def test_flag_off_leaves_existing_pilot_read_only(self):
        self.assertEqual(self.report(self.payload(1)).status_code, 404)
        self.assertEqual(self.action("rotate").status_code, 503)
        self.assertFalse(Delivery.objects.exists())

    @override_settings(DS_PILOT_RECOVERY_FENCED=True, EMAIL_HOST="relay.test", DS_PILOT_PROBE_TOKEN=PROBE_TOKEN)
    def test_recovery_fence_and_reconciliation(self):
        self.assertEqual(self.report(self.payload(1)).json()["code"], "recovery_fenced")
        self.assertEqual(self.action("rotate").status_code, 503)
        self.assertFalse(deliver_one())
        args = ("pilot_recovery", "reconcile", "--workspace", "pilot", "--target", str(self.target.pk),
                "--ticket", "REC-123", "--operator", "owner")
        call_command(*args, stdout=StringIO())
        call_command(*args, stdout=StringIO())
        self.assertEqual(EvidenceEvent.objects.filter(kind="recovery_reconciled").count(), 1)
        self.assertFalse(Notification.objects.filter(sent_at=None, failed_at=None).exists())
        self.assertEqual(self.client.get("/internal/pilot/healthz",
                                         HTTP_AUTHORIZATION=f"Bearer {PROBE_TOKEN}").status_code, 503)

    @override_settings(DS_PILOT_RECOVERY_FENCED=True)
    def test_recovery_issue_requires_reconciliation_and_new_complete_report(self):
        args = ("--workspace", "pilot", "--target", str(self.target.pk), "--ticket", "REC-789", "--operator", "owner")
        with self.assertRaises(CommandError):
            call_command("pilot_recovery", "issue-runner", *args, stdout=StringIO())
        call_command("pilot_recovery", "reconcile", *args, stdout=StringIO())
        output = StringIO()
        with patch("infrastructure.management.commands.pilot_recovery.sys.stdin.isatty", return_value=True), patch(
            "infrastructure.management.commands.pilot_recovery.sys.stdout.isatty", return_value=True,
        ), self.captureOnCommitCallbacks(execute=True):
            call_command("pilot_recovery", "issue-runner", *args, stdout=output)
        credential = json.loads(output.getvalue())
        self.assertEqual(credential["credential_epoch"], 2)
        self.assertEqual(self.report(self.payload(25), token=credential["token"], second=26).status_code, 503)
        with override_settings(DS_PILOT_RECOVERY_FENCED=False):
            self.assertEqual(self.report(self.payload(25), second=26).status_code, 401)
            self.enrollment.refresh_from_db()
            report = self.report(self.payload(25), token=credential["token"], second=26)
            self.assertEqual(report.json()["coverage"], "complete")
        self.assertFalse(CoverageGap.objects.filter(ended_at=None).exists())

    @override_settings(DS_PILOT_RECOVERY_FENCED=True, EMAIL_HOST="relay.test")
    def test_worker_once_ignores_pending_delivery_during_recovery(self):
        with patch("governance.management.commands.work.deliver_one") as deliver, patch(
            "governance.management.commands.work.prune",
        ) as prune:
            call_command("work", "--once", stdout=StringIO())
        deliver.assert_not_called()
        prune.assert_not_called()

    @override_settings(EMAIL_HOST="relay.test", DS_PILOT_PROBE_TOKEN=PROBE_TOKEN)
    def test_probe_shows_relay_acceptance_without_claiming_mailbox_receipt(self):
        self.report(self.payload(1))
        WorkerHealth.objects.create(name="worker", updated_at=timezone.now())
        now = timezone.now()
        enqueue_mail_canary(now)
        enqueue_mail_canary(now)
        self.assertEqual(Notification.objects.count(), 1)
        with patch("governance.worker.EmailMessage.send", return_value=1):
            self.assertTrue(deliver_one())
        self.assertEqual(self.client.get("/internal/pilot/healthz").status_code, 401)
        payload = self.client.get("/internal/pilot/healthz", HTTP_AUTHORIZATION=f"Bearer {PROBE_TOKEN}").json()
        self.assertEqual(payload["latest_canary_relay_state"], "accepted")
        self.assertNotIn("mailbox_confirmed", payload)


@override_settings(DS_PILOT_EBS=True, DS_PILOT_STAGING=True, DS_PILOT_WORKSPACE="pilot", DS_INGEST_MIN_SECONDS=0)
class PilotPostgresTests(PilotFixture, TransactionTestCase):
    def test_concurrent_intake_commits_one_delivery_and_gap(self):
        if connection.vendor != "postgresql":
            self.skipTest("Requires PostgreSQL row locks.")
        data = self.payload(1, coverage="partial", error="incomplete_inventory")

        def submit(_):
            try:
                return self.report(data, client=Client()).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(list(executor.map(submit, range(2))), [200, 200])
        self.assertEqual(Delivery.objects.count(), 1)
        self.assertEqual(CoverageGap.objects.count(), 1)
        self.assertEqual(Notification.objects.count(), 1)

    def test_concurrent_baseline_approval_commits_once(self):
        if connection.vendor != "postgresql":
            self.skipTest("Requires PostgreSQL row locks.")
        self.report(self.payload(1))
        self.action("request_baseline", at=21)
        proposal = PilotApproval.objects.get()
        approver = self.approver

        def approve(_):
            client = Client()
            try:
                client.force_login(approver)
                return client.post(self.detail + "action/", {
                    "action": "approve_baseline", "approval_id": proposal.pk,
                }).status_code
            finally:
                close_old_connections()

        with patch("infrastructure.views.timezone.now", return_value=self.now + timedelta(seconds=22)):
            with ThreadPoolExecutor(max_workers=2) as executor:
                self.assertEqual(list(executor.map(approve, range(2))), [302, 302])
        self.assertEqual(Baseline.objects.count(), 1)
        self.assertEqual(EvidenceEvent.objects.filter(kind="baseline_approved").count(), 1)

    def test_archive_and_repeatable_read_bundle(self):
        self.baseline()
        self.action("retire", actor=self.approver, confirm=self.target.name)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(self.detail + "evidence-bundle/").status_code, 403)
        self.client.force_login(self.approver)
        response = self.client.get(self.detail + "evidence-bundle/")
        self.assertEqual(response.status_code, 200)
        try:
            binary = b"".join(response.streaming_content)
            with ZipFile(BytesIO(binary)) as bundle:
                manifest = json.loads(bundle.read("manifest.json"))
                for name, metadata in manifest["files"].items():
                    content = bundle.read(name)
                    self.assertEqual(sha256(content).hexdigest(), metadata["sha256"])
                    self.assertEqual(len(content.splitlines()), metadata["rows"])
                    self.assertNotIn(self.token.encode(), content)
                self.assertIn(b"target_retired", bundle.read("events.jsonl"))
        finally:
            response.close()
        if connection.vendor == "postgresql":
            with self.assertRaises(DatabaseError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM infrastructure_target WHERE id=%s", [self.target.pk])

    def test_export_budget_returns_error_instead_of_partial_zip(self):
        self.client.force_login(self.approver)
        with patch.object(pilot_export, "MAX_BYTES", 1):
            response = self.client.get(self.detail + "evidence-bundle/")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "export_range_required")


class EbsContractTests(SimpleTestCase):
    def setUp(self):
        self.args = SimpleNamespace(scope=SCOPE, region="ap-south-1", expected_role_prefix=ROLE,
                                    enrollment_id=str(uuid4()), credential_epoch=1, collection_failed=False)
        self.identity = {"Arn": ROLE + "runner-session", "Account": "123456789012"}
        self.row = {"VolumeId": A.removeprefix("ebs/"), "Size": 100, "VolumeType": "gp3",
                    "Encrypted": True, "Iops": 3000, "Throughput": 125, "State": "deleting"}

    def test_collector_rejects_residual_pagination_and_duplicate_inventory(self):
        for document, expected in (
            ({"Volumes": [self.row], "NextToken": "remaining"}, "incomplete_inventory"),
            ({"Volumes": [self.row, self.row]}, "invalid_inventory"),
            ({"Volumes": [self.row] * 501}, "inventory_limit"),
        ):
            with self.subTest(error=expected):
                report, _ = ebs.build_report(self.args, Mock(side_effect=[self.identity, document]))
                self.assertEqual((report["coverage"], report["error"]), ("partial", expected))
                self.assertNotIn("resources", report)

    def test_collector_keeps_transient_and_empty_inventory_and_uses_no_filters(self):
        cli = Mock(side_effect=[self.identity, {"Volumes": [self.row]}])
        report, _ = ebs.build_report(self.args, cli)
        self.assertEqual(report["resources"], {A: volume()})
        self.assertEqual(cli.call_args.args[0],
                         ["aws", "--no-cli-pager", "--output", "json", "--region", "ap-south-1", "ec2", "describe-volumes"])
        empty, _ = ebs.build_report(self.args, Mock(side_effect=[self.identity, {"Volumes": []}]))
        self.assertEqual((empty["coverage"], empty["resources"]), ("complete", {}))

    def test_denied_read_never_exposes_provider_error(self):
        error = subprocess.CalledProcessError(254, ["aws"], stderr=b"(UnauthorizedOperation) internal details")
        report, _ = ebs.build_report(self.args, Mock(side_effect=[self.identity, error]))
        self.assertEqual((report["coverage"], report["error"]), ("failed", "provider_denied"))
        self.assertNotIn("internal details", json.dumps(report))

    def test_envelope_rejects_bad_count_wrong_resource_and_malformed_metadata(self):
        report, now = ebs.build_report(self.args, Mock(side_effect=[self.identity, {"Volumes": [self.row]}]))
        for change in (
            {"projected_count": True}, {"projected_count": 0}, {"scope": []}, {"credential_epoch": True},
            {"resources": {"ec2/i-123": {"instance_type": "x", "state": "running"}}},
            {"coverage": "partial", "error": "incomplete_inventory"}, {"captured_at": "2020-01-01T00:00:00Z"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                pilot_envelope(dict(report, **change), now)



class CollectorRetryTests(SimpleTestCase):
    def test_429_retry_reuses_exact_payload_and_retries_truncated_response(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"status":"unbaselined"}'
        opener = Mock()
        opener.open.side_effect = [
            HTTPError("https://pilot.test", 429, "Too Many Requests", {"Retry-After": "2"}, None),
            IncompleteRead(b"partial"),
            response,
        ]
        body = b'{"delivery":"unique"}'
        with patch("collector.__main__.build_opener", return_value=opener), patch("collector.__main__.time.sleep") as delay:
            self.assertEqual(send("https://pilot.test", "secret", body, captured=timezone.now()), "unbaselined")
        self.assertEqual([item.args[0].data for item in opener.open.call_args_list], [body] * 3)
        self.assertEqual([item.args[0] for item in delay.call_args_list], [2, 2])

    def test_collector_does_not_retry_a_scope_error(self):
        opener = Mock()
        opener.open.side_effect = HTTPError("https://pilot.test", 422, "Scope", {}, None)
        with patch("collector.__main__.build_opener", return_value=opener), self.assertRaises(ValueError):
            send("https://pilot.test", "secret", b"{}", captured=timezone.now())
        self.assertEqual(opener.open.call_count, 1)
