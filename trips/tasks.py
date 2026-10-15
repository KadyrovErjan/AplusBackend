import logging
from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from django.conf import settings as django_settings

from vehicles.models import Vehicle
from vehicles.policies import is_lorry_vehicle

logger = logging.getLogger(__name__)


@shared_task
def sync_trips_task():
    """
    Фоновая задача синхронизации рейсов.
    DISABLED: Report 60 abandoned.
    """
    logger.info("sync_trips_task is DISABLED (Report 60 abandoned).")
    return "Task disabled"


@shared_task
def sync_single_vehicle_task(veh_id: int):
    """
    Принудительная синхронизация одной машины.
    Вызывается при закрытии смены водителя.

    После синхронизации Trip записей обновляет distance_km
    в недавних сменах из Incomtek Mileage Report (report_type=4).
    """
    if not veh_id:
        return "No veh_id provided"

    vehicle = Vehicle.objects.filter(veh_id=veh_id).first()
    if not vehicle:
        return f"Vehicle not found veh_id={veh_id}"
    if not is_lorry_vehicle(vehicle):
        return f"Skipped non-lorry vehicle veh_id={veh_id}"

    _update_shift_distances(veh_id)

    return f"Synced vehicle veh_id={veh_id} (trips skipped)"


def _update_shift_distances(veh_id: int):
    """
    После синхронизации Trip обновляет DriverShift.distance_km
    из Incomtek Mileage Report (report_type=4) для недавних смен.
    """
    from vehicles.models import DriverShift
    from users.shift_utils import fetch_shift_distance_from_incomtek

    vehicle = Vehicle.objects.filter(veh_id=veh_id).first()
    if not vehicle:
        return
    if not is_lorry_vehicle(vehicle):
        return

    cutoff = timezone.now() - timedelta(hours=24)
    recent_shifts = DriverShift.objects.filter(
        vehicle=vehicle,
        status="completed",
        ended_at__gte=cutoff,
    )

    for shift in recent_shifts:
        distance_km = fetch_shift_distance_from_incomtek(
            vehicle=vehicle,
            started_at=shift.started_at,
            ended_at=shift.ended_at,
        )
        if distance_km > 0:
            shift.distance_km = distance_km
            shift.save(update_fields=['distance_km'])
            logger.info(
                f"Updated shift #{shift.id} distance_km={distance_km} from Incomtek Mileage Report"
            )


@shared_task
def check_idle_vehicles_task():
    """
    Периодическая задача мониторинга простоя техники.

    Применяется только к самосвалам (Lorry).

    Работает только в рабочие часы (09:00–18:00), исключает обеденный перерыв.
    Если техника стоит дольше idle_timeout_minutes — отправляет уведомление администратору.
    Антиспам: повторное уведомление не раньше, чем через idle_timeout_minutes.
    """
    from vehicles.models import DriverShift
    from users.shift_settings import ShiftKPISettings
    from services.admin_telegram import notify_idle

    settings_obj = ShiftKPISettings.get_settings()
    local_now = timezone.localtime()

    workday_start = int(getattr(django_settings, 'WORKDAY_START_HOUR_LOCAL', 9))
    workday_end = int(getattr(django_settings, 'AUTO_SHIFT_CLOSE_NOT_BEFORE_HOUR_LOCAL', 18))

    if local_now.hour < workday_start or local_now.hour >= workday_end:
        return "Outside working hours"

    lunch_start = settings_obj.lunch_break_start_hour
    lunch_end = settings_obj.lunch_break_end_hour
    if lunch_start <= local_now.hour < lunch_end:
        return "Lunch break"

    idle_threshold_minutes = settings_obj.idle_timeout_minutes
    now = timezone.now()

    active_shifts = DriverShift.objects.filter(
        status='active',
        ended_at__isnull=True,
    ).select_related('vehicle', 'driver')

    checked = 0
    notified = 0

    for shift in active_shifts:
        vehicle = shift.vehicle
        if not vehicle:
            continue
        if not is_lorry_vehicle(vehicle):
            continue

        speed = vehicle.speed or 0.0
        is_idle = speed < 1.0

        if not is_idle:
            if shift.idle_since is not None:
                shift.idle_since = None
                shift.idle_notified_at = None
                shift.save(update_fields=['idle_since', 'idle_notified_at'])
            checked += 1
            continue

        if shift.idle_since is None:
            shift.idle_since = now
            shift.save(update_fields=['idle_since'])
            checked += 1
            continue

        idle_minutes = int((now - shift.idle_since).total_seconds() / 60)

        if idle_minutes < idle_threshold_minutes:
            checked += 1
            continue

        if shift.idle_notified_at:
            minutes_since_last = (now - shift.idle_notified_at).total_seconds() / 60
            if minutes_since_last < idle_threshold_minutes:
                checked += 1
                continue

        try:
            notify_idle(shift, idle_minutes)
            shift.idle_notified_at = now
            shift.save(update_fields=['idle_notified_at'])
            notified += 1
            logger.info(
                f"Уведомление о простое: смена #{shift.id}, "
                f"техника {vehicle.name}, простой {idle_minutes} мин"
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления о простое для смены #{shift.id}: {e}")

        checked += 1

    logger.info(f"Проверка простоя: {checked} смен проверено, {notified} уведомлений отправлено")
    return {"checked": checked, "notified": notified}
