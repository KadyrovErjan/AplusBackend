from celery import shared_task
from django.db import IntegrityError, transaction
from django.utils import timezone
from datetime import timedelta, datetime, timezone as dt_timezone
from dataclasses import dataclass
from typing import Protocol
import logging

from vehicles.policies import (
    POLICY_DEFAULT,
    POLICY_LORRY,
    is_lorry_vehicle,
    resolve_vehicle_policy_key,
)

logger = logging.getLogger(__name__)


@dataclass
class PolicyOutcome:
    created_shifts: int = 0
    closed_shifts: int = 0


class ShiftPolicy(Protocol):
    key: str

    def process_vehicle_driver_pair(self, vehicle, driver, settings) -> PolicyOutcome:
        ...

    def finalize(self, settings) -> PolicyOutcome:
        ...


class BaseShiftPolicy:
    key = POLICY_DEFAULT

    def process_vehicle_driver_pair(self, vehicle, driver, settings) -> PolicyOutcome:
        return PolicyOutcome()

    def finalize(self, settings) -> PolicyOutcome:
        return PolicyOutcome()


class LorryShiftPolicy(BaseShiftPolicy):
    key = POLICY_LORRY

    def process_vehicle_driver_pair(self, vehicle, driver, settings) -> PolicyOutcome:
        return _process_lorry_vehicle_driver_pair(vehicle, driver, settings)

    def finalize(self, settings) -> PolicyOutcome:
        closed = _auto_close_idle_shifts(settings, vehicle_predicate=is_lorry_vehicle)
        closed += _auto_close_idle_ignition_on_shifts(settings, vehicle_predicate=is_lorry_vehicle)
        return PolicyOutcome(closed_shifts=closed)


_SHIFT_POLICIES: dict[str, ShiftPolicy] = {
    POLICY_LORRY: LorryShiftPolicy(),
}
_DEFAULT_SHIFT_POLICY = BaseShiftPolicy()


def _resolve_shift_policy(vehicle) -> ShiftPolicy:
    return _SHIFT_POLICIES.get(resolve_vehicle_policy_key(vehicle), _DEFAULT_SHIFT_POLICY)


@shared_task
def monitor_vehicles_for_shift_creation():
    """
    Периодическая задача для мониторинга GPS-данных техники.

    Проверяет все активные транспортные средства и применяет policy по типу ТС.
    Текущая реализованная policy: только для самосвалов (Lorry).

    Для Lorry:
    1. Создаёт смены автоматически при обнаружении начала работы
    2. Закрывает смены при длительном простое
    3. Уведомляет администраторов о событиях
    """
    from users.shift_settings import ShiftKPISettings

    settings = ShiftKPISettings.get_settings()

    if not settings.enable_auto_shift_creation:
        logger.info("Автосоздание смен отключено в настройках")
        return "Auto shift creation disabled"

    vehicle_driver_pairs = _get_eligible_vehicle_driver_pairs()
    logger.info(f"Проверяю {len(vehicle_driver_pairs)} транспортных средств с включенным зажиганием")

    created_shifts = 0
    closed_shifts = 0
    skipped_pairs = 0
    lorry_pairs = 0

    for vehicle, driver in vehicle_driver_pairs:
        if not driver or driver.status != "active":
            continue

        policy = _resolve_shift_policy(vehicle)
        if policy.key == POLICY_DEFAULT:
            skipped_pairs += 1
            continue

        if policy.key == POLICY_LORRY:
            lorry_pairs += 1

        outcome = policy.process_vehicle_driver_pair(vehicle, driver, settings)
        created_shifts += outcome.created_shifts
        closed_shifts += outcome.closed_shifts

    for policy in _SHIFT_POLICIES.values():
        outcome = policy.finalize(settings)
        created_shifts += outcome.created_shifts
        closed_shifts += outcome.closed_shifts

    logger.info(
        f"Мониторинг завершен: создано {created_shifts} смен, закрыто {closed_shifts} смен, "
        f"обработано самосвалов={lorry_pairs}, пропущено прочих ТС={skipped_pairs}"
    )

    return {
        "created_shifts": created_shifts,
        "closed_shifts": closed_shifts,
        "lorry_pairs": lorry_pairs,
        "skipped_pairs": skipped_pairs,
    }


