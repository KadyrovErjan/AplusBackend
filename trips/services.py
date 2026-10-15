import hashlib
import logging
import re
from datetime import datetime, date
import pytz
from django.conf import settings
from django.db.models import Sum, Q, Prefetch
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Trip
from vehicles.models import DriverShift, Vehicle, VehicleAssignment
from vehicles.serializers import FrontendVehicleSerializer
from services.pilot_client import PilotClient
from django.core.cache import cache
from fuels.models import FuelZone
from vehicles.type_mapping import (
    VEHICLE_TYPE_LABELS,
    REVERSE_VEHICLE_LABELS,
    get_vehicle_type_label,
    normalize_vehicle_type,
)

logger = logging.getLogger(__name__)


class TripSyncService:
    """
    Сервис для синхронизации подтвержденных рейсов из Incomtek API.
    Использует report_type=60 (Trips between geozones).

    ИСПРАВЛЕНО: Парсинг корневого блока 'data' по имени машины,
    так как в блоке 'history' нет дистанции.
    """

    def sync_trips_for_period(self, vehicle_ids_str: str, start_ts: int, end_ts: int, pilot_client):
        """
        Загружает данные из API и сохраняет в таблицу Trip.
        """
        response = pilot_client.get_report(
            report_type=60,
            veh_id=vehicle_ids_str,
            start=start_ts,
            stop=end_ts,
            explode=1,
            group=2
        )

        if not response.get('success'):
            logger.error(f"Incomtek API Error: {response.get('msg')}")
            return

        root_data = response.get('data', {})
        if not root_data:
            logger.warning("Incomtek API returned empty 'data' block")
            return

        veh_ids = [v.strip() for v in vehicle_ids_str.split(',') if v.strip()]

        vehicles = Vehicle.objects.filter(veh_id__in=veh_ids)

        tz = pytz.timezone(settings.TIME_ZONE)

        for vehicle in vehicles:
            self._process_vehicle_from_root_data(vehicle, root_data, tz)

    def _process_vehicle_from_root_data(self, vehicle, root_data, tz):
        """
        Ищет данные машины в root_data по имени и сохраняет рейсы.
        """
        trips_map = _find_trips_map_for_vehicle(vehicle, root_data)
        if trips_map is None:
            return

        if isinstance(trips_map, list) and not trips_map:
            return

        for ts_start_key, trip_array in trips_map.items():
            if isinstance(trip_array, list) and len(trip_array) > 8:
                self._upsert_trip_from_array(vehicle, trip_array, tz)

    def _upsert_trip_from_array(self, vehicle, trip_array, tz):
        try:
            ts_start = int(trip_array[3])
            ts_arrival = int(trip_array[7])

            raw_uid = f"{vehicle.id}_{ts_start}"
            uid = hashlib.md5(raw_uid.encode('utf-8')).hexdigest()

            dt_start = datetime.fromtimestamp(ts_start, tz)
            dt_arrival = datetime.fromtimestamp(ts_arrival, tz)

            duration = int(trip_array[5])

            distance = float(trip_array[6])

            start_zone = str(trip_array[4])
            end_zone = str(trip_array[8])

            ts_end = int(trip_array[9]) if len(trip_array) > 9 and trip_array[9] else ts_arrival
            dt_end = datetime.fromtimestamp(ts_end, tz)

            dwell_seconds = int(trip_array[10]) if len(trip_array) > 10 and trip_array[10] else 0

            Trip.objects.update_or_create(
                uid=uid,
                defaults={
                    'vehicle': vehicle,
                    'start_at': dt_start,
                    'arrival_at': dt_arrival,
                    'end_at': dt_end,
                    'start_zone_name': start_zone,
                    'end_zone_name': end_zone,
                    'trip_duration_seconds': duration,
                    'dwell_duration_seconds': dwell_seconds,
                    'distance_km': distance
                }
            )
        except (ValueError, TypeError, IndexError) as e:
            logger.error(f"Error parsing trip array for {vehicle}: {e}")


