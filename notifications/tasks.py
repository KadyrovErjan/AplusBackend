from celery import shared_task
from django.utils import timezone

from vehicles.models import Vehicle
from notifications.models import Notification
from django.core.cache import cache
from notifications.domain_events import collect_system_events
from services.notification_service import (
    create_low_fuel_notification,
    create_oil_service_notification,
    send_notification,
)

LOCK_KEY = "lock:system_events"
LOW_FUEL_THRESHOLD_LITERS = 20.0


def _vehicle_label(vehicle: Vehicle) -> str:
    name = (vehicle.name or "").strip()
    plate = (vehicle.plate_number or "").strip()

    if name and plate and name != plate:
        return f"{name} ({plate})"
    return name or plate or "Транспорт"


@shared_task(bind=True)
def check_system_events(self):
    if not cache.add(LOCK_KEY, "1", timeout=180):
        return

    try:
        events = collect_system_events()

        for event in events:
            if event.type == "low_fuel":
                create_low_fuel_notification(event.vehicle)

            elif event.type == "oil_service":
                create_oil_service_notification(event.vehicle)

    finally:
        cache.delete(LOCK_KEY)


@shared_task
def check_low_fuel():
    """
    Создаёт уведомление, если топлива < 20 л
    Не чаще 1 раза в сутки на машину
    """
    vehicles = Vehicle.objects.filter(fuel__isnull=False)

    for v in vehicles:
        try:
            liters_left = float(v.fuel)
        except Exception:
            continue

        if liters_left >= LOW_FUEL_THRESHOLD_LITERS:
            continue

        already_sent = Notification.objects.filter(
            vehicle=v,
            type="low_fuel",
            created_at__date=timezone.now().date()
        ).exists()

        if already_sent:
            continue

        vehicle_label = _vehicle_label(v)
        send_notification(
            type_code="low_fuel",
            title="Низкий уровень топлива",
            message=f"{vehicle_label} — остаток {round(liters_left, 1)} л. Требуется заправка.",
            vehicle=v
        )

@shared_task
def check_oil_service():
    """
    Создаёт уведомление, если до ТО <= 500 км
    """
    vehicles = Vehicle.objects.filter(to_remaining__lte=500)

    for v in vehicles:
        exists = Notification.objects.filter(
            vehicle=v,
            type="oil_service",
            is_read=False
        ).exists()

        if exists:
            continue

        vehicle_label = _vehicle_label(v)
        send_notification(
            type_code="service",
            title="Требуется замена масла",
            message=f"{vehicle_label} — осталось {v.to_remaining} км до ТО",
            vehicle=v
        )
