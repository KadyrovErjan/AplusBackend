import time
import requests
from django.conf import settings
from django.utils import timezone
from fuels.models import FuelReport, FuelEvent
from vehicles.models import Vehicle, VehicleAssignment 
from typing import Optional

INCOMTEK_URL = getattr(settings, "INCOMTEK_API_URL", "https://pilot.incomtek.kg/api/api.php")
INCOMTEK_NODE = getattr(settings, "INCOMTEK_NODE", 1)
INCOMTEK_TIMEOUT = 8

def _call_incomtek_run(imei: str, ts: int, window_seconds: int = 60) -> dict:
    """Запрос run с zones=1 вокруг ts (стандартный поиск зоны)."""
    params = {
        "cmd": "run",
        "imei": imei,
        "start": ts - window_seconds,
        "stop": ts + window_seconds,
        "zones": 1,
        "node": INCOMTEK_NODE,
    }
    r = requests.get(INCOMTEK_URL, params=params, timeout=INCOMTEK_TIMEOUT)
    r.raise_for_status()
    return r.json()

def _pick_zone_from_run(run_json: dict) -> Optional[dict]:
    """По данным run найди последнюю зону (stopname/startname) вокруг события."""
    zones = run_json.get("zones") or {}
    if not zones:
        return None
    items = []
    for k, v in zones.items():
        try:
            t = int(k)
        except Exception:
            t = int(v.get("ts", 0))
        items.append((t, v))
    items.sort(key=lambda x: abs(x[0])) 
    _, zone = items[-1]
    return zone

def get_current_driver_for_vehicle(vehicle: Vehicle):
    """
    Попробовать найти активное назначение водителя для техники.
    Ожидаем модель VehicleAssignment с полем driver и unassigned_at.
    """
    try:
        assignment = (
            VehicleAssignment.objects
            .filter(vehicle=vehicle, unassigned_at__isnull=True)
            .select_related("driver")
            .order_by("-assigned_at")
            .first()
        )
        return assignment.driver if assignment else None
    except Exception:
        return None

def process_sensor_fill(imei: str, ts: int, liters_from_ink: float, lat: float = None, lon: float = None, address: str = None, source: str = "sensor"):
    """
    Главный entry point: вызвать, когда детектили изменение топлива (delta).
    - imei: imei устройства
    - ts: unix timestamp события (int)
    - liters_from_ink: количество литров от Incomtek-датчика (delta)
    """
    try:
        vehicle = Vehicle.objects.get(imei=imei)
    except Vehicle.DoesNotExist:
        vehicle = None

    driver = get_current_driver_for_vehicle(vehicle) if vehicle else None

    zone_id = None
    zone_name = None
    try:
        run_json = _call_incomtek_run(imei, ts)
        zone = _pick_zone_from_run(run_json)
        if zone:
            zone_id = zone.get("startuid") or zone.get("stopuid") or zone.get("uid") or zone.get("id")
            zone_name = zone.get("startname") or zone.get("stopname") or zone.get("name")
    except Exception:
        pass

    fr = FuelReport.objects.create(
        vehicle=vehicle,
        driver=driver,
        liters_from_ink=liters_from_ink,
        liters_from_driver=None, 
        zone_id=zone_id,
        zone_name=zone_name if zone_name else (address or "Вне объекта"),
        lat=lat,
        lon=lon,
        address=address,
        source=source,
        created_at=timezone.datetime.fromtimestamp(ts, tz=timezone.get_current_timezone()),
    )

    FuelEvent.objects.create(
        fuel_report=fr,
        vehicle=vehicle,
        liters_from_ink=liters_from_ink,
        zone_id=zone_id,
        zone_name=zone_name,
        created_at=fr.created_at,
    )

    return fr