class RouteAnalyticsService:
    """
    Сервис для получения (и кэширования) статистики рейсов
    между конкретной парой зон (report_type=173).
    """

    def get_verified_route_stats(self, shifts_qs, zone_from_id: int, zone_to_id: int):
        """
        Возвращает словарь {shift_id: {validated: bool, count: int, distance: float, error: str}}.
        """
        # 1. Validation
        if not zone_from_id or not zone_to_id:
            return {}

        try:
            zone_from_id = int(zone_from_id)
            zone_to_id = int(zone_to_id)
        except (TypeError, ValueError):
            return {s.id: {"error": "Invalid zone id"} for s in shifts_qs}

        if zone_from_id == zone_to_id:
            return {s.id: {"error": "Same zones"} for s in shifts_qs}

        # Check existence
        zones = FuelZone.objects.filter(external_id__in=[zone_from_id, zone_to_id]).only("external_id", "name")
        zones_by_external_id = {z.external_id: z for z in zones}
        if zone_from_id not in zones_by_external_id or zone_to_id not in zones_by_external_id:
            return {s.id: {"error": "Zones not found"} for s in shifts_qs}

        zone_from_name = zones_by_external_id[zone_from_id].name
        zone_to_name = zones_by_external_id[zone_to_id].name

        client = PilotClient()
        tz = pytz.timezone(settings.TIME_ZONE)
        results = {}

        # 2. Iterate shifts
        for shift in shifts_qs:
             veh = _find_vehicle_for_shift(shift)
             if not veh or not veh.veh_id:
                 results[shift.id] = {"error": "No vehicle"}
                 continue
             
             start_ts = int(shift.started_at.timestamp())
             end_ts = int((shift.ended_at or timezone.now()).timestamp())

             # 3. Cache Check
             cache_key = f"route_173_v3:{veh.veh_id}:{start_ts}:{end_ts}:{zone_from_id}_{zone_to_id}"
             cached_data = cache.get(cache_key)

             if cached_data:
                 results[shift.id] = cached_data
                 continue
             
             # 4. API Call
             try:
                 resp = client.get_route_report(
                     veh_id=veh.veh_id,
                     start=start_ts,
                     stop=end_ts,
                     zone_from_id=zone_from_id,
                     zone_to_id=zone_to_id
                 )

                 parsed = self._parse_173_response(
                     response=resp,
                     vehicle=veh,
                     tz=tz,
                     zone_from_name=zone_from_name,
                     zone_to_name=zone_to_name,
                 )

                 stats = {
                     "count": parsed["count"],
                     "distance_km": parsed["distance_km"],
                     "validated": True,
                     "error": None
                 }
                 
                 # Cache logic: 24h if completed > 2h ago, else 5 min
                 is_completed = shift.status == 'completed'
                 timeout = 60 * 60 * 24 if is_completed else 300
                 cache.set(cache_key, stats, timeout)
                 
                 results[shift.id] = stats

             except Exception as e:
                 logger.error(f"Error fetching route 173 for shift {shift.id}: {e}")
                 results[shift.id] = {"error": "API Error"}

        return results

    def _parse_173_response(self, response, vehicle, tz, zone_from_name=None, zone_to_name=None):
        if not response.get('success'):
            return {"count": 0, "distance_km": 0.0}

        root_data = response.get('data', {})
        vehicle_root = _find_trips_map_for_vehicle(vehicle, root_data)

        if not vehicle_root:
            return {"count": 0, "distance_km": 0.0}

        trips_summary = []
        self._extract_trips_recursive(vehicle_root, trips_summary)

        if zone_from_name and zone_to_name:
            trips_summary = [
                item for item in trips_summary
                if self._route_matches_direction(item.get("route"), zone_from_name, zone_to_name)
            ]

        unique_intervals = set()
        total_count = 0
        total_distance = 0.0

        for item in trips_summary:
            raw_intervals = item.get("tr") if isinstance(item.get("tr"), list) else []
            intervals = [str(v).strip() for v in raw_intervals if str(v).strip()]

            if intervals:
                item_count = 0
                for interval in intervals:
                    if interval in unique_intervals:
                        continue
                    unique_intervals.add(interval)
                    item_count += 1
            else:
                item_count = max(0, self._to_int(item.get("count")))

            if item_count <= 0:
                continue

            total_count += item_count
            item_distance = max(0.0, self._to_float(item.get("distance")))
            declared_count = max(0, self._to_int(item.get("count")))

            if item_distance > 0 and declared_count > 0:
                # Пропорционально распределяем расстояние, если часть интервалов была отфильтрована/дедуплицирована.
                total_distance += (item_distance / declared_count) * item_count
            else:
                total_distance += item_distance

        return {"count": total_count, "distance_km": round(total_distance, 1)}

    def _extract_trips_recursive(self, data, results):
        """
        Deep traversal to find nodes with 'tr' list and 'mileage'.
        User's JSON structure:
        "Subject": {
           "Date Range": {
               "0": { "tr": ["ts-ts", ...], "mileage": 0, ... },
               "1": { ... }
           }
        }
        """
        if isinstance(data, dict):
            # Check if this node is a leaf with trips
            if 'tr' in data and isinstance(data['tr'], list):
                intervals = [str(v).strip() for v in data.get("tr", []) if str(v).strip()]
                count = self._to_int(data.get("number")) or len(intervals)
                mileage = self._to_float(data.get("mileage", 0))
                if count > 0:
                    results.append({
                        'count': count,
                        'distance': mileage,
                        'route': data.get('route', ''),
                        'tr': intervals,
                    })
            
            # Recurse into all values
            for key, value in data.items():
                if isinstance(value, (dict, list)):
                    self._extract_trips_recursive(value, results)
        
        elif isinstance(data, list):
            for item in data:
                self._extract_trips_recursive(item, results)

    @staticmethod
    def _normalize_name(value):
        return " ".join(str(value or "").replace("ё", "е").lower().split())

    @classmethod
    def _name_matches(cls, route_part, zone_name):
        route_norm = cls._normalize_name(route_part)
        zone_norm = cls._normalize_name(zone_name)
        if not route_norm or not zone_norm:
            return False
        return (
            route_norm == zone_norm
            or zone_norm in route_norm
            or route_norm in zone_norm
        )

    @classmethod
    def _route_matches_direction(cls, route_text, zone_from_name, zone_to_name):
        if not route_text:
            return False
        parts = re.split(r"\s*(?:=>|->|→|➝)\s*", str(route_text), maxsplit=1)
        if len(parts) != 2:
            return False
        left, right = parts[0], parts[1]
        return cls._name_matches(left, zone_from_name) and cls._name_matches(right, zone_to_name)

    @staticmethod
    def _to_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _to_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