def _process_lorry_vehicle_driver_pair(vehicle, driver, settings) -> PolicyOutcome:
    """
    Стратегия для самосвалов (Lorry): текущая логика автосмен без изменений.
    """
    from vehicles.models import DriverShift

    created_shifts = 0
    closed_shifts = 0

    active_shift = DriverShift.objects.filter(
        driver=driver,
        vehicle=vehicle,
        status="active",
        ended_at__isnull=True
    ).first()

    if not active_shift and vehicle.ignition:
        now_local = timezone.localtime()
        if now_local.hour < 4:
            logger.debug(
                f"Техника {vehicle.name}: автосоздание смен запрещено до 04:00 "
                f"(сейчас {now_local.strftime('%H:%M')})"
            )
        else:
            should_create, work_started_at, ignition_on_at = _check_shift_creation_criteria(vehicle, driver, settings)

            if should_create:
                shift, created = _create_automatic_shift(
                    vehicle,
                    driver,
                    settings,
                    work_started_at=work_started_at,
                    ignition_on_at=ignition_on_at,
                )
                if created:
                    created_shifts += 1
                    _notify_admin_ignition_and_shift(shift)

    if active_shift and _check_max_shift_duration(active_shift, settings):
        if _can_auto_close_shift_now(active_shift):
            from users.shift_utils import fetch_shift_distance_from_incomtek

            end_at = timezone.now()
            active_shift.ended_at = end_at
            active_shift.status = "completed"
            active_shift.distance_km = fetch_shift_distance_from_incomtek(
                vehicle=active_shift.vehicle,
                started_at=active_shift.started_at,
                ended_at=end_at,
            )
            if active_shift.vehicle and active_shift.engine_hours_at_shift_start is not None:
                active_shift.engine_hours = round(
                    (active_shift.vehicle.total_engine_hours or 0.0) - active_shift.engine_hours_at_shift_start, 1
                )
            active_shift.save(update_fields=["ended_at", "status", "distance_km", "engine_hours"])
            logger.warning(
                f"Смена #{active_shift.id} принудительно закрыта: "
                f"превышена максимальная длительность ({settings.max_shift_duration_hours}ч), "
                f"distance_km={active_shift.distance_km:.3f}"
            )
            closed_shifts += 1
            _notify_admin_shift_ended(active_shift)
        else:
            logger.info(
                f"Смена #{active_shift.id}: автозакрытие отложено до 18:00 по Asia/Bishkek"
            )

    return PolicyOutcome(created_shifts=created_shifts, closed_shifts=closed_shifts)


def _get_eligible_vehicle_driver_pairs():
    """
    Возвращает пары (vehicle, driver) для автосоздания смен.

    Источник истины по назначению водителя — VehicleAssignment.
    Для обратной совместимости добавлен fallback на legacy-поле Vehicle.driver.
    """
    from vehicles.models import Vehicle, VehicleAssignment

    pairs = []
    seen = set()

    active_assignments = VehicleAssignment.objects.filter(
        unassigned_at__isnull=True,
        driver__isnull=False,
        driver__status="active",
        vehicle__isnull=False,
        vehicle__ignition=True,
    ).select_related("vehicle", "driver")

    for assignment in active_assignments:
        vehicle = assignment.vehicle
        driver = assignment.driver
        key = (vehicle.id, driver.id)
        if key in seen:
            continue
        pairs.append((vehicle, driver))
        seen.add(key)

    legacy_vehicles = Vehicle.objects.filter(
        ignition=True,
        driver__isnull=False,
        driver__status="active",
    ).select_related("driver")

    for vehicle in legacy_vehicles:
        driver = vehicle.driver
        key = (vehicle.id, driver.id)
        if key in seen:
            continue
        pairs.append((vehicle, driver))
        seen.add(key)

    return pairs


