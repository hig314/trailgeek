from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from core.roles import ROLE_GROUPS


class Command(BaseCommand):
    help = "Create the role groups and grant their model permissions (idempotent)."

    def handle(self, *args, **opts):
        for name, grants in ROLE_GROUPS.items():
            group, created = Group.objects.get_or_create(name=name)
            for app_label, model, actions in grants:
                for action in actions:
                    perm = Permission.objects.get(
                        content_type__app_label=app_label,
                        codename=f"{action}_{model}",
                    )
                    group.permissions.add(perm)
            self.stdout.write(f"{'created' if created else 'ok     '} {name}")