def format_date(dt):
    if not dt:
        return None
    return timezone.localtime(dt).strftime("%d-%m-%Y %H:%M")


def _resolve_zone_external_id(zone_id_raw=None, zone_name_raw=None):
    """
    Поддержка двух форматов фильтра:
    - zone_from/zone_to: внешний integer ID (предпочтительно)
    - route_from/route_to: legacy-имена зон
    """
    try:
        if zone_id_raw not in (None, ""):
            return int(zone_id_raw), None
    except (TypeError, ValueError):
        return None, "Invalid zone id"

    zone_name = (zone_name_raw or "").strip()
    if not zone_name:
        return None, None

    exact = FuelZone.objects.filter(name__iexact=zone_name, external_id__isnull=False).only("external_id").first()
    if exact:
        return exact.external_id, None

    partial_matches = list(
        FuelZone.objects
        .filter(name__icontains=zone_name, external_id__isnull=False)
        .only("external_id")[:2]
    )
    if len(partial_matches) == 1:
        return partial_matches[0].external_id, None
    if len(partial_matches) > 1:
        return None, f"Ambiguous zone name: {zone_name}"

    return None, f"Zone not found: {zone_name}"


def _parse_filter_date(value):
    """
    Поддерживаем оба формата:
    - DD-MM-YYYY (новый UI формат)
    - YYYY-MM-DD (обратная совместимость)
    """
    if value in (None, ""):
        return None

    if isinstance(value, date):
        return value

    value = str(value).strip()
    if not value:
        return None

    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue

    return None


def _normalize_filter_date_range(date_from, date_to):
    today = timezone.localdate()
    if not date_from and not date_to:
        return today, today
    if date_from and not date_to:
        return date_from, today
    return date_from, date_to


def _build_filter_datetime_range(date_from, date_to):
    start_dt = None
    end_dt = None
    if date_from:
        start_dt = timezone.make_aware(datetime.combine(date_from, datetime.min.time()))
    if date_to:
        end_dt = timezone.make_aware(datetime.combine(date_to, datetime.max.time()))
    return start_dt, end_dt


