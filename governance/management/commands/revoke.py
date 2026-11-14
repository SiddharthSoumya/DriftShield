from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from governance.domain import audit
from governance.models import Membership, Workspace
from infrastructure.models import Finding, RunnerEnrollment, Target
from infrastructure.pilot_intake import revoke_runner


class Command(BaseCommand):
    help = "Revoke a member's workspace access."

    def add_arguments(self, parser):
        parser.add_argument("workspace")
        parser.add_argument("username")

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            workspace = Workspace.objects.select_for_update().get(slug=options["workspace"])
            member = Membership.objects.select_for_update().get(workspace=workspace, user__username=options["username"])
        except (Workspace.DoesNotExist, Membership.DoesNotExist) as error:
            raise CommandError("Membership not found.") from error
        if member.role == "owner" and not Membership.objects.filter(workspace=workspace, role="owner").exclude(pk=member.pk).exists():
            raise CommandError("Grant another user the owner role first.")
        targets = Target.objects.select_for_update(of=("self",)).filter(
            workspace=workspace, profile=Target.Profile.EBS_PILOT, retired_at=None,
            runnerenrollment__primary_operator_id=member.user_id,
            runnerenrollment__status=RunnerEnrollment.Status.ACTIVE,
        ).order_by("pk")
        now = timezone.now()
        for target in targets:
            revoke_runner(target, "server operator", now)
        Finding.objects.filter(target__workspace=workspace, owner=member.user, closed_at__isnull=True).update(owner=None)
        member.delete()
        audit(workspace, "server operator", "membership_revoked", username=options["username"])
