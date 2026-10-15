
from celery import shared_task
from django.utils import timezone
from datetime import timedelta

from vehicles.models import Vehicle
from fuels.services.incomtek_fuel_import import import_fuel_from_incomtek


@shared_task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 30})
def import_fuel_task(self, days: int = 1):
    """
    Автоматический импорт топливных событий (заправки/сливы) из Incomtek
    """
    end_dt = timezone.now()
    start_dt = end_dt - timedelta(days=days)

    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    total_created = 0

    for vehicle in Vehicle.objects.exclude(imei__isnull=True).exclude(imei=""):
        created = import_fuel_from_incomtek(
            vehicle=vehicle,
            start_ts=start_ts,
            end_ts=end_ts,
        )
        total_created += created

    return {
        "vehicles": Vehicle.objects.count(),
        "created": total_created,
        "period_days": days,
    }