def _dt_to_epoch_seconds(value):
    if not value:
        return None
    return int(value.timestamp())


def _build_shift_identity_key(shift):
    """
    Stable key for suppressing duplicated DriverShift rows that represent
    the same фактическая смена.
    """
    movement_start = shift.movement_started_at or shift.started_at
    return (
        shift.driver_id,
        shift.vehicle_id,
        shift.status,
        _dt_to_epoch_seconds(shift.started_at),
        _dt_to_epoch_seconds(shift.ended_at),
        _dt_to_epoch_seconds(movement_start),
    )


def _shift_quality_score(shift):
    """
    Prefer the shift row that has more user/business data populated.
    """
    return (
        1 if shift.trips_count is not None else 0,
        1 if (shift.trips_note or "").strip() else 0,
        1 if shift.ended_at else 0,
        float(shift.distance_km or 0.0),
        float(shift.engine_hours or 0.0),
        shift.id or 0,
    )


def _deduplicate_shifts(shifts):
    best_shift_by_key = {}
    for shift in shifts:
        key = _build_shift_identity_key(shift)
        existing = best_shift_by_key.get(key)
        if not existing or _shift_quality_score(shift) > _shift_quality_score(existing):
            best_shift_by_key[key] = shift

    return sorted(
        best_shift_by_key.values(),
        key=lambda item: (item.started_at, item.id),
        reverse=True,
    )


def _find_trips_map_for_vehicle(vehicle, root_data):
    """
    В ответе Incomtek ключи root_data зависят от имени машины.
    Пытаемся найти подходящий ключ.
    """
    if not root_data:
        return None

    target_name = (vehicle.name or "").strip()
    found_key = None

    if target_name:
        for key in root_data.keys():
            if target_name == key.strip():
                found_key = key
                break

    if not found_key and target_name:
        for key in root_data.keys():
            if target_name in key or key in target_name:
                found_key = key
                break

    if not found_key:
        if len(root_data.keys()) == 1:
            found_key = list(root_data.keys())[0]

    if not found_key:
        logger.warning(
            f"Data for vehicle '{target_name}' (ID: {vehicle.veh_id}) not found in API response."
        )
        return None

    return root_data.get(found_key)


def _parse_trip_array(vehicle, trip_array, tz):
    """
    Преобразует массив из отчета Incomtek в словарь данных рейса.
    Индексы: [3]=ts_start, [4]=start_zone, [5]=duration, [6]=distance,
             [7]=ts_arrival, [8]=end_zone, [9]=ts_end, [10]=dwell_duration
    """
    ts_start = int(trip_array[3])
    ts_arrival = int(trip_array[7])

    dt_start = datetime.fromtimestamp(ts_start, tz)
    dt_arrival = datetime.fromtimestamp(ts_arrival, tz)

    duration = int(trip_array[5])
    distance = float(trip_array[6])

    start_zone = str(trip_array[4])
    end_zone = str(trip_array[8])

    ts_end = int(trip_array[9]) if len(trip_array) > 9 and trip_array[9] else ts_arrival
    dt_end = datetime.fromtimestamp(ts_end, tz)

    dwell_seconds = int(trip_array[10]) if len(trip_array) > 10 and trip_array[10] else 0

    return {
        "vehicle": vehicle,
        "start_at": dt_start,
        "arrival_at": dt_arrival,
        "end_at": dt_end,
        "start_zone_name": start_zone,
        "end_zone_name": end_zone,
        "trip_duration_seconds": duration,
        "dwell_duration_seconds": dwell_seconds,
        "distance_km": distance,
    }


