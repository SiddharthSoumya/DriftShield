import json
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from collector.contract import EBS_SCOPE
from governance.models import Membership, Workspace
from infrastructure.domain import Conflict
from infrastructure.models import Target
from infrastructure.pilot_intake import enroll_runner


class Command(BaseCommand):
    help = "Bootstrap the first EBS target and enrollment while the customer pilot is fenced."

    def add_arguments(self, parser):
        parser.add_argument("--workspace", required=True)
        parser.add_argument("--scope", required=True)
        parser.add_argument("--role-prefix", required=True)
        parser.add_argument("--owner", required=True)
        parser.add_argument("--runner-operator", required=True)
        parser.add_argument("--runner-label", required=True)
        parser.add_argument("--ticket", required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        if (not settings.DS_PILOT_EBS or not settings.DS_PILOT_RECOVERY_FENCED
                or options["workspace"] != settings.DS_PILOT_WORKSPACE):
            raise CommandError("Use the configured dedicated workspace with the pilot fenced.")
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise CommandError("Issue enrollment credentials from a local terminal.")
        match = EBS_SCOPE.fullmatch(options["scope"])
        if match is None or len(match["region"]) > 32:
            raise CommandError("Use a canonical ACCOUNT:REGION:ebs scope.")
        try:
            workspace = Workspace.objects.select_for_update().get(slug=options["workspace"])
            actor = Membership.objects.select_related("user").get(
                workspace=workspace, user__username=options["owner"], user__is_active=True, role="owner",
            ).user
            operator = Membership.objects.get(
                workspace=workspace, user__username=options["runner_operator"],
                user__is_active=True, role__in=("owner", "operator"),
            )
        except (Workspace.DoesNotExist, Membership.DoesNotExist) as error:
            raise CommandError("Provision the named owner and runner operator first.") from error
        if Target.objects.filter(workspace=workspace, retired_at=None).exists():
            raise CommandError("An active target already exists.")
        target = Target.objects.create(workspace=workspace, name=f"ebs-{match['region']}", platform="aws",
                                       scope=options["scope"], profile=Target.Profile.EBS_PILOT)
        try:
            token, enrollment = enroll_runner(target, actor, options["runner_label"].strip(),
                                              options["role_prefix"].strip(), operator.user_id,
                                              options["ticket"].strip(), timezone.now())
        except Conflict as error:
            raise CommandError(str(error)) from error
        transaction.on_commit(lambda: self.stdout.write(json.dumps({
            "target": target.pk, "token": token, "enrollment_id": str(enrollment.pk),
            "credential_epoch": enrollment.credential_epoch,
        })))
