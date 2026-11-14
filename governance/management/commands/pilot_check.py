import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from governance.domain import audit
from governance.models import Workspace
from governance.pilot import health_payload, topology_issues
from infrastructure.models import EvidenceEvent, Target
from infrastructure.triage import record


class Command(BaseCommand):
    help = "Check the EBS pilot or record an approved activation while fenced."

    def add_arguments(self, parser):
        parser.add_argument("--workspace", required=True)
        modes = parser.add_mutually_exclusive_group()
        modes.add_argument("--activate", action="store_true")
        modes.add_argument("--bootstrap", action="store_true")
        parser.add_argument("--ticket")
        parser.add_argument("--operator")
        parser.add_argument("--recovery-ticket")
        parser.add_argument("--acceptance-ref")

    def handle(self, *args, **options):
        slug = options["workspace"]
        if options["activate"]:
            self._activate(slug, options)
            self.stdout.write("Pilot activation approval recorded. Intake and worker remain fenced until cutover.")
            return
        issues = topology_issues(slug, bootstrap=options["bootstrap"])
        if issues:
            raise CommandError("\n".join(issues))
        if options["bootstrap"]:
            self.stdout.write("Dedicated pilot bootstrap configuration checks passed.")
            return
        payload = health_payload(timezone.now())
        self.stdout.write(json.dumps(payload, cls=DjangoJSONEncoder))
        if payload["status"] != "healthy":
            raise CommandError("Pilot application health check failed.")

    @transaction.atomic
    def _activate(self, slug, options):
        if not settings.DS_PILOT_EBS or settings.DS_PILOT_STAGING or not settings.DS_PILOT_RECOVERY_FENCED:
            raise CommandError("Record production activation with the pilot fenced and staging mode disabled.")
        values = {}
        for name, maximum in (("ticket", 120), ("operator", 150), ("recovery_ticket", 120), ("acceptance_ref", 240)):
            value = (options[name] or "").strip()
            if not value or len(value) > maximum or not value.isprintable():
                raise CommandError(f"Provide --{name.replace('_', '-')} using a nonsecret reference.")
            values[name] = value
        if values["ticket"] != settings.DS_PILOT_ACCEPTANCE_TICKET:
            raise CommandError("The ticket must match DS_PILOT_ACCEPTANCE_TICKET.")
        workspace = Workspace.objects.select_for_update().filter(slug=slug).first()
        issues = topology_issues(slug, activate=True)
        if issues:
            raise CommandError("\n".join(issues))
        target = Target.objects.select_for_update().get(
            workspace=workspace, profile=Target.Profile.EBS_PILOT, retired_at=None,
        )
        if not target.token_hash:
            raise CommandError("The enrolled target must have an active credential.")
        drill = EvidenceEvent.objects.filter(
            target=target, kind="recovery_drill", detail__ticket=values["recovery_ticket"],
        ).order_by("-pk").first()
        reconciliation = EvidenceEvent.objects.filter(target=target, kind="recovery_reconciled").order_by("-pk").first()
        if (drill is None or drill.detail.get("passed") is not True or drill.detail.get("release") != settings.DS_RELEASE_SHA
                or (reconciliation and (drill.pk <= reconciliation.pk
                                        or reconciliation.detail.get("ticket") != values["recovery_ticket"]))):
            raise CommandError("Record a passing recovery drill for this release and current recovery ticket.")
        record(target, "pilot_activated", values["operator"], ticket=values["ticket"],
               acceptance_ref=values["acceptance_ref"], release=settings.DS_RELEASE_SHA,
               recovery_ticket=values["recovery_ticket"], recovery_drill=drill.pk)
        audit(workspace, values["operator"], "pilot_activated", target=target.pk, ticket=values["ticket"],
              release=settings.DS_RELEASE_SHA, recovery_drill=drill.pk)
