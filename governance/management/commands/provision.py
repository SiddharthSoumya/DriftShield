from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_slug
from django.db import transaction

from governance.models import Membership, Workspace
from governance.domain import audit


class Command(BaseCommand):
    help = "Provision a workspace or grant a user access. Prompts for new-user passwords."

    def add_arguments(self, parser):
        parser.add_argument("workspace")
        parser.add_argument("username")
        parser.add_argument("--name")
        parser.add_argument("--role", choices=Membership.Role.values, default="owner")
        parser.add_argument("--repo-limit", type=int)

    @transaction.atomic
    def handle(self, *args, **options):
        slug, username = options["workspace"], options["username"]
        name = options["name"] or slug
        limit = options["repo_limit"]
        try:
            validate_slug(slug)
            if not 1 <= len(slug) <= 64 or not 1 <= len(name) <= 100 or limit is not None and not 1 <= limit <= 1000:
                raise ValidationError("Check workspace slug, name, and repository limit (1–1,000).")
            user_model = get_user_model()
            user_model._meta.get_field("username").clean(username, None)
            user = user_model.objects.filter(username=username).first()
            if user is None:
                user = user_model(username=username)
                password = getpass("New user password: ")
                validate_password(password, user)
                user.set_password(password)
                user.save()
        except ValidationError as error:
            raise CommandError(" ".join(error.messages)) from error
        workspace, created = Workspace.objects.get_or_create(slug=slug, defaults={"name": name})
        if created and options["role"] != Membership.Role.OWNER:
            raise CommandError("The first workspace member must be an owner.")
        workspace = Workspace.objects.select_for_update().get(pk=workspace.pk)
        if limit is not None:
            workspace.repo_limit = limit
            workspace.save(update_fields=["repo_limit"])
        membership = Membership.objects.filter(workspace=workspace, user=user).first()
        if (membership and membership.role == Membership.Role.OWNER and options["role"] != Membership.Role.OWNER
                and not Membership.objects.filter(workspace=workspace, role=Membership.Role.OWNER).exclude(user=user).exists()):
            raise CommandError("Grant another user the owner role first.")
        Membership.objects.update_or_create(workspace=workspace, user=user, defaults={"role": options["role"]})
        audit(workspace, "server operator", "membership_granted", username=username, role=options["role"])
        self.stdout.write(self.style.SUCCESS(f"{username}: {options['role']} in {slug}"))
