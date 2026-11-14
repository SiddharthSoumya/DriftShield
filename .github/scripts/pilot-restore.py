"""Exercise the restored pilot through authenticated application entry points."""
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "driftshield.settings")
django.setup()

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from governance.models import Membership, Notification, Workspace
from infrastructure.models import Baseline, EvidenceEvent, Finding, PilotApproval, Target
from infrastructure.pilot_intake import enroll_runner

SCOPE = "123456789012:ap-south-1:ebs"
RESOURCE = "ebs/vol-0123456789abcdef0"
HOST = os.environ.get("PILOT_TEST_HOST", "testserver")
STATE = Path(os.environ["PILOT_RESTORE_STATE"])


def actor_client(actor):
    client = Client(headers={"Host": HOST, "X-Forwarded-Proto": "https"})
    client.force_login(actor)
    return client


def submit(target, credential, size, *, client=None):
    now = timezone.now().isoformat()
    client = client or Client(headers={"Host": HOST, "X-Forwarded-Proto": "https"})
    return client.post(
        f"/api/v2/infrastructure/{target.pk}/observations/",
        {
            "schema": 2, "platform": "aws", "scope": SCOPE, "source_id": f"aws:{SCOPE}",
            "enrollment_id": credential["enrollment_id"], "credential_epoch": credential["credential_epoch"],
            "delivery": str(uuid4()), "started_at": now, "captured_at": now, "coverage": "complete",
            "projected_count": 1,
            "resources": {RESOURCE: {"size": size, "volume_type": "gp3", "encrypted": True,
                                     "iops": 3000, "throughput": 125}},
        },
        content_type="application/json", headers={"Authorization": f"Bearer {credential['token']}"},
    )


def seed():
    workspace = Workspace.objects.create(slug="restore-pilot", name="CI restore", recipient="ci@example.test")
    users = get_user_model()
    requester = users.objects.create_user("ci-requester")
    reviewer = users.objects.create_user("ci-reviewer")
    former = users.objects.create_user("ci-former")
    Membership.objects.create(workspace=workspace, user=requester, role="owner")
    Membership.objects.create(workspace=workspace, user=reviewer, role="owner")
    Membership.objects.create(workspace=workspace, user=former, role="viewer")
    target = Target.objects.create(workspace=workspace, name="regional-ebs", platform="aws", scope=SCOPE,
                                   profile=Target.Profile.EBS_PILOT)
    token, enrollment = enroll_runner(target, requester, "ci-runner",
                                      "arn:aws:sts::123456789012:assumed-role/DriftShield/",
                                      requester.pk, "CI-1", timezone.now() - timedelta(minutes=1))
    credential = {"token": token, "enrollment_id": str(enrollment.pk), "credential_epoch": enrollment.credential_epoch}
    response = submit(target, credential, 100)
    assert response.status_code == 200, response.content
    target.refresh_from_db()
    path = f"/w/restore-pilot/infra/{target.pk}/action/"
    response = actor_client(requester).post(path, {"action": "request_baseline", "revision": target.revision,
                                                   "version": target.version, "ticket": "CI-1"})
    assert response.status_code == 302, response.content
    approval = PilotApproval.objects.get(target=target, kind="baseline")
    response = actor_client(reviewer).post(path, {"action": "approve_baseline", "approval_id": approval.pk})
    assert response.status_code == 302, response.content
    assert Baseline.objects.get(target=target).pilot_approval_id == approval.pk
    Target.objects.filter(pk=target.pk).update(last_attempt=None)
    response = submit(target, credential, 120)
    assert response.status_code == 200 and response.json()["status"] == "drift", response.content
    pending = Notification.objects.filter(workspace=workspace, sent_at=None, failed_at=None).get()
    state = {"target": target.pk, "credential": credential, "approval": approval.pk, "pending": pending.pk}
    with STATE.open("x") as handle:
        json.dump(state, handle)


def reconcile():
    from io import StringIO

    state = json.loads(STATE.read_text())
    target = Target.objects.get(pk=state["target"])
    assert Baseline.objects.get(target=target).pilot_approval_id == state["approval"]
    assert Finding.objects.filter(target=target, closed_at=None, actual__size=120).exists()
    assert EvidenceEvent.objects.filter(target=target, kind="baseline_approved").exists()
    assert Notification.objects.get(pk=state["pending"]).failed_at is None
    response = submit(target, state["credential"], 100)
    assert response.status_code == 503 and response.json()["code"] == "recovery_fenced", response.content
    former = get_user_model().objects.get(username="ci-former")
    client = actor_client(former)
    path = f"/w/restore-pilot/infra/{target.pk}/"
    assert client.get(path).status_code == 200
    call_command("revoke", "restore-pilot", former.username)
    assert client.get(path).status_code == 404
    arguments = ("--workspace", "restore-pilot", "--target", str(target.pk), "--ticket", "CI-RESTORE",
                 "--operator", "ci-reviewer")
    call_command("pilot_recovery", "reconcile", *arguments)
    output = StringIO()
    with patch("infrastructure.management.commands.pilot_recovery.sys.stdin.isatty", return_value=True), patch(
        "infrastructure.management.commands.pilot_recovery.sys.stdout.isatty", return_value=True,
    ):
        call_command("pilot_recovery", "issue-runner", *arguments, stdout=output)
    state["new_credential"] = json.loads(output.getvalue())
    assert Notification.objects.get(pk=state["pending"]).disposition == "recovery_quarantine"
    STATE.write_text(json.dumps(state))


def verify():
    state = json.loads(STATE.read_text())
    target = Target.objects.get(pk=state["target"])
    assert submit(target, state["credential"], 100).status_code == 401
    response = submit(target, state["new_credential"], 100)
    assert response.status_code == 200 and response.json()["status"] == "match", response.content
    restored = Finding.objects.get(target=target, resolution="observed_restoration")
    assert restored.closure_event_id and restored.closure_delivery_id
    assert restored.closure_delivery.credential_epoch == state["new_credential"]["credential_epoch"]
    assert Baseline.objects.get(target=target).pilot_approval_id == state["approval"]
    assert Notification.objects.get(pk=state["pending"]).disposition == "recovery_quarantine"
    Target.objects.filter(pk=target.pk).update(last_attempt=None)
    response = submit(target, state["new_credential"], 125)
    assert response.status_code == 200 and response.json()["status"] == "drift", response.content
    assert Finding.objects.filter(target=target, closed_at=None, actual__size=125).exists()
    assert Notification.objects.filter(workspace_id=target.workspace_id, failed_at=None, sent_at=None).exists()
    owner = get_user_model().objects.get(username="ci-reviewer")
    response = actor_client(owner).get(f"/w/restore-pilot/infra/{target.pk}/evidence-bundle/")
    assert response.status_code == 200
    try:
        with ZipFile(BytesIO(b"".join(response.streaming_content))) as bundle:
            manifest = json.loads(bundle.read("manifest.json"))
            for name, metadata in manifest["files"].items():
                content = bundle.read(name)
                assert sha256(content).hexdigest() == metadata["sha256"]
                assert len(content.splitlines()) == metadata["rows"]
            events = bundle.read("events.jsonl")
            assert b"recovery_reconciled" in events and b"observed_restoration" in events
    finally:
        response.close()
    print("Populated pilot restore, intake fence, access reconciliation, fresh drift and evidence export passed.")


{"seed": seed, "reconcile": reconcile, "verify": verify}[sys.argv[1]]()
