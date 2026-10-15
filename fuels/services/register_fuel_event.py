from datetime import datetime
from django.utils.timezone import make_aware
from fuels.models import FuelEvent
from fuels.utils.geo import find_zone_for_point
from fuels.services.zone_resolver import resolve_zone_from_run


def register_fuel_event(vehicle, ts, liters, lat=None, lon=None):
    """
    Сохраняет событие топлива из Polling Worker.
    Преобразует timestamp в datetime и мапит поля.
    """

    try:
        created_at = make_aware(datetime.fromtimestamp(ts))
    except Exception:
        from django.utils import timezone
        created_at = timezone.now()

    event_type = 1 if liters > 0 else 2

    zone = None
    zone_name = "Вне объекта"
    if lat is not None and lon is not None:
        zone = find_zone_for_point(lat, lon)
        if zone:
            zone_name = zone.name

    if not zone and vehicle and ts:
        run_zone, run_name = resolve_zone_from_run(vehicle.imei, ts)
        if run_zone:
            zone = run_zone
            zone_name = run_zone.name
        elif run_name:
            zone_name = run_name

    return FuelEvent.objects.create(
        vehicle=vehicle,
        created_at=created_at, 
        liters_from_ink=liters, 
        lat=lat,
        lon=lon,
        event_type=event_type,
        zone=zone,
        zone_name=zone_name
    )
