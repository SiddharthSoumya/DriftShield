from django.conf import settings
from pathlib import Path

import httpx
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from governance.domain import audit
from governance.github import CollectionError, GitHub, cipher, signing_key
from governance.models import Integration, Monitor, Workspace
from django.utils import timezone


class Command(BaseCommand):
    help = "Bind a verified GitHub App installation to a workspace, or rotate its key."

    def add_arguments(self, parser):
        parser.add_argument("workspace")
        parser.add_argument("app_id")
        parser.add_argument("installation_id", type=int)
        parser.add_argument("private_key", type=Path)

    def handle(self, *args, **options):
        if settings.DS_PILOT_EBS:
            raise CommandError("GitHub integration is unavailable in the dedicated EBS pilot.")
        try:
            workspace = Workspace.objects.get(slug=options["workspace"])
            app_id, installation_id = options["app_id"], options["installation_id"]
            if not 1 <= len(app_id) <= 64 or not app_id.isalnum() or not 0 < installation_id < 2**63:
                raise CommandError("Enter a valid GitHub App client ID and installation ID.")
            with options["private_key"].open("rb") as file:
                pem = file.read(32769)
            if len(pem) > 32768:
                raise CommandError("Private key file is too large.")
            encryption = cipher()
            with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
                account, _ = GitHub(client).installation(app_id, signing_key(pem), installation_id)
            with transaction.atomic():
                workspace = Workspace.objects.select_for_update().get(pk=workspace.pk)
                previous = Integration.objects.filter(workspace=workspace).first()
                if previous and previous.installation_id != installation_id:
                    raise CommandError("This workspace is bound to another installation. Use a new workspace.")
                Integration.objects.update_or_create(workspace=workspace, defaults={
                    "app_id": app_id, "installation_id": installation_id,
                    "encrypted_key": encryption.encrypt(pem), "account": account,
                })
                Monitor.objects.filter(workspace=workspace, app_managed=True).update(
                    next_scan=timezone.now(), lease=None, lease_until=None,
                )
                audit(workspace, "server operator", "github_connected", account=account)
        except (Workspace.DoesNotExist, CollectionError, IntegrityError, OSError) as error:
            raise CommandError("Could not connect the workspace. Check the installation, key, and workspace.") from error
        self.stdout.write(self.style.SUCCESS(f"Verified installation for {account}. Import repositories in the dashboard."))
