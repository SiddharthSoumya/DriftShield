from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from driftshield.app import create_app
from driftshield.github import ScanError


OPERATOR = {"X-DriftShield-Request": "1"}
META = {
    "id": 10, "visibility": "public", "default_branch": "main",
    "archived": False, "has_issues": True, "has_wiki": False,
}


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "drift.db"
        self.metadata = META.copy()
        self.client = TestClient(create_app(self.database, "example-long-password", 0, self.collect))
        self.auth = ("admin", "example-long-password")
        self.addCleanup(self.client.close)
        self.base_time = datetime.now(timezone.utc) - timedelta(minutes=2)

    def collect(self, repository):
        if self.metadata is None:
            raise ScanError("Repository unavailable.")
        return self.metadata

    def post(self, url, payload=None):
        return self.client.post(url, json=payload or {}, auth=self.auth, headers=OPERATOR)

    def add(self, mode="public"):
        response = self.post("/api/monitors", {"repository": "owner/project", "mode": mode})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def detail(self, monitor=1):
        return self.client.get(f"/api/monitors/{monitor}", auth=self.auth).json()

    def push(self, token, offset, delivery, metadata=None):
        return self.client.post(
            "/api/ingest/1", headers={"Authorization": "Bearer " + token},
            json={
                "delivery_id": delivery, "repository": "owner/project",
                "observed_at": (self.base_time + timedelta(seconds=offset)).isoformat(),
                "metadata": META if metadata is None else metadata,
            },
        )

    def approve(self, detail):
        return self.post("/api/monitors/1/baseline", {"observed_at": detail["monitor"]["last_success"]})

    def test_public_baseline_drift_dedup_ack_and_restore(self):
        self.add()
        self.assertEqual(self.post("/api/monitors/1/scan").json()["status"], "unbaselined")
        self.assertEqual(self.approve(self.detail()).status_code, 200)
        self.metadata = {**META, "default_branch": "release"}
        for _ in range(2):
            self.assertEqual(self.post("/api/monitors/1/scan").json()["status"], "drift")
        incident, = self.detail()["incidents"]
        self.assertEqual(incident["occurrences"], 2)
        self.assertEqual(incident["expected"], "main")
        self.assertEqual(incident["actual"], "release")
        self.assertEqual(self.post(f"/api/monitors/1/incidents/{incident['id']}/ack").status_code, 200)
        self.metadata = META
        self.assertEqual(self.post("/api/monitors/1/scan").json()["status"], "clean")
        incident, = self.detail()["incidents"]
        self.assertEqual(incident["resolution"], "restored")
        self.assertIsNotNone(incident["acknowledged_at"])

    def test_private_runner_idempotency_stale_and_rotation(self):
        token = self.add("runner")["token"]
        self.assertEqual(self.push(token, 1, "run1").json()["status"], "unbaselined")
        self.assertEqual(self.approve(self.detail()).status_code, 200)
        self.assertEqual(self.push(token, 2, "run2", {**META, "visibility": "private"}).json()["status"], "drift")
        self.assertEqual(self.push(token, 2, "run2").json()["status"], "duplicate")
        self.assertEqual(self.push(token, 0, "late").json()["status"], "stale")
        self.assertEqual(len(self.detail()["scans"]), 2)
        new_token = self.post("/api/monitors/1/rotate").json()["token"]
        self.assertEqual(self.push(token, 3, "run3").status_code, 401)
        self.assertEqual(self.push(new_token, 3, "run3", {**META, "id": 11}).json()["status"], "error")
        self.assertEqual(self.detail()["monitor"]["latest"]["visibility"], "private")
        self.assertNotIn("token_hash", self.detail()["monitor"])

    def test_failure_does_not_close_incident_or_allow_stale_approval(self):
        self.add()
        self.post("/api/monitors/1/scan")
        self.approve(self.detail())
        self.metadata = {**META, "archived": True}
        self.post("/api/monitors/1/scan")
        old = self.detail()
        self.metadata = None
        self.assertEqual(self.post("/api/monitors/1/scan").json()["status"], "error")
        self.assertEqual(self.approve(old).status_code, 409)
        self.assertIsNone(self.detail()["incidents"][0]["closed_at"])
        self.metadata = {**META, "archived": True}
        self.post("/api/monitors/1/scan")
        self.assertEqual(self.approve(old).status_code, 409)
        self.assertEqual(self.approve(self.detail()).status_code, 200)
        detail = self.detail()
        self.assertEqual([row["version"] for row in detail["baselines"]], [2, 1])
        self.assertEqual(detail["incidents"][0]["resolution"], "baseline_accepted")

    def test_authentication_and_browser_request_header(self):
        self.assertEqual(self.client.get("/").status_code, 401)
        self.assertEqual(self.client.get("/api/monitors").status_code, 401)
        self.assertEqual(self.client.get("/", auth=self.auth).status_code, 200)
        self.assertEqual(self.client.get("/static/dashboard.js").status_code, 200)
        self.assertEqual(self.client.post("/api/monitors", auth=self.auth, json={}).status_code, 403)

    def test_persists_across_restart_and_rejects_oversized_ingest(self):
        token = self.add("runner")["token"]
        self.push(token, 1, "run1")
        with TestClient(create_app(self.database, "example-long-password", 0)) as fresh:
            response = fresh.get("/api/monitors/1", auth=self.auth)
            self.assertEqual(response.json()["monitor"]["latest"]["default_branch"], "main")
            response = fresh.post("/api/ingest/1", headers={"Authorization": "Bearer " + token}, content=b"x" * 8193)
            self.assertEqual(response.status_code, 413)