def _check_shift_creation_criteria(vehicle, driver, settings):
    """
    Проверяет, выполняются ли критерии для автосоздания смены.

    Критерии:
    1. Зажигание включено
    2. Техника проехала минимум N км (защита от "сон в кабине")
    3. Моточасы растут (техника работает)
    """
    from vehicles.models import VehicleStatusHistory

    if not vehicle.ignition:
        return False, None, None

    if not vehicle.last_ignition_change_at:
        logger.debug(f"Техника {vehicle.name} не имеет last_ignition_change_at")
        return False, None, None

    history_start_ts = _get_shift_creation_history_start_ts(vehicle, driver)

    history = list(VehicleStatusHistory.objects.filter(
        vehicle=vehicle,
        ts__gte=history_start_ts,
        ignition=True
    ).order_by('ts').values_list('lat', 'lon', 'ts'))

    if len(history) < 2:
        logger.info(f"Техника {vehicle.name}: недостаточно GPS точек после включения ({len(history)})")
        return False, None, None

    first_lat, first_lon, first_ts = history[0]
    last_lat, last_lon, last_ts = history[-1]
    ignition_on_at = datetime.fromtimestamp(first_ts, tz=dt_timezone.utc)

    track_km = _calculate_track_distance_km(history)
    work_started_at = _detect_work_started_at(history, movement_threshold_km=0.2)

    odometer_delta_km = None
    movement_km = track_km
    if movement_km < settings.min_movement_km:
        odometer_delta_km = _get_odometer_delta_km_from_istatus(vehicle, history_start_ts)
        if odometer_delta_km is not None:
            movement_km = max(movement_km, odometer_delta_km)

    displacement_km = _calculate_distance_haversine(first_lat, first_lon, last_lat, last_lon)
    odometer_log = "n/a" if odometer_delta_km is None else f"{odometer_delta_km:.3f} км"
    work_start_log = (
        timezone.localtime(work_started_at).strftime("%H:%M:%S")
        if work_started_at else "n/a"
    )
    logger.info(
        f"Критерий движения {vehicle.name}: "
        f"трек={track_km:.3f} км, по прямой={displacement_km:.3f} км, "
        f"odometer_delta={odometer_log}, старт_движения={work_start_log}, "
        f"порог={settings.min_movement_km:.3f} км"
    )

    if movement_km < settings.min_movement_km:
        return False, None, None

    exact_ignition_on_at = _fetch_ignition_on_at_from_sensors(vehicle, history_start_ts)
    if exact_ignition_on_at:
        ignition_on_at = exact_ignition_on_at

    logger.info(
        f"✅ Техника {vehicle.name} прошла проверку: движение {movement_km:.2f} км за "
        f"{(last_ts - first_ts) / 60:.1f} минут"
    )

    if work_started_at is None:
        work_started_at = ignition_on_at

    return True, work_started_at, ignition_on_at


def _get_shift_creation_history_start_ts(vehicle, driver):
    """
    Окно для поиска старта движения:
    - не раньше начала текущего локального дня;
    - не раньше времени назначения техники этому водителю.
    """
    from vehicles.models import VehicleAssignment

    now_local = timezone.localtime()
    day_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_dt = day_start_local

    assignment = (
        VehicleAssignment.objects
        .filter(
            vehicle=vehicle,
            driver=driver,
            unassigned_at__isnull=True,
        )
        .only("assigned_at")
        .first()
    )
    if assignment and assignment.assigned_at and assignment.assigned_at > start_dt:
        start_dt = assignment.assigned_at

    return int(start_dt.timestamp()) - 30


