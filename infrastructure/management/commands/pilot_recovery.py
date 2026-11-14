"""Record, reconcile and re-enroll a fenced pilot after recovery."""
import json
import re
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from collector.contract import pilot_time
from governance.domain import audit
from governance.models import Notification, Workspace
from infrastructure import pilot_intake, pilot_review
from infrastructure.domain import Conflict
from infrastructure.models import EvidenceEvent, Target
from infrastructure.triage import record

SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")


class Command(BaseCommand):
    help = "Reconcile a restored pilot and record the measured recovery evidence."

    def add_arguments(self, parser):
        actions = parser.add_subparsers(dest="action", required=True)
        for name in ("record", "reconcile", "issue-runner"):
            action = actions.add_parser(name)
            action.add_argument("--workspace", required=True)
            action.add_argument("--target", required=True, type=int)
            action.add_argument("--ticket", required=True)
            action.add_argument("--operator", required=True)
            if name == "reconcile":
                action.add_argument("--dry-run", action="store_true")
            if name == "record":
                action.add_argument("--started", required=True)
                action.add_argument("--ended", required=True)
                action.add_argument("--recovery-point", required=True)
                action.add_argument("--backup-ref", required=True)
                action.add_argument("--checksum", required=True)
                action.add_argument("--release", required=True)
                action.add_argument("--rto-target-seconds", required=True, type=int)
                action.add_argument("--rpo-target-seconds", required=True, type=int)
                action.add_argument("--loss-seconds", required=True, type=int)
                action.add_argument("--checks", required=True, type=json.loads)

    def handle(self, *args, **options):
        with transaction.atomic():
            result = self._execute(**options)
        if result is False:
            raise CommandError("Recovery drill failed the recorded acceptance criteria.")

    def _execute(self, **options):
        if not settings.DS_PILOT_EBS or options["workspace"] != settings.DS_PILOT_WORKSPACE:
            raise CommandError("Use the configured dedicated pilot workspace.")
        action = options["action"]
        if action != "record" and not settings.DS_PILOT_RECOVERY_FENCED:
            raise CommandError("Fence the restored deployment before changing recovery state.")
        try:
            ticket = pilot_review.text_field(options["ticket"], 120, "recovery ticket")
            operator = pilot_review.text_field(options["operator"], 150, "named operator")
            workspace = Workspace.objects.select_for_update().get(slug=options["workspace"])
            target = Target.objects.select_for_update().get(
                pk=options["target"], workspace=workspace, profile=Target.Profile.EBS_PILOT, retired_at=None,
            )
            now = timezone.now()
            if action == "record":
                return self._record(target, operator, ticket, now, options)
            prior = EvidenceEvent.objects.filter(target=target, kind="recovery_reconciled", detail__ticket=ticket).exists()
            if action == "issue-runner":
                if not prior:
                    raise CommandError("Reconcile this recovery ticket first.")
                if not (sys.stdin.isatty() and sys.stdout.isatty()):
                    raise CommandError("Issue runner credentials from a local terminal.")
                token, enrollment = pilot_intake.rotate_runner(target, operator, now)
                transaction.on_commit(lambda: self.stdout.write(json.dumps({
                    "token": token, "enrollment_id": str(enrollment.pk), "credential_epoch": enrollment.credential_epoch,
                })))
                return
            pending = Notification.objects.filter(workspace=workspace, sent_at=None, failed_at=None)
            count = pending.count()
            if options["dry_run"]:
                self.stdout.write(f"{count} pending notifications will be quarantined.")
            elif prior:
                self.stdout.write("Recovery ticket already reconciled.")
            else:
                pilot_intake.revoke_runner(target, operator, now)
                pilot_intake.invalidate_epoch(target, now)
                target.last_complete_delivery = None
                target.save(update_fields=["last_complete_delivery"])
                pilot_intake.open_gap(target, now, "recovery", actor=operator)
                count = pending.update(failed_at=now, disposition=Notification.Disposition.RECOVERY_QUARANTINE,
                                       lease=None, lease_until=None)
                record(target, "recovery_reconciled", operator, ticket=ticket, quarantined=count)
                audit(workspace, operator, "pilot_recovery_reconciled", target=target.pk, ticket=ticket, quarantined=count)
        except (Conflict, Workspace.DoesNotExist, Target.DoesNotExist, ValueError, OverflowError) as error:
            raise CommandError(str(error)) from error

    def _record(self, target, operator, ticket, now, options):
        started, ended, point = (pilot_time(options[key]) for key in ("started", "ended", "recovery_point"))
        checks = options["checks"]
        names = {"access", "credentials", "evidence", "collector", "mailbox", "rollback"}
        if (not point <= started <= ended <= now or not isinstance(checks, dict) or set(checks) != names
                or any(type(value) is not bool for value in checks.values())
                or not SHA256.fullmatch(options["checksum"]) or not SHA.fullmatch(options["release"])
                or min(options["rto_target_seconds"], options["rpo_target_seconds"], options["loss_seconds"]) < 0):
            raise CommandError("Invalid recovery timing, hashes, objectives or check results.")
        elapsed = (ended - started).total_seconds()
        loss = options["loss_seconds"]
        passed = all(checks.values()) and elapsed <= options["rto_target_seconds"] and loss <= options["rpo_target_seconds"]
        backup_ref = pilot_review.text_field(options["backup_ref"], 240, "nonsecret backup reference")
        if "://" in backup_ref:
            raise CommandError("Use a nonsecret identifier for the backup.")
        record(target, "recovery_drill", operator, ticket=ticket, started=started.isoformat(), ended=ended.isoformat(),
               recovery_point=point.isoformat(), release=options["release"], backup_ref=backup_ref,
               checksum=options["checksum"], rto_seconds=elapsed, rpo_seconds=loss,
               rto_target_seconds=options["rto_target_seconds"], rpo_target_seconds=options["rpo_target_seconds"],
               checks=checks, passed=passed)
        audit(target.workspace, operator, "pilot_recovery_drill", target=target.pk, ticket=ticket, passed=passed)
        if passed:
            self.stdout.write("pass")
        return passed
