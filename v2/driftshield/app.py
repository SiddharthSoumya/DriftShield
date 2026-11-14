from contextlib import asynccontextmanager
import asyncio
from datetime import datetime, timedelta, timezone
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import threading

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import github
from .engine import accept_baseline, ingest, parse_time, record_error, token_hash
from .store import Store, audit, now

STATIC = Path(__file__).with_name("static")
DELIVERY = re.compile(r"[A-Za-z0-9_.:-]{1,96}\Z")
logger = logging.getLogger(__name__)


class MonitorInput(BaseModel):
    repository: str
    mode: str


class BaselineInput(BaseModel):
    observed_at: str = Field(max_length=48)


def create_app(path, password="", scan_interval=900, collector=github.collect):
    store = Store(path)
    basic = HTTPBasic(auto_error=False)
    scan_lock = threading.Lock()

    def authorize(credentials: HTTPBasicCredentials | None = Depends(basic)):
        if not password:
            return
        if (
            credentials is None
            or credentials.username != "admin"
            or not hmac.compare_digest(credentials.password.encode(), password.encode())
        ):
            raise HTTPException(401, "Sign in as admin.", {"WWW-Authenticate": 'Basic realm="DriftShield"'})

    def operator_write(request: Request, _=Depends(authorize)):
        if request.headers.get("X-DriftShield-Request") != "1":
            raise HTTPException(403, "Missing DriftShield request header.")

    def get_monitor(db, monitor_id):
        monitor = db.execute("SELECT * FROM monitors WHERE id=?", (monitor_id,)).fetchone()
        if monitor is None:
            raise HTTPException(404, "Monitor not found.")
        return monitor

    def freshness_window(monitor):
        if monitor["mode"] == "public" and scan_interval:
            return max(2 * scan_interval + 60, 900)
        return 3600

    def run_public_scan(monitor_id):
        if not scan_lock.acquire(blocking=False):
            raise HTTPException(409, "A public scan is in progress. Try again shortly.")
        try:
            with store.connect() as db:
                monitor = get_monitor(db, monitor_id)
                if monitor["mode"] != "public":
                    raise HTTPException(409, "This monitor receives scans from a runner.")
                repository = monitor["repository"]
            started_at = now()
            try:
                metadata = collector(repository)
                with store.connect(write=True) as db:
                    status = ingest(db, get_monitor(db, monitor_id), metadata, started_at)
            except github.ScanError as error:
                with store.connect(write=True) as db:
                    status = record_error(db, get_monitor(db, monitor_id), str(error), started_at)
            return {"status": status}
        finally:
            scan_lock.release()

    def scan_due():
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=scan_interval)).isoformat(timespec="microseconds")
        with store.connect() as db:
            ids = [
                row["id"] for row in db.execute(
                    """SELECT id FROM monitors WHERE mode='public' AND
                       (last_attempt IS NULL OR last_attempt <= ?)
                       ORDER BY last_attempt LIMIT 10""", (cutoff,)
                )
            ]
        for monitor_id in ids:
            try:
                run_public_scan(monitor_id)
            except HTTPException as error:
                if error.status_code == 409:
                    break
                raise

    async def scheduler():
        while True:
            try:
                await run_in_threadpool(scan_due)
            except Exception:
                logger.exception("Scheduled scan failed")
            await asyncio.sleep(30)

    @asynccontextmanager
    async def lifespan(_):
        task = asyncio.create_task(scheduler()) if scan_interval else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(title="DriftShield", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    @app.middleware("http")
    async def response_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        return response

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", dependencies=[Depends(authorize)])
    def dashboard():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/monitors", dependencies=[Depends(authorize)])
    def monitors():
        with store.connect() as db:
            return [
                dict(row) for row in db.execute(
                    """SELECT id, repository, mode, baseline_version, last_attempt, last_success, last_error,
                        (SELECT count(*) FROM incidents i WHERE i.monitor_id=m.id AND i.closed_at IS NULL) AS open_count
                        FROM monitors m ORDER BY repository"""
                )
            ]

    @app.post("/api/monitors", status_code=201, dependencies=[Depends(operator_write)])
    def create_monitor(payload: MonitorInput):
        repository = payload.repository.strip()
        if not github.REPOSITORY.fullmatch(repository) or repository.split("/")[1] in (".", ".."):
            raise HTTPException(422, "Enter a GitHub repository in owner/name format.")
        if payload.mode not in ("public", "runner"):
            raise HTTPException(422, "Select a supported collector.")
        token = secrets.token_urlsafe(32) if payload.mode == "runner" else None
        with store.connect(write=True) as db:
            if db.execute("SELECT 1 FROM monitors WHERE repository=?", (repository,)).fetchone():
                raise HTTPException(409, "This repository already has a monitor.")
            cursor = db.execute(
                "INSERT INTO monitors(repository, mode, token_hash, created_at) VALUES (?, ?, ?, ?)",
                (repository, payload.mode, token_hash(token) if token else None, now()),
            )
            monitor_id = cursor.lastrowid
            audit(db, monitor_id, "monitor_created", repository)
        return {"id": monitor_id, "token": token}

    @app.get("/api/monitors/{monitor_id}", dependencies=[Depends(authorize)])
    def detail(monitor_id: int):
        with store.connect() as db:
            monitor = dict(get_monitor(db, monitor_id))
            del monitor["token_hash"]
            monitor["latest"] = json.loads(monitor["latest"]) if monitor["latest"] else None
            results = {}
            for key, query in {
                "baselines": "SELECT * FROM baselines WHERE monitor_id=? ORDER BY version DESC LIMIT 20",
                "scans": "SELECT * FROM scans WHERE monitor_id=? ORDER BY id DESC LIMIT 30",
                "incidents": "SELECT * FROM incidents WHERE monitor_id=? ORDER BY closed_at IS NOT NULL, id DESC LIMIT 100",
                "audit": "SELECT * FROM audit WHERE monitor_id=? ORDER BY id DESC LIMIT 30",
            }.items():
                results[key] = [dict(row) for row in db.execute(query, (monitor_id,))]
            for baseline in results["baselines"]:
                baseline["values"] = json.loads(baseline.pop("values_json"))
            for incident in results["incidents"]:
                incident["expected"] = json.loads(incident["expected"])
                incident["actual"] = json.loads(incident["actual"])
        return {
            "monitor": monitor, "fields": github.FIELDS,
            "stale_after_seconds": freshness_window(monitor),
            **results,
        }

    @app.post("/api/monitors/{monitor_id}/scan", dependencies=[Depends(operator_write)])
    def scan(monitor_id: int):
        return run_public_scan(monitor_id)

    @app.post("/api/monitors/{monitor_id}/baseline", dependencies=[Depends(operator_write)])
    def baseline(monitor_id: int, payload: BaselineInput):
        with store.connect(write=True) as db:
            try:
                monitor = get_monitor(db, monitor_id)
                cutoff = datetime.now(timezone.utc) - timedelta(seconds=freshness_window(monitor))
                if monitor["last_success"] and datetime.fromisoformat(monitor["last_success"]) < cutoff:
                    raise github.ScanError("The observation is overdue. Collect a fresh scan before approving.")
                accept_baseline(db, monitor, payload.observed_at)
            except github.ScanError as error:
                raise HTTPException(409, str(error)) from error
        return {"status": "accepted"}

    @app.post("/api/monitors/{monitor_id}/incidents/{incident_id}/ack", dependencies=[Depends(operator_write)])
    def acknowledge(monitor_id: int, incident_id: int):
        with store.connect(write=True) as db:
            result = db.execute(
                """UPDATE incidents SET acknowledged_at=? WHERE monitor_id=? AND id=?
                   AND closed_at IS NULL AND acknowledged_at IS NULL""",
                (now(), monitor_id, incident_id),
            )
            if result.rowcount != 1:
                raise HTTPException(409, "This incident is unavailable or has already been acknowledged.")
            audit(db, monitor_id, "incident_acknowledged", f"Incident {incident_id}")
        return {"status": "acknowledged"}

    @app.post("/api/monitors/{monitor_id}/rotate", dependencies=[Depends(operator_write)])
    def rotate(monitor_id: int):
        with store.connect(write=True) as db:
            monitor = get_monitor(db, monitor_id)
            if monitor["mode"] != "runner":
                raise HTTPException(409, "Only runner monitors use tokens.")
            token = secrets.token_urlsafe(32)
            db.execute("UPDATE monitors SET token_hash=? WHERE id=?", (token_hash(token), monitor_id))
            audit(db, monitor_id, "runner_token_rotated", "Runner credential rotated")
        return {"token": token}

    @app.post("/api/ingest/{monitor_id}")
    async def push_observation(monitor_id: int, request: Request):
        authorization = request.headers.get("Authorization", "")
        if not re.fullmatch(r"Bearer [A-Za-z0-9_-]{43}", authorization):
            raise HTTPException(401, "Invalid runner credential.")
        credential = token_hash(authorization[7:])

        def check_token(monitor):
            if not monitor or not hmac.compare_digest(monitor["token_hash"] or "", credential):
                raise HTTPException(401, "Invalid runner credential.")

        def authenticate_runner():
            with store.connect() as db:
                check_token(db.execute("SELECT token_hash FROM monitors WHERE id=?", (monitor_id,)).fetchone())

        await run_in_threadpool(authenticate_runner)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 8192:
                raise HTTPException(413, "Observation is too large.")
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise github.ScanError("Observation must be an object.")
            delivery = payload.get("delivery_id")
            if not isinstance(delivery, str) or not DELIVERY.fullmatch(delivery):
                raise github.ScanError("Observation requires a valid delivery ID.")
            observed_at = parse_time(payload.get("observed_at"))
        except (ValueError, github.ScanError) as error:
            message = str(error) if isinstance(error, github.ScanError) else "Invalid observation JSON."
            raise HTTPException(422, message) from error

        def persist():
            with store.connect(write=True) as db:
                monitor = get_monitor(db, monitor_id)
                check_token(monitor)
                repository = payload.get("repository")
                if not isinstance(repository, str) or repository.lower() != monitor["repository"].lower():
                    raise HTTPException(422, "Observation repository does not match this monitor.")
                if "error" in payload:
                    message = payload["error"]
                    if message not in ("repository_unavailable", "github_api_error"):
                        raise HTTPException(422, "Unknown runner failure code.")
                    return record_error(db, monitor, message.replace("_", " "), observed_at, delivery)
                try:
                    return ingest(db, monitor, payload.get("metadata"), observed_at, delivery)
                except github.ScanError as error:
                    return record_error(db, monitor, str(error), observed_at, delivery)

        return {"status": await run_in_threadpool(persist)}

    return app


def from_environment():
    path = Path(os.getenv("DRIFTSHIELD_DB", "data/driftshield.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    interval = int(os.getenv("DRIFTSHIELD_SCAN_SECONDS", "900"))
    if interval and interval < 300:
        raise ValueError("Set DRIFTSHIELD_SCAN_SECONDS to at least 300, or 0 to disable it.")
    return create_app(path, os.getenv("DRIFTSHIELD_PASSWORD", ""), interval)
