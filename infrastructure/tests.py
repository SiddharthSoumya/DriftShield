from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
import json
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connection, close_old_connections
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone

from collector import adapters
from collector.__main__ import main
from collector.contract import MAX_BODY, decode, validate_values
from governance.models import Audit, Membership, Notification, Workspace
from .domain import issue_token, maintenance
from .models import Baseline, Delivery, Finding, Target


class TargetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.workspace = Workspace.objects.create(slug="alpha", name="Alpha", recipient="test@example.org")
        cls.other = Workspace.objects.create(slug="beta", name="Beta")
        cls.owner = get_user_model().objects.create_user("owner")
        cls.viewer = get_user_model().objects.create_user("viewer")
        Membership.objects.create(workspace=cls.workspace, user=cls.owner, role="owner")
        Membership.objects.create(workspace=cls.workspace, user=cls.viewer, role="viewer")
        cls.token, token_hash = issue_token()
        cls.target = Target.objects.create(workspace=cls.workspace, name="production", platform="aws",
                                           scope="123456789012:us-east-1", token_hash=token_hash)

    def setUp(self):
        self.client.force_login(self.owner)
        self.ingest = f"/api/v1/infrastructure/{self.target.pk}/observations/"
        self.detail = f"/w/alpha/infra/{self.target.pk}/"
        self.now = timezone.now()

    def payload(self, values=None, seconds=0, **changes):
        return {
            "schema": 1, "platform": self.target.platform, "scope": self.target.scope,
            "delivery": str(uuid4()), "captured_at": (self.now + timedelta(seconds=seconds)).isoformat(),
            "resources": values if values is not None else {"ebs/vol-1": {"size": 50}}, **changes,
        }

    def post(self, payload, token=None):
        return self.client.post(self.ingest, payload, content_type="application/json",
                                HTTP_AUTHORIZATION=f"Bearer {token or self.token}")

    def action(self, name, **fields):
        self.target.refresh_from_db()
        return self.client.post(self.detail + "action/", {
            "action": name, "revision": self.target.revision, "version": self.target.version, "reason": "CHG-12", **fields,
        }, follow=True)

    def test_drift_repeat_review_and_verification(self):
        self.assertEqual(self.post(self.payload()).json()["status"], "unbaselined")
        self.assertContains(self.action("approve"), "Version 1")
        changed = self.payload({"ebs/vol-1": {"size": 100}}, seconds=1)
        self.assertEqual(self.post(changed).json()["status"], "drift")
        self.assertEqual(self.post(changed).json()["status"], "drift")
        self.post(self.payload({"ebs/vol-1": {"size": 100}}, seconds=2))
        finding = Finding.objects.get()
        self.assertEqual((finding.count, finding.priority), (2, "high"))
        self.assertEqual(Notification.objects.count(), 1)
        self.assertContains(self.action("review", finding=finding.pk), "CHG-12")
        self.assertEqual(self.post(self.payload(seconds=3)).json()["status"], "match")
        finding.refresh_from_db()
        self.assertEqual(finding.resolution, "verified")
        self.assertContains(self.client.get("/w/alpha/infra/evidence.csv"), "CHG-12")

    def test_accept_selected_and_reject_stale_approval(self):
        self.post(self.payload())
        self.action("approve")
        self.post(self.payload({"rds/db-1": {"instance_class": "db.r7g.large"}}, seconds=1))
        added = Finding.objects.get(resource="rds/db-1")
        response = self.action("approve", finding=added.pk, revision=1)
        self.assertContains(response, "Refresh the page")
        self.action("approve", finding=added.pk)
        self.assertEqual(Baseline.objects.latest("version").values, {
            "ebs/vol-1": {"size": 50}, "rds/db-1": {"instance_class": "db.r7g.large"},
        })
        self.assertTrue(Finding.objects.filter(resource="ebs/vol-1", closed_at__isnull=True).exists())

    def test_errors_preserve_state_and_out_of_order_is_rejected(self):
        self.post(self.payload())
        self.action("approve")
        data = self.payload(seconds=3)
        del data["resources"]
        data["error"] = "collection_failed"
        self.assertEqual(self.post(data).json()["status"], "error")
        self.assertEqual(self.post(self.payload(seconds=1)).status_code, 409)
        self.target.refresh_from_db()
        self.assertEqual(self.target.latest, {"ebs/vol-1": {"size": 50}})
        self.assertEqual(self.target.revision, 1)

    def test_access_scope_rotation_body_and_replay(self):
        self.assertEqual(self.post(self.payload(scope="other")).status_code, 422)
        self.assertEqual(self.post(self.payload(seconds=-1000)).status_code, 422)
        self.assertEqual(self.post(self.payload(), token="x" * 43).status_code, 401)
        self.assertEqual(self.client.get(f"/w/beta/infra/{self.target.pk}/").status_code, 404)
        self.client.force_login(self.viewer)
        self.assertEqual(self.action("rotate").status_code, 403)
        self.client.force_login(self.owner)
        self.assertContains(self.action("rotate"), "Collector token")
        self.assertEqual(self.post(self.payload()).status_code, 401)
        new_token, hashed = issue_token()
        Target.objects.filter(pk=self.target.pk).update(token_hash=hashed)
        self.assertEqual(self.post("x" * (MAX_BODY + 1), token=new_token).status_code, 413)
        self.assertEqual(self.post('{"schema":1,"schema":1}', token=new_token).status_code, 422)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(csrf_client.post(self.detail + "action/", {"action": "rotate"}).status_code, 403)

    def test_terraform_observations_cannot_approve_away_drift(self):
        self.target.platform = "terraform"
        self.target.scope = "prod"
        self.target.save()
        data = self.payload({"aws_instance/opaque": {"action": "update", "resource_type": "aws_instance"}})
        self.assertEqual(self.post(data).json()["status"], "drift")
        self.assertContains(self.action("approve"), "refreshed state")
        self.assertFalse(Baseline.objects.exists())
        self.assertEqual(self.post(self.payload({}, seconds=1)).json()["status"], "unbaselined")
        self.action("approve")
        self.assertEqual(Baseline.objects.get().values, {})

    def test_overdue_alert_is_deduplicated(self):
        Target.objects.update(created_at=self.now - timedelta(days=1))
        for _ in range(2):
            maintenance(self.workspace, self.now - timedelta(days=90), self.now)
        self.assertEqual(Notification.objects.filter(subject__contains="overdue").count(), 1)

    def test_add_rejects_invalid_aws_scope_and_audits_good_scope(self):
        url = "/w/alpha/infra/add/"
        request = {"name": "other-region", "platform": "aws", "scope": "not-an-account", "interval": 30}
        self.client.post(url, request)
        self.assertEqual(Target.objects.count(), 1)
        response = self.client.post(url, {**request, "scope": "123456789012:eu-west-1"})
        self.assertContains(response, "Collector token")
        self.assertTrue(Audit.objects.filter(action="infra_target_added").exists())
        self.assertNotContains(self.client.get("/w/alpha/infra/"), Target.objects.get(name="other-region").token_hash)