def _parse_ts(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _is_ignition_sensor(sensor_data):
    fieldname = str((sensor_data or {}).get("fieldname") or "").upper()
    info = str((sensor_data or {}).get("info") or "").lower()
    return fieldname == "DIS1" or ("зажиган" in info) or ("ignition" in info)


def _fetch_ignition_on_at_from_sensors(vehicle, start_ts):
    """
    Возвращает точное время включения зажигания (ts) из Incomtek cmd=sensors.
    Берём самый поздний ts по датчику зажигания в текущем окне.
    """
    from services.pilot_client import PilotClient

    if not vehicle or not vehicle.imei:
        return None

    now_ts = int(timezone.now().timestamp())
    start_ts = _parse_ts(start_ts)
    if start_ts is None:
        start_ts = now_ts - 24 * 3600
    if start_ts > now_ts:
        start_ts = now_ts - 3600

    try:
        client = PilotClient()
        response = client.get_sensors_report(
            imei=str(vehicle.imei),
            start=start_ts,
            stop=now_ts,
            node=1,
        )
    except Exception as e:
        logger.warning(f"Техника {vehicle.name}: sensors request failed: {e}")
        return None

    if not isinstance(response, dict) or not response.get("success"):
        return None

    sensors = response.get("sensors")
    if not isinstance(sensors, dict):
        return None

    latest_ts = None

    for _, sensor_data in sensors.items():
        if not isinstance(sensor_data, dict):
            continue
        if not _is_ignition_sensor(sensor_data):
            continue

        work = sensor_data.get("work")
        if not isinstance(work, dict):
            continue

        for key, period in work.items():
            ts = None
            if isinstance(period, dict):
                ts = _parse_ts(period.get("ts"))
            if ts is None:
                ts = _parse_ts(key)
            if ts is None or ts > now_ts:
                continue

            if latest_ts is None or ts > latest_ts:
                latest_ts = ts

    if latest_ts is None:
        return None

    return datetime.fromtimestamp(latest_ts, tz=dt_timezone.utc)


def _calculate_distance_haversine(lat1, lon1, lat2, lon2):
    """
    Рассчитывает расстояние между двумя GPS точками по формуле Haversine.
    Возвращает расстояние в километрах.
    """
    from math import radians, cos, sin, asin, sqrt

    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])

    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))

    r = 6371

    return c * r


def _calculate_track_distance_km(history_points):
    """
    Считает накопленную дистанцию (сумма отрезков между соседними GPS точками).
    history_points: iterable[(lat, lon, ts)] в хронологическом порядке.
    """
    if len(history_points) < 2:
        return 0.0

    total = 0.0
    prev_lat, prev_lon, _ = history_points[0]

    for lat, lon, _ in history_points[1:]:
        total += _calculate_distance_haversine(prev_lat, prev_lon, lat, lon)
        prev_lat, prev_lon = lat, lon

    return total


def _detect_work_started_at(history_points, movement_threshold_km=0.2):
    """
    Возвращает timestamp первого "осмысленного старта" по GPS:
    когда накопленное движение достигло movement_threshold_km.
    """
    if len(history_points) < 2:
        return None

    total = 0.0
    prev_lat, prev_lon, _ = history_points[0]

    for lat, lon, ts in history_points[1:]:
        total += _calculate_distance_haversine(prev_lat, prev_lon, lat, lon)
        if total >= movement_threshold_km:
            return datetime.fromtimestamp(ts, tz=dt_timezone.utc)
        prev_lat, prev_lon = lat, lon

    return None