def fetch_api_trips_for_vehicle(vehicle, start_dt, end_dt, pilot_client=None, persist=True):
    """
    Фоллбек: получить рейсы из Incomtek напрямую и (опционально) сохранить в Trip.
    """
    if not vehicle or not vehicle.veh_id:
        return []

    client = pilot_client or PilotClient()
    tz = pytz.timezone(settings.TIME_ZONE)

    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    response = client.get_report(
        report_type=60,
        veh_id=str(vehicle.veh_id),
        start=start_ts,
        stop=end_ts,
        explode=1,
        group=2
    )

    if not response.get("success"):
        logger.error(f"Incomtek API Error: {response.get('msg')}")
        return []

    root_data = response.get("data", {})
    trips_map = _find_trips_map_for_vehicle(vehicle, root_data)
    if not trips_map:
        return []

    trips_list = []
    for ts_start_key, trip_array in trips_map.items():
        if isinstance(trip_array, list) and len(trip_array) > 8:
            try:
                trip_data = _parse_trip_array(vehicle, trip_array, tz)
                trips_list.append(trip_data)

                if persist:
                    raw_uid = f"{vehicle.id}_{int(trip_data['start_at'].timestamp())}"
                    uid = hashlib.md5(raw_uid.encode('utf-8')).hexdigest()
                    Trip.objects.update_or_create(
                        uid=uid,
                        defaults=trip_data
                    )
            except (ValueError, TypeError, IndexError) as e:
                logger.error(f"Error parsing trip array for {vehicle}: {e}")

    return trips_list


def _trip_distance_km(trip):
    if hasattr(trip, "distance_km"):
        return float(trip.distance_km or 0)
    return float(trip.get("distance_km") or 0)


def _trip_start_at(trip):
    if hasattr(trip, "start_at"):
        return trip.start_at
    return trip.get("start_at")


def _trip_arrival_at(trip):
    if hasattr(trip, "arrival_at"):
        return trip.arrival_at
    return trip.get("arrival_at")


def _trip_start_zone(trip):
    if hasattr(trip, "start_zone_name"):
        return trip.start_zone_name
    return trip.get("start_zone_name")


def _trip_end_zone(trip):
    if hasattr(trip, "end_zone_name"):
        return trip.end_zone_name
    return trip.get("end_zone_name")


def _trip_duration_seconds(trip):
    if hasattr(trip, "trip_duration_seconds"):
        return int(trip.trip_duration_seconds or 0)
    return int(trip.get("trip_duration_seconds") or 0)


def _trip_dwell_seconds(trip):
    if hasattr(trip, "dwell_duration_seconds"):
        return int(trip.dwell_duration_seconds or 0)
    return int(trip.get("dwell_duration_seconds") or 0)


def _resolve_object_route_from_zone_filters(zone_from_id, zone_to_id, zone_resolution_error):
    """
    Формирует подпись "Объект (откуда → куда)" по той же входной зоне-логике,
    что и /api/trips/history/ (zone_from/zone_to или legacy route_from/route_to).
    """
    if zone_resolution_error:
        return f"Ошибка: {zone_resolution_error}"

    if not zone_from_id or not zone_to_id:
        return "Выберите zone_from и zone_to для сверки"

    zones = (
        FuelZone.objects
        .filter(external_id__in=[zone_from_id, zone_to_id])
        .only("external_id", "name")
    )
    zones_by_external_id = {z.external_id: z for z in zones}
    if zone_from_id not in zones_by_external_id or zone_to_id not in zones_by_external_id:
        return "Ошибка: Zones not found"

    zone_from_name = zones_by_external_id[zone_from_id].name
    zone_to_name = zones_by_external_id[zone_to_id].name
    return f"{zone_from_name} - {zone_to_name}"


def _resolve_zone_names(zone_from_id, zone_to_id):
    if not zone_from_id or not zone_to_id:
        return None, None, None

    zones = (
        FuelZone.objects
        .filter(external_id__in=[zone_from_id, zone_to_id])
        .only("external_id", "name")
    )
    zones_by_external_id = {z.external_id: z for z in zones}
    if zone_from_id not in zones_by_external_id or zone_to_id not in zones_by_external_id:
        return None, None, "Zones not found"

    return (
        zones_by_external_id[zone_from_id].name,
        zones_by_external_id[zone_to_id].name,
        None,
    )


def _get_shift_trips_queryset(vehicle, shift, range_start_dt=None, range_end_dt=None):
    if not vehicle:
        return Trip.objects.none()

    start_dt = shift.started_at
    shift_end = shift.ended_at or timezone.now()
    end_dt = shift_end

    if range_start_dt and range_start_dt > start_dt:
        start_dt = range_start_dt
    if range_end_dt and range_end_dt < end_dt:
        end_dt = range_end_dt
    if end_dt < start_dt:
        return Trip.objects.none()

    return Trip.objects.filter(
        vehicle=vehicle,
        start_at__gte=start_dt,
        start_at__lte=end_dt,
    ).order_by("start_at", "id")


