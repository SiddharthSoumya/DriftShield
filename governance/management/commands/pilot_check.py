import json

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from governance.pilot import health_payload, topology_issues


class Command(BaseCommand):
    help = "Check the dedicated EBS deployment configuration or report application health."

    def add_arguments(self, parser):
        parser.add_argument("--workspace", required=True)
        modes = parser.add_mutually_exclusive_group()
        modes.add_argument("--activate", action="store_true")
        modes.add_argument("--bootstrap", action="store_true")

    def handle(self, *args, **options):
        slug = options["workspace"]
        issues = topology_issues(slug, activate=options["activate"], bootstrap=options["bootstrap"])
        if issues:
            raise CommandError("\n".join(issues))
        if options["activate"] or options["bootstrap"]:
            self.stdout.write("Dedicated pilot configuration checks passed.")
            return
        payload = health_payload(timezone.now())
        self.stdout.write(json.dumps(payload, cls=DjangoJSONEncoder))
        if payload["status"] != "healthy":
            raise CommandError("Pilot application health check failed.")
