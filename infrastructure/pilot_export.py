"""Point-in-time, bounded owner evidence export."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import time
from zipfile import ZIP_DEFLATED, ZipFile

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.http import FileResponse

from .models import Baseline, CoverageGap, Delivery, EvidenceEvent, PilotApproval

MAX_BYTES = 100 * 1024 * 1024
MAX_SECONDS = 30


class ExportRangeRequired(Exception):
    pass


EXPORTS = {
    "events": (EvidenceEvent, "created_at", ("id", "created_at", "kind", "resource", "actor", "finding_id", "detail")),
    "deliveries": (Delivery, "received_at", (
        "id", "identifier", "received_at", "started_at", "captured_at", "status", "coverage", "failure_code",
        "projected_count", "snapshot", "payload_hash", "enrollment_id", "credential_epoch",
    )),
    "baselines": (Baseline, "created_at", ("id", "version", "created_at", "values", "actor", "reason", "pilot_approval_id")),
    "approvals": (PilotApproval, "requested_at", (
        "id", "finding_id", "kind", "state", "requester_id", "approver_id", "requested_at", "decided_at",
        "ticket", "decision_reason", "execution_ref", "execution_completed_at", "delivery_id",
        "base_version", "proposal", "proposal_hash",
    )),
    "coverage_gaps": (CoverageGap, "started_at", (
        "id", "started_at", "ended_at", "opened_reason", "ended_reason", "opening_delivery_id", "closing_delivery_id",
    )),
}


def _json(value):
    return json.dumps(value, cls=DjangoJSONEncoder, ensure_ascii=False, separators=(",", ":")).encode()


def build_bundle(target, start=None, end=None):
    started = time.monotonic()
    folder = tempfile.TemporaryDirectory(prefix="driftshield-evidence-")
    bundle = Path(folder.name) / "evidence.zip"
    manifest = {
        "schema": 1, "target_id": target.pk, "scope": target.scope, "release": settings.DS_RELEASE_SHA or None,
        "from": start, "to": end, "files": {},
    }
    total = 0

    def check_budget():
        if total > MAX_BYTES or time.monotonic() - started >= MAX_SECONDS:
            raise ExportRangeRequired

    try:
        with transaction.atomic():
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                    cursor.execute("SET LOCAL statement_timeout = '28s'")
                    cursor.execute("SELECT transaction_timestamp()")
                    manifest["as_of"] = cursor.fetchone()[0]
            else:
                manifest["as_of"] = datetime.now(timezone.utc)
            with ZipFile(bundle, "w", compression=ZIP_DEFLATED, compresslevel=1) as archive:
                for name, (model, timestamp, fields) in EXPORTS.items():
                    query = model.objects.filter(target=target)
                    if start is not None:
                        query = query.filter(**{f"{timestamp}__gte": start})
                    if end is not None:
                        query = query.filter(**{f"{timestamp}__lt": end})
                    filename = f"{name}.jsonl"
                    digest = hashlib.sha256()
                    count = 0
                    with archive.open(filename, "w") as output:
                        for row in query.order_by(timestamp, "pk").values(*fields).iterator(chunk_size=50):
                            encoded = _json(row) + b"\n"
                            total += len(encoded)
                            check_budget()
                            output.write(encoded)
                            digest.update(encoded)
                            count += 1
                    manifest["files"][filename] = {"rows": count, "sha256": digest.hexdigest()}
                    check_budget()
                archive.writestr("manifest.json", _json(manifest) + b"\n")
        check_budget()
        if bundle.stat().st_size > MAX_BYTES:
            raise ExportRangeRequired
        stream = bundle.open("rb")
        try:
            response = FileResponse(stream, as_attachment=True, filename=f"driftshield-target-{target.pk}.zip")
        except BaseException:
            stream.close()
            raise
        response._resource_closers.append(folder.cleanup)
        response["Cache-Control"] = "no-store"
        return response, manifest
    except BaseException:
        folder.cleanup()
        raise