def _trip_matches_zone_pair(trip, zone_from_name, zone_to_name):
    if not zone_from_name or not zone_to_name:
        return False

    return (
        RouteAnalyticsService._name_matches(_trip_start_zone(trip), zone_from_name)
        and RouteAnalyticsService._name_matches(_trip_end_zone(trip), zone_to_name)
    )


def get_trips_with_fallback(vehicle, start_dt, end_dt, pilot_client=None, persist=True):
    """
    Получить рейсы из БД, если пусто — дернуть API.
    Возвращает список рейсов (модели Trip или dict), и источник: "db"|"api".
    """
    if not vehicle:
        return [], "none"

    qs = Trip.objects.filter(
        vehicle=vehicle,
        start_at__gte=start_dt,
        start_at__lte=end_dt
    ).order_by("start_at")

    trips = list(qs)
    if trips:
        return trips, "db"

    api_trips = fetch_api_trips_for_vehicle(
        vehicle=vehicle,
        start_dt=start_dt,
        end_dt=end_dt,
        pilot_client=pilot_client,
        persist=persist
    )
    return api_trips, "api" if api_trips else "none"


def compute_trips_stats(trips):
    count = len(trips)
    distance = sum(_trip_distance_km(t) for t in trips)
    return {
        "count": count,
        "distance_km": round(distance, 1),
    }


def build_timeline_from_trips(trips):
    timeline = []
    for trip in trips:
        dur_seconds = _trip_duration_seconds(trip)
        hours = dur_seconds // 3600
        mins = (dur_seconds % 3600) // 60
        duration_str = f"{mins} мин"
        if hours > 0:
            duration_str = f"{hours} ч {mins} мин"

        start_at = _trip_start_at(trip)
        arrival_at = _trip_arrival_at(trip)

        timeline.append({
            "zone_name": f"{_trip_start_zone(trip)} ➝ {_trip_end_zone(trip)}",
            "enter_time": timezone.localtime(start_at).strftime("%H:%M"),
            "exit_time": timezone.localtime(arrival_at).strftime("%H:%M"),
            "date": timezone.localtime(start_at).strftime("%d-%m-%Y"),
            "duration": duration_str,
            "distance": f"{_trip_distance_km(trip)} км",
            "coordinates": {"lat": None, "lon": None},
            "status": "completed"
        })

        dwell_seconds = _trip_dwell_seconds(trip)
        if dwell_seconds > 60:
            dwell_h = dwell_seconds // 3600
            dwell_m = (dwell_seconds % 3600) // 60
            timeline.append({
                "zone_name": f"Стоянка: {_trip_end_zone(trip)}",
                "enter_time": timezone.localtime(arrival_at).strftime("%H:%M"),
                "exit_time": timezone.localtime(arrival_at).strftime("%H:%M"),
                "date": timezone.localtime(arrival_at).strftime("%d-%m-%Y"),
                "duration": f"{dwell_h}ч {dwell_m}мин",
                "distance": "-",
                "coordinates": {"lat": None, "lon": None},
                "status": "waiting"
            })

    return timeline


def get_trips_statistics():
    """
    Общая статистика для верхних карточек дашборда.
    """
    qs_shifts = DriverShift.objects.all()

    total_shifts = qs_shifts.count()
    active_shifts = qs_shifts.filter(status="active").count()
    completed_shifts = qs_shifts.filter(status="completed").count()
    claimed_trips = qs_shifts.aggregate(t=Coalesce(Sum("trips_count"), 0))["t"]

    verified_trips_count = Trip.objects.count()
    verified_distance = qs_shifts.aggregate(d=Coalesce(Sum("distance_km"), 0.0))["d"]

    return {
        "total_shifts": total_shifts,
        "active_shifts": active_shifts,
        "completed_shifts": completed_shifts,
        "claimed_trips": claimed_trips,
        "verified_trips": verified_trips_count,
        "verified_distance_km": round(verified_distance, 1),
    }