class AdapterTests(TestCase):
    def test_custom_collector_cli_projects_and_submits_envelope(self):
        with TemporaryDirectory() as directory:
            snapshot = Path(directory) / "snapshot.json"
            snapshot.write_text('{"billing/api":{"replicas":2}}')
            args = ["collector", "snapshot", "--scope", "prod", "--file", str(snapshot)]
            with patch("sys.argv", args + ["--preview"]), patch("sys.stdout", new_callable=StringIO) as output:
                self.assertEqual(main(), 0)
                self.assertEqual(json.loads(output.getvalue())["resources"], {"billing/api": {"replicas": 2}})
            with patch("sys.argv", args + ["--endpoint", "https://example.org/observe"]), patch.dict(
                "os.environ", {"DRIFTSHIELD_TOKEN": "fake-collector-token"},
            ), patch("collector.__main__.send", return_value="match") as sender, patch("sys.stdout", new_callable=StringIO):
                self.assertEqual(main(), 0)
                self.assertEqual(json.loads(sender.call_args.args[2])["scope"], "prod")

    def test_scalars_are_type_sensitive(self):
        from .domain import same

        self.assertFalse(same({"enabled": True}, {"enabled": 1}))
        self.assertTrue(same({"replicas": 3, "enabled": True}, {"enabled": True, "replicas": 3}))

    def test_refresh_only_plan_projects_only_action(self):
        document = {"format_version": "1.2", "configuration": {}, "planned_values": {},
                    "resource_drift": [{"type": "aws_instance", "address": 'aws_instance.x["customer-secret"]',
                                        "change": {"actions": ["update"], "before": {"value": "very-secret"}}}]}
        output = adapters.terraform(document)
        self.assertNotIn("secret", json.dumps(output))
        self.assertEqual(list(output.values()), [{"action": "update", "resource_type": "aws_instance"}])
        with self.assertRaises(ValueError):
            adapters.terraform({**document, "resource_changes": [{"change": {"actions": ["delete"]}}]})
        with self.assertRaises(ValueError):
            adapters.terraform({**document, "complete": False})

    def test_docker_excludes_environment_and_ignores_port_order(self):
        container = {"Name": "/app", "Image": "sha256:test", "Config": {"Env": ["PASSWORD=secret"]},
                     "HostConfig": {"Privileged": False, "ReadonlyRootfs": True, "Memory": 512, "NanoCpus": 1,
                                    "NetworkMode": "bridge", "RestartPolicy": {"Name": "always"},
                                    "PortBindings": {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "80"},
                                                                {"HostIp": "127.0.0.1", "HostPort": "81"}]}}}
        left = adapters.docker([container])
        container["HostConfig"]["PortBindings"]["80/tcp"].reverse()
        self.assertEqual(left, adapters.docker([container]))
        self.assertNotIn("secret", json.dumps(left))
        self.assertEqual(validate_values(left), left)

    def test_kubernetes_ignores_volatile_and_secret_fields(self):
        item = {"kind": "Deployment", "metadata": {"namespace": "prod", "name": "api", "resourceVersion": "1"},
                "spec": {"replicas": 2, "template": {"spec": {"containers": [
                    {"name": "api", "image": "api:1", "env": [{"name": "PASSWORD", "value": "secret"}]},
                ]}}}, "status": {"readyReplicas": 0}}
        left = adapters.kubernetes([{"items": [item]}], "prod")
        item["status"]["readyReplicas"] = 2
        item["metadata"]["resourceVersion"] = "2"
        self.assertEqual(left, adapters.kubernetes([{"items": [item]}], "prod"))
        self.assertNotIn("secret", json.dumps(left))
        item["spec"]["replicas"] = 3
        self.assertNotEqual(left, adapters.kubernetes([{"items": [item]}], "prod"))

    def test_aws_cost_fields_and_partial_inventory(self):
        values = {"ec2": {"Reservations": [{"Instances": [
            {"InstanceId": "i-test", "State": {"Name": "running"}, "InstanceType": "m7g.large"},
        ]}]}, "ebs": {"Volumes": [{"VolumeId": "vol-test", "State": "available", "Size": 100,
                                     "VolumeType": "gp3", "Encrypted": True}]}, "nat": {"NatGateways": []},
                  "rds": {"DBInstances": []}, "asg": {"AutoScalingGroups": []},
                  "elb": {"LoadBalancers": []}, "eip": {"Addresses": []}}
        result = adapters.aws(values)
        self.assertEqual(result["ebs/vol-test"]["size"], 100)
        self.assertEqual(result["ec2/i-test"]["instance_type"], "m7g.large")
        values["ebs"]["NextToken"] = "not-finished"
        with self.assertRaises(ValueError):
            adapters.aws(values)

    def test_json_contract_rejects_invalid_values(self):
        for data in ('{"foo":NaN}', '{"foo":1,"foo":2}'):
            with self.assertRaises(ValueError):
                decode(data)
        with self.assertRaises(ValueError):
            validate_values({"resource": {"password": {"raw": "value"}}})


class ConcurrentIngestionTests(TransactionTestCase):
    def test_postgres_duplicate_deliveries_commit_once(self):
        if connection.vendor != "postgresql":
            self.skipTest("Requires PostgreSQL row locks.")
        workspace = Workspace.objects.create(slug="race", name="Race")
        token, hashed = issue_token()
        target = Target.objects.create(workspace=workspace, name="race", platform="snapshot", scope="race", token_hash=hashed)
        payload = {"schema": 1, "platform": "snapshot", "scope": "race", "delivery": str(uuid4()),
                   "captured_at": timezone.now().isoformat(), "resources": {"r": {"enabled": True}}}

        def send(_):
            try:
                return Client().post(f"/api/v1/infrastructure/{target.pk}/observations/", payload,
                                     content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}").status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(send, range(4))), [200] * 4)
        target.refresh_from_db()
        self.assertEqual(target.revision, 1)
        self.assertEqual(Delivery.objects.count(), 1)
