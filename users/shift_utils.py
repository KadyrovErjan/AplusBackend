import logging
from django.utils import timezone

logger = logging.getLogger(__name__)


def _haversine_km(lat1, lon1, lat2, lon2):
    from math import radians, cos, sin, asin, sqrt

    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    r = 6371
    return c * r


def calculate_shift_distance_km(vehicle, started_at, ended_at=None):
    """
    DEPRECATED: Считает километраж по GPS-истории (Haversine).
    Используйте fetch_shift_distance_from_incomtek() для точного пробега из Incomtek.
    Оставлено как fallback при недоступности API.
    """
    from vehicles.models import VehicleStatusHistory

    if not vehicle or not started_at:
        return 0.0

    end_at = ended_at or timezone.now()
    if end_at <= started_at:
        return 0.0

    start_ts = int(started_at.timestamp())
    end_ts = int(end_at.timestamp())

    points = list(
        VehicleStatusHistory.objects.filter(
            vehicle=vehicle,
            ts__gte=start_ts,
            ts__lte=end_ts,
            ignition=True,
        )
        .order_by("ts")
        .values_list("lat", "lon")
    )

    if len(points) < 2:
        return 0.0

    total_km = 0.0
    prev_lat, prev_lon = points[0]
    for lat, lon in points[1:]:
        total_km += _haversine_km(prev_lat, prev_lon, lat, lon)
        prev_lat, prev_lon = lat, lon

    return round(total_km, 3)


def fetch_shift_distance_from_incomtek(vehicle, started_at, ended_at=None):
    """
    Получает общий пробег за период из Incomtek Mileage Report (report_type=4).

    Формат ответа report_type=4:
    data: {
        "Vehicle_Name": {
            "date_range": [
                {"length": 63.81, "ts": ..., "te": ..., ...},
                {"length": 10.59, ...},
            ]
        }
    }

    Суммируем все segment.length для общего пробега.
    """
    from services.pilot_client import PilotClient

    if not vehicle or not vehicle.veh_id or not started_at:
        return 0.0

    end_at = ended_at or timezone.now()
    if end_at <= started_at:
        return 0.0

    try:
        client = PilotClient()
        response = client.get_mileage_report(
            veh_id=str(vehicle.veh_id),
            start=int(started_at.timestamp()),
            stop=int(end_at.timestamp()),
        )
    except Exception as e:
        logger.error(f"Incomtek Mileage Report error for vehicle {vehicle.veh_id}: {e}")
        return 0.0

    if not response.get('success'):
        logger.warning(f"Incomtek Mileage Report failed: {response.get('msg')}")
        return 0.0

    root_data = response.get('data', {})
    if not root_data:
        return 0.0

    total_km = 0.0

    for vehicle_name, date_ranges in root_data.items():
        if not isinstance(date_ranges, dict):
            continue
        for date_range, segments in date_ranges.items():
            if not isinstance(segments, list):
                continue
            for segment in segments:
                if isinstance(segment, dict):
                    total_km += float(segment.get('length', 0) or 0)

    return round(total_km, 3)