def get_filtered_trips_data(filters: dict, include_object_route: bool = False):
    """
    Гибридный отчет: Смены Водителей + Данные GPS.
    """
    date_from = _parse_filter_date(filters.get("date_from"))
    date_to = _parse_filter_date(filters.get("date_to"))
    date_from, date_to = _normalize_filter_date_range(date_from, date_to)
    range_start_dt, range_end_dt = _build_filter_datetime_range(date_from, date_to)
    status_filter = filters.get("status")
    plate_filter = filters.get("plate_number")
    raw_type_filter = filters.get("vehicle_type")
    driver_filter = filters.get("driver_name")

    qs = (
        DriverShift.objects
        .select_related("driver", "vehicle")
        .prefetch_related(
            Prefetch(
                "driver__vehicles",
                queryset=VehicleAssignment.objects.select_related("vehicle").order_by("-assigned_at", "-id"),
                to_attr="report_vehicle_assignments",
            )
        )
        .order_by("-started_at", "-id")
    )

    if range_end_dt:
        qs = qs.filter(started_at__lte=range_end_dt)
    if range_start_dt:
        qs = qs.filter(Q(ended_at__isnull=True) | Q(ended_at__gte=range_start_dt))

    if status_filter:
        qs = qs.filter(status=status_filter)

    if driver_filter:
        qs = qs.filter(
            Q(driver__first_name__icontains=driver_filter) |
            Q(driver__surname__icontains=driver_filter)
        )

    target_type_key = None
    if raw_type_filter:
        target_type_key = normalize_vehicle_type(
            REVERSE_VEHICLE_LABELS.get(raw_type_filter, raw_type_filter)
        )

    raw_shifts = list(qs)
    shifts = _deduplicate_shifts(raw_shifts)
    if len(raw_shifts) != len(shifts):
        logger.warning(
            "Trips history dedupe: suppressed %s duplicate rows (requested=%s).",
            len(raw_shifts) - len(shifts),
            len(raw_shifts),
        )

    # New Logic: Report 173 On-Demand
    zone_from_id, zone_from_error = _resolve_zone_external_id(
        zone_id_raw=filters.get("zone_from"),
        zone_name_raw=filters.get("route_from"),
    )
    zone_to_id, zone_to_error = _resolve_zone_external_id(
        zone_id_raw=filters.get("zone_to"),
        zone_name_raw=filters.get("route_to"),
    )

    zone_filter_requested = any(
        [
            filters.get("zone_from"),
            filters.get("zone_to"),
            filters.get("route_from"),
            filters.get("route_to"),
        ]
    )
    zone_resolution_error = zone_from_error or zone_to_error
    if zone_filter_requested and not zone_resolution_error and (not zone_from_id or not zone_to_id):
        zone_resolution_error = "Both zones are required"

    zone_from_name = None
    zone_to_name = None
    if zone_from_id and zone_to_id and not zone_resolution_error:
        zone_from_name, zone_to_name, zone_names_error = _resolve_zone_names(zone_from_id, zone_to_id)
        if zone_names_error:
            zone_resolution_error = zone_names_error

    route_stats_map = {}
    analytics_service = None
    should_filter_by_route = bool(zone_from_id and zone_to_id and not zone_resolution_error)
    if should_filter_by_route:
        analytics_service = RouteAnalyticsService()

    object_route_label = None
    if include_object_route:
        object_route_label = _resolve_object_route_from_zone_filters(
            zone_from_id=zone_from_id,
            zone_to_id=zone_to_id,
            zone_resolution_error=zone_resolution_error,
        )

    result = []

    for shift in shifts:
        current_vehicle = _find_vehicle_for_shift(shift)

        if not _filter_match_vehicle(current_vehicle, plate_filter, target_type_key):
            continue

        vehicle_data = None
        if current_vehicle:
            frontend_veh = FrontendVehicleSerializer.from_vehicle_obj(current_vehicle)
            v_type = normalize_vehicle_type(current_vehicle.type) or "Unknown"
            vehicle_data = {
                "id": current_vehicle.veh_id,
                "name": frontend_veh["name"],
                "plate_number": frontend_veh["number"],
                "type": v_type,
                "type_label": get_vehicle_type_label(v_type) or v_type
            }

        # Default values (Report 60 DISABLED)
        verified_count = 0
        # Дистанция берется из DriverShift, который уже заполняется через report_type=4.
        verified_dist = float(shift.distance_km or 0.0)
        route_text = "Выберите zone_from и zone_to для сверки"
        verification_status = "none" # none, ok, error

        # 1. Если выбраны зоны -> используем данные из 173
        if zone_resolution_error:
            route_text = f"Ошибка: {zone_resolution_error}"
            verification_status = "error"
        elif zone_from_id and zone_to_id:
            shift_trips_qs = _get_shift_trips_queryset(
                current_vehicle,
                shift,
                range_start_dt=range_start_dt,
                range_end_dt=range_end_dt,
            )
            matched_shift_trips = [
                trip for trip in shift_trips_qs
                if _trip_matches_zone_pair(trip, zone_from_name, zone_to_name)
            ]
            if matched_shift_trips:
                route_stats_map[shift.id] = {
                    "count": len(matched_shift_trips),
                    "distance_km": round(sum(_trip_distance_km(trip) for trip in matched_shift_trips), 1),
                    "validated": True,
                    "error": None,
                }
            elif should_filter_by_route and analytics_service and shift.id not in route_stats_map:
                route_stats_map.update(
                    analytics_service.get_verified_route_stats([shift], zone_from_id, zone_to_id)
                )

            stats = route_stats_map.get(shift.id, {})
            if stats.get("error"):
                if should_filter_by_route:
                    continue
                route_text = f"Ошибка: {stats['error']}"
                verification_status = "error"
            else:
                verified_count = stats.get("count", 0)
                if should_filter_by_route and verified_count <= 0:
                    continue
                verification_status = "ok"
                if verified_count > 0:
                    route_text = f"Рейсов: {verified_count}"
                else:
                    route_text = "Рейсов не найдено"
        
        # 2. Иначе (зоны не выбраны) -> можно показать 0 или null
        # Оставляем verified_count=0, route_text="Выберите зоны..."

        row_data = {
            "id": shift.id,
            "vehicle": vehicle_data,
            "driver": f"{shift.driver.first_name} {shift.driver.surname}".strip(),
            "work_time": {
                "start": format_date(shift.started_at),
                "end": format_date(shift.ended_at),
                "movement_start": format_date(shift.movement_started_at or shift.started_at),
            },
            "stats": {
                "claimed_trips": shift.trips_count or 0,
                "trips_note": shift.trips_note,
                "verified_trips": verified_count, 
                "verified_distance_km": round(verified_dist, 1),
                "verification_status": verification_status,
                "engine_hours": round(float(shift.engine_hours or 0.0), 1),
                "late_minutes": shift.late_minutes_effective,
                "late_label": shift.late_label,
                "worked_norm_minutes": shift.worked_norm_minutes_effective,
                "worked_norm_label": shift.worked_norm_label,
            },
            "route": {
                "type": "gps_verified" if verification_status == "ok" else "manual",
                "description": route_text
            },
            "status": shift.status,
            "started_at_dt": shift.started_at,
        }

        if include_object_route:
            row_data["object_route"] = object_route_label
            row_data["work_type"] = ""
            row_data["note"] = (shift.trips_note or "").strip()

        result.append(row_data)

    return result


def _find_vehicle_for_shift(shift):
    """
    Попытка найти, какая машина была у водителя в момент начала смены.
    """
    driver = shift.driver
    if shift.vehicle:
        return shift.vehicle

    assignments = getattr(driver, "report_vehicle_assignments", None)
    if assignments is None:
        assignments = (
            driver.vehicles
            .select_related("vehicle")
            .order_by("-assigned_at", "-id")
        )

    for assign in assignments:
        if assign.assigned_at <= shift.started_at:
            if assign.unassigned_at is None or assign.unassigned_at >= shift.started_at:
                return assign.vehicle
    return None


def _filter_match_vehicle(vehicle, plate_filter, type_filter_key):
    """Проверка фильтров по машине"""
    if not plate_filter and not type_filter_key:
        return True

    if not vehicle:
        return False

    matches_plate = True
    matches_type = True

    if plate_filter:
        raw_plate = vehicle.plate_number or ""
        if plate_filter.lower() not in raw_plate.lower():
            matches_plate = False

    if type_filter_key:
        veh_type = normalize_vehicle_type(vehicle.type) or ""
        if type_filter_key.lower() != veh_type.lower():
            matches_type = False

    return matches_plate and matches_type
