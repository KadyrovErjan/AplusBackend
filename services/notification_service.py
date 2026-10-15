from django.utils import timezone
from notifications.models import Notification
from django.contrib.auth import get_user_model
from django.db.models import Q

User = get_user_model()


def _notification_recipients():
    """
    Приоритет: пользователи с ролью admin.
    Фолбэк: superuser, если role не заполнена как admin.
    """
    return User.objects.filter(Q(role="admin") | Q(is_superuser=True)).distinct()


def create_low_fuel_notification(vehicle):
    fuel_liters = round(float(vehicle.fuel or 0), 1)
    admins = _notification_recipients()

    if not admins.exists():
        Notification.objects.create(
            user=None,
            type="low_fuel",
            title="Низкий уровень топлива",
            message=f"{vehicle.name} — {fuel_liters} л. Требуется заправка.",
            created_at=timezone.now()
        )
        return

    for admin in admins:
        Notification.objects.create(
            user=admin,
            type="low_fuel",
            title="Низкий уровень топлива",
            message=f"{vehicle.name} — {fuel_liters} л. Требуется заправка.",
            created_at=timezone.now()
        )

def create_oil_service_notification(vehicle):
    admins = _notification_recipients()

    if not admins.exists():
        Notification.objects.create(
            user=None,
            type="service",
            title="Требуется замена масла",
            message=f"{vehicle.name} — осталось {vehicle.to_remaining} км до замены масла",
            created_at=timezone.now()
        )
        return

    for admin in admins:
        Notification.objects.create(
            user=admin,
            type="service",
            title="Требуется замена масла",
            message=f"{vehicle.name} — осталось {vehicle.to_remaining} км до замены масла",
            created_at=timezone.now()
        )


def send_notification(type_code: str, title: str, message: str, vehicle=None):
    """
    Создает уведомление для всех админов.
    """
    admins = _notification_recipients()

    if not admins.exists():
        Notification.objects.create(
            user=None,
            vehicle=vehicle,
            type=type_code,
            title=title,
            message=message
        )
        return

    for admin in admins:
        Notification.objects.create(
            user=admin,
            vehicle=vehicle,
            type=type_code,
            title=title,
            message=message
        )