def _compute_shift_work_metrics(shift_start_time):
    """
    Возвращает (late_minutes, worked_norm_minutes) по локальному графику.
    Логика:
    - рабочий старт в 09:00 локального времени;
    - до 09:30 дается прогрев (без штрафа);
    - опоздание = минуты после 09:30;
    - учет рабочего времени = 480 минут - опоздание (не ниже 0).
    """
    from django.conf import settings

    start_hour = int(getattr(settings, "WORKDAY_START_HOUR_LOCAL", 9))
    warmup_minutes = int(getattr(settings, "WORKDAY_WARMUP_MINUTES", 30))
    norm_minutes = int(getattr(settings, "WORKDAY_NORM_MINUTES", 480))

    start_hour = max(0, min(23, start_hour))
    warmup_minutes = max(0, warmup_minutes)
    norm_minutes = max(0, norm_minutes)

    local_start = timezone.localtime(shift_start_time)
    deadline = local_start.replace(hour=start_hour, minute=0, second=0, microsecond=0) + timedelta(minutes=warmup_minutes)

    late_minutes = max(0, int((local_start - deadline).total_seconds() // 60))
    worked_norm_minutes = max(0, norm_minutes - late_minutes)

    return late_minutes, worked_norm_minutes


def _to_float(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", ".").replace(" ", "")
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _extract_istatus_odometer_km(data):
    for key in ("общий_пробег", "total_run_by_can", "run"):
        value = _to_float(data.get(key))
        if value is not None:
            return value
    return None


def _is_istatus_ok(data):
    code = data.get("code")
    if code is not None and str(code) == "0":
        return True
    code_ru = data.get("код")
    if code_ru is not None and str(code_ru) == "0":
        return True
    return False


def _get_odometer_delta_km_from_istatus(vehicle, ignition_ts):
    """
    Возвращает прирост одометра (км) между моментом включения зажигания и текущим временем.
    Если данные недоступны — None.
    """
    from services.pilot_client import pilot_request_sync

    if not vehicle.imei:
        return None

    now_ts = int(timezone.now().timestamp())

    start_data = pilot_request_sync(
        "istatus",
        node=14,
        params={"imei": vehicle.imei, "unixtimestamp": ignition_ts}
    )
    end_data = pilot_request_sync(
        "istatus",
        node=14,
        params={"imei": vehicle.imei, "unixtimestamp": now_ts}
    )

    if not _is_istatus_ok(start_data) or not _is_istatus_ok(end_data):
        return None

    start_km = _extract_istatus_odometer_km(start_data)
    end_km = _extract_istatus_odometer_km(end_data)

    if start_km is None or end_km is None:
        return None

    delta = end_km - start_km
    if delta < 0:
        logger.warning(
            f"Техника {vehicle.name}: отрицательная дельта одометра ({delta:.3f} км), игнорирую"
        )
        return None

    return delta


def _find_existing_shift_for_session(driver, vehicle, shift_start_time, ignition_time):
    from vehicles.models import DriverShift

    base_qs = DriverShift.objects.filter(driver=driver, vehicle=vehicle)

    active_shift = base_qs.filter(status="active", ended_at__isnull=True).order_by("-started_at", "-id").first()
    if active_shift:
        return active_shift

    if ignition_time:
        same_ignition_shift = base_qs.filter(
            auto_created=True,
            ignition_on_at=ignition_time,
        ).order_by("-started_at", "-id").first()
        if same_ignition_shift:
            return same_ignition_shift

    window_start = shift_start_time - timedelta(minutes=1)
    window_end = shift_start_time + timedelta(minutes=1)
    return base_qs.filter(
        auto_created=True,
        started_at__gte=window_start,
        started_at__lte=window_end,
    ).order_by("-started_at", "-id").first()


def _create_automatic_shift(vehicle, driver, settings, work_started_at=None, ignition_on_at=None):
    """
    Создаёт смену автоматически на основе GPS-данных.
    """
    from vehicles.models import DriverShift

    now = timezone.now()
    ignition_time = ignition_on_at or vehicle.last_ignition_change_at or now
    shift_start_time = work_started_at or ignition_time
    if shift_start_time > now:
        shift_start_time = now

    existing_shift = _find_existing_shift_for_session(
        driver=driver,
        vehicle=vehicle,
        shift_start_time=shift_start_time,
        ignition_time=ignition_time,
    )
    if existing_shift:
        logger.info(
            f"Skip duplicate auto-shift for driver={driver.id}, vehicle={vehicle.id}: "
            f"existing_shift_id={existing_shift.id}"
        )
        return existing_shift, False

    late_minutes, worked_norm_minutes = _compute_shift_work_metrics(shift_start_time)

    try:
        with transaction.atomic():
            shift = DriverShift.objects.create(
                driver=driver,
                vehicle=vehicle,
                started_at=shift_start_time,
                ignition_on_at=ignition_time,
                movement_started_at=shift_start_time,
                late_minutes=late_minutes,
                worked_norm_minutes=worked_norm_minutes,
                distance_at_shift_start=0.0,
                engine_hours_at_shift_start=(vehicle.total_engine_hours or 0.0),
                auto_created=True,
                status="active"
            )
    except IntegrityError:
        existing_shift = _find_existing_shift_for_session(
            driver=driver,
            vehicle=vehicle,
            shift_start_time=shift_start_time,
            ignition_time=ignition_time,
        )
        if existing_shift:
            logger.warning(
                f"Auto-shift create raced for driver={driver.id}, vehicle={vehicle.id}; "
                f"using shift_id={existing_shift.id}"
            )
            return existing_shift, False
        raise

    logger.info(
        f"Автосоздана смена #{shift.id} для водителя {driver.first_name} ({driver.telegram_id}) "
        f"на технике {vehicle.name}. started_at={timezone.localtime(shift_start_time).strftime('%H:%M:%S')}, "
        f"ignition_on_at={timezone.localtime(ignition_time).strftime('%H:%M:%S')}, "
        f"late={late_minutes}мин, worked_norm={worked_norm_minutes}мин"
    )

    return shift, True


def _notify_admin_ignition_and_shift(shift):
    """
    Отправляет два уведомления администратору:
    1. Зажигание включено (с реальным временем ignition_on_at)
    2. Смена началась (с временем started_at)
    """
    try:
        from services.admin_telegram import notify_ignition_on, notify_shift_started
        if shift.ignition_on_at:
            notify_ignition_on(shift.vehicle, shift.driver, shift.ignition_on_at)
        notify_shift_started(shift)
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления администратору о смене #{shift.id}: {e}")


def _notify_admin_shift_ended(shift):
    """Отправляет уведомление администратору о завершении смены."""
    try:
        from services.admin_telegram import notify_shift_ended
        notify_shift_ended(shift)
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления администратору о завершении смены #{shift.id}: {e}")


def _detect_last_movement_time(vehicle, since_dt):
    """
    Определяет время последнего реального движения техники по GPS-истории.
    """
    from vehicles.models import VehicleStatusHistory

    since_ts = int(since_dt.timestamp())

    points = list(
        VehicleStatusHistory.objects.filter(
            vehicle=vehicle,
            ts__gte=since_ts,
            ignition=True,
        )
        .order_by("-ts")
        .values_list("lat", "lon", "ts", "speed")[:500]
    )

    if not points:
        return None

    for i, (lat, lon, ts, speed) in enumerate(points):
        if speed is not None and speed > 1.0:
            return datetime.fromtimestamp(ts, tz=dt_timezone.utc)

        if i + 1 < len(points):
            next_lat, next_lon, _, _ = points[i + 1]
            dist = _calculate_distance_haversine(lat, lon, next_lat, next_lon)
            if dist > 0.02:  # > 20 метров
                return datetime.fromtimestamp(ts, tz=dt_timezone.utc)

    return None


def _auto_close_idle_ignition_on_shifts(settings, vehicle_predicate=None):
    """
    Закрывает смены, где техника стоит с включённым зажиганием без движения
    дольше N часов (после 18:00 по локальному времени).
    """
    from vehicles.models import DriverShift
    from django.conf import settings as django_settings

    now_local = timezone.localtime()
    close_hour = int(getattr(django_settings, "AUTO_SHIFT_CLOSE_NOT_BEFORE_HOUR_LOCAL", 18))
    close_hour = max(0, min(23, close_hour))

    if now_local.hour < close_hour:
        return 0

    idle_hours = settings.auto_close_idle_ignition_on_hours

    active_shifts = DriverShift.objects.filter(
        status="active",
        ended_at__isnull=True,
        vehicle__ignition=True,
    ).select_related("vehicle", "driver")

    count = 0
    for shift in active_shifts:
        vehicle = shift.vehicle
        if not vehicle:
            continue
        if vehicle_predicate and not vehicle_predicate(vehicle):
            continue

        last_movement = _detect_last_movement_time(vehicle, shift.started_at)

        if last_movement is None:
            idle_since = shift.started_at
        else:
            idle_since = last_movement

        idle_duration_hours = (timezone.now() - idle_since).total_seconds() / 3600

        if idle_duration_hours < idle_hours:
            continue

        from users.shift_utils import fetch_shift_distance_from_incomtek

        end_at = last_movement or shift.started_at
        shift.ended_at = end_at
        shift.status = "completed"
        shift.distance_km = fetch_shift_distance_from_incomtek(
            vehicle=vehicle,
            started_at=shift.started_at,
            ended_at=end_at,
        )
        if vehicle and shift.engine_hours_at_shift_start is not None:
            shift.engine_hours = round(
                (vehicle.total_engine_hours or 0.0) - shift.engine_hours_at_shift_start, 1
            )
        shift.save(update_fields=["ended_at", "status", "distance_km", "engine_hours"])

        logger.info(
            f"Автозакрыта смена #{shift.id} водителя {shift.driver.first_name}: "
            f"простой с включённым зажиганием > {idle_hours}ч, "
            f"последнее движение={timezone.localtime(end_at).strftime('%H:%M:%S')}, "
            f"distance_km={shift.distance_km:.3f}"
        )
        _notify_admin_shift_ended(shift)
        count += 1

    return count


def _auto_close_idle_shifts(settings, vehicle_predicate=None):
    """
    Закрывает смены, которые простаивают без движения и с выключенным зажиганием.
    """
    from vehicles.models import DriverShift

    idle_threshold = timezone.now() - timedelta(hours=settings.auto_close_idle_hours)

    shifts_to_close = DriverShift.objects.filter(
        status="active",
        ended_at__isnull=True,
        vehicle__ignition=False,
        vehicle__last_ignition_change_at__lt=idle_threshold
    ).select_related('vehicle', 'driver')

    count = 0
    for shift in shifts_to_close:
        if vehicle_predicate and not vehicle_predicate(shift.vehicle):
            continue

        if not _can_auto_close_shift_now(shift):
            logger.info(f"Смена #{shift.id}: автозакрытие отложено до 18:00 по Asia/Bishkek")
            continue

        from users.shift_utils import fetch_shift_distance_from_incomtek

        end_at = shift.vehicle.last_ignition_change_at or timezone.now()
        shift.ended_at = end_at
        shift.status = "completed"
        shift.distance_km = fetch_shift_distance_from_incomtek(
            vehicle=shift.vehicle,
            started_at=shift.started_at,
            ended_at=end_at,
        )
        if shift.vehicle and shift.engine_hours_at_shift_start is not None:
            shift.engine_hours = round(
                (shift.vehicle.total_engine_hours or 0.0) - shift.engine_hours_at_shift_start, 1
            )
        shift.save(update_fields=['ended_at', 'status', 'distance_km', 'engine_hours'])

        logger.info(
            f"Автоматически закрыта смена #{shift.id} водителя {shift.driver.first_name} "
            f"(простой > {settings.auto_close_idle_hours} часов), "
            f"distance_km={shift.distance_km:.3f}"
        )
        _notify_admin_shift_ended(shift)
        count += 1

    return count


def _check_max_shift_duration(shift, settings):
    """
    Проверяет, не превышена ли максимальная длительность смены.
    """
    if not shift.started_at:
        return False

    duration_hours = (timezone.now() - shift.started_at).total_seconds() / 3600

    if duration_hours > settings.max_shift_duration_hours:
        logger.debug(
            f"Смена #{shift.id}: длительность {duration_hours:.1f}ч "
            f"> максимум {settings.max_shift_duration_hours}ч"
        )
        return True

    return False


def _can_auto_close_shift_now(shift):
    """
    Автозакрытие запрещено для смен, начатых сегодня, до 18:00 по локальному времени (Asia/Bishkek).
    Для смен прошлых дат ограничение не применяется.
    """
    from django.conf import settings

    if not shift.started_at:
        return True

    now_local = timezone.localtime()
    start_local = timezone.localtime(shift.started_at)

    if start_local.date() != now_local.date():
        return True

    close_hour = int(getattr(settings, "AUTO_SHIFT_CLOSE_NOT_BEFORE_HOUR_LOCAL", 18))
    close_hour = max(0, min(23, close_hour))
    return now_local.hour >= close_hour
