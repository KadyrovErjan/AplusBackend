"""
DEPRECATED: Этот модуль заменён на trips/services.py.

- get_trip_data_from_pilot() использовал cmd=run — теперь не используется
- get_trips_history() заменён на trips/services.py::get_filtered_trips_data()
- format_date() дублируется в trips/services.py::format_date()

Оставлен для обратной совместимости. Не добавляйте новый код сюда.
"""
from vehicles.models import DriverShift
from django.utils import timezone
from vehicles.serializers import FrontendVehicleSerializer
import logging
from services.pilot_client import pilot_request_sync

log = logging.getLogger(__name__)


def format_date(dt):
    if not dt: return None
    return timezone.localtime(dt).strftime("%d-%m-%Y %H:%M")


def get_trip_data_from_pilot(imei: str, start_ts: int, end_ts: int) -> dict:
    """
    Запрашивает трек, собирает историю посещения зон и считает моточасы.
    """
    try:
        run_data = pilot_request_sync(
            cmd="run",
            node=1,
            params={"imei": imei, "start": start_ts, "stop": end_ts, "zones": 1}
        )
    except Exception as e:
        log.error(f"Pilot API error (run): {e}")
        run_data = {}

    tracks = run_data.get("tracks", {})
    total_km = 0.0
    track_items = list(tracks.values()) if isinstance(tracks, dict) else (tracks or [])

    for t in track_items:
        total_km += float(t.get("length", 0) or 0)

    zones_raw = run_data.get("zones", {})
    zones_list = []

    if isinstance(zones_raw, dict):
        for ts_key, z in zones_raw.items():
            z["ts"] = int(ts_key)
            zones_list.append(z)
    elif isinstance(zones_raw, list):
        zones_list = zones_raw

    zones_list.sort(key=lambda x: int(x.get("ts", 0)))

    visits = []
    current_visit = None

    for z in zones_list:
        msg_type = z.get("msgtype")
        zone_name = z.get("startname") or z.get("stopname") or "Неизвестная зона"
        ts = int(z.get("ts", 0))
        lat = z.get("lat")
        lon = z.get("lon")

        if msg_type == 1:
            if current_visit:
                visits.append(current_visit)

            current_visit = {
                "name": zone_name,
                "enter_ts": ts,
                "exit_ts": None,
                "duration": 0,
                "lat": lat,
                "lon": lon
            }

        elif msg_type == 2:
            if current_visit:
                current_visit["exit_ts"] = ts
                current_visit["duration"] = ts - current_visit["enter_ts"]
                visits.append(current_visit)
                current_visit = None
            else:
                visits.append({
                    "name": zone_name,
                    "enter_ts": start_ts, 
                    "exit_ts": ts,
                    "duration": ts - start_ts,
                    "lat": lat,
                    "lon": lon
                })

    if current_visit:
        current_visit["exit_ts"] = None
        visits.append(current_visit)

    route_from = visits[0]["name"] if visits else None
    route_to = visits[-1]["name"] if visits else None

    engine_hours = 0.0
    try:
        status_data = pilot_request_sync(
            cmd="istatus",
            node=1,
            params={"imei": imei, "start": start_ts, "stop": end_ts}
        )
        seconds = float(status_data.get("motorhours", 0) or 0)
        engine_hours = round(seconds / 3600.0, 1)
    except Exception as e:
        log.error(f"Pilot API error (istatus): {e}")

    return {
        "distance_km": round(total_km, 1),
        "engine_hours": engine_hours,
        "route_from": route_from,
        "route_to": route_to,
        "zones_data": visits,
    }


def get_trips_history():
    """
    Вывод истории смен с оптимизацией SQL запросов.
    """
    shifts = (
        DriverShift.objects
        .select_related("driver") 
        .prefetch_related("driver__vehicles__vehicle") 
        .order_by("-started_at")
    )

    result = []

    for shift in shifts:
        driver = shift.driver

        target_assignment = None
        for assign in driver.vehicles.all():
            if assign.assigned_at <= shift.started_at:
                if assign.unassigned_at is None or assign.unassigned_at >= shift.started_at:
                    target_assignment = assign
                    break 

        vehicle_data = None
        if target_assignment:
            v = target_assignment.vehicle
            frontend = FrontendVehicleSerializer.from_vehicle_obj(v)
            vehicle_data = {
                "id": v.veh_id,
                "name": frontend["name"],
                "plate_number": frontend["number"],
            }

        result.append({
            "id": shift.id,
            "vehicle": vehicle_data,
            "driver": f"{driver.first_name or ''} {driver.surname or ''}".strip(),
            "work_time": {
                "start": format_date(shift.started_at),
                "end": format_date(shift.ended_at),
            },
            "stats": {
                "trips": shift.trips_count or 0,
                "distance_km": round(shift.distance_km or 0, 1) if shift.ended_at else 0
            },
            "route": {
                "type": "zone",
                "from": shift.route_from,
                "to": shift.route_to,
            },
            "status": shift.status,
        })

    return result
