from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction

User = get_user_model()


class TelegramAuthService:

    @staticmethod
    def bind_admin_by_secret(telegram_id: int, provided_secret: str):
        configured_secret = (getattr(settings, "ADMIN_TELEGRAM_SECRET", None) or "").strip()
        submitted_secret = (provided_secret or "").strip()

        if not configured_secret:
            return None

        if submitted_secret != configured_secret:
            return None

        with transaction.atomic():
            existing = User.objects.select_for_update().filter(telegram_id=telegram_id).first()
            if existing:
                if existing.role != "admin":
                    return None
                return existing

            admin_user = (
                User.objects.select_for_update()
                .filter(role="admin", telegram_id__isnull=True)
                .order_by("id")
                .first()
            )

            if not admin_user:
                return None

            admin_user.telegram_id = telegram_id
            admin_user.save(update_fields=["telegram_id"])

            return admin_user
