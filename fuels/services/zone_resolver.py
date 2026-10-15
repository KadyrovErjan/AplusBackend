from typing import Optional, Tuple

from fuels.models import FuelZone
from fuels.services.processor import _call_incomtek_run, _pick_zone_from_run


def resolve_zone_from_run(imei: str, ts: int) -> Tuple[Optional[FuelZone], Optional[str]]:
    """
    Пытается определить зону через Incomtek run (zones=1).
    Возвращает (FuelZone или None, zone_name или None).
    """
    if not imei or not ts:
        return None, None

    try:
        run_json = _call_incomtek_run(imei, ts)
    except Exception:
        return None, None

    zone = _pick_zone_from_run(run_json)
    if not zone:
        return None, None

    ext_id = zone.get("startuid") or zone.get("stopuid") or zone.get("uid") or zone.get("id")
    zone_name = zone.get("startname") or zone.get("stopname") or zone.get("name")

    zone_obj = None
    if ext_id:
        zone_obj = FuelZone.objects.filter(external_id=ext_id).first()

    if zone_obj:
        return zone_obj, zone_obj.name

    return None, zone_name
