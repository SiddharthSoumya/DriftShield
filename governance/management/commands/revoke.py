from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from governance.domain import audit
from governance.models import Membership, Workspace


class Command(BaseCommand):
    help = "Revoke a member's workspace access."

    def add_arguments(self, parser):
        parser.add_argument("workspace")
        parser.add_argument("username")

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            workspace = Workspace.objects.select_for_update().get(slug=options["workspace"])
            member = Membership.objects.get(workspace=workspace, user__username=options["username"])
        except (Workspace.DoesNotExist, Membership.DoesNotExist) as error:
            raise CommandError("Membership not found.") from error
        if member.role == "owner" and not Membership.objects.filter(workspace=workspace, role="owner").exclude(pk=member.pk).exists():
            raise CommandError("Grant another user the owner role first.")
        member.delete()
        audit(workspace, "server operator", "membership_revoked", username=options["username"])
