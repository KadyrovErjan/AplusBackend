from django.core.management.base import BaseCommand
from users.models import CustomUser


class Command(BaseCommand):
    help = "List users whose selfie file is missing from storage."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear selfie field for users whose file is missing.",
        )

    def handle(self, *args, **options):
        clear = options.get("clear", False)
        qs = (
            CustomUser.objects
            .exclude(selfie="")
            .exclude(selfie__isnull=True)
            .only("id", "email", "telegram_id", "selfie")
        )

        total = qs.count()
        missing = 0
        cleared = 0

        for user in qs.iterator():
            selfie = user.selfie
            if not selfie:
                continue

            if not selfie.storage.exists(selfie.name):
                missing += 1
                self.stdout.write(
                    f"id={user.id} telegram_id={user.telegram_id} "
                    f"email={user.email} selfie={selfie.name}"
                )
                if clear:
                    user.selfie = None
                    user.save(update_fields=["selfie"])
                    cleared += 1

        if clear:
            self.stdout.write(f"Checked: {total}. Missing: {missing}. Cleared: {cleared}.")
        else:
            self.stdout.write(f"Checked: {total}. Missing: {missing}.")
