import logging
from datetime import datetime
import pytz
from django.conf import settings

from fuels.models import FuelEvent, FuelEventType
from services.pilot_client import PilotClient
from fuels.utils.geo import find_zone_for_point
from services.notification_service import send_notification

logger = logging.getLogger(__name__)


def _to_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def import_fuel_from_incomtek(vehicle, start_ts, end_ts):
    """
    Импортирует заправки/сливы из Incomtek cmd=fuel.
    Источник события: response.fillings (type_id: 1=заправка, 2=слив).
    """
    if not vehicle.imei:
        return 0

    client = PilotClient()

    response = client.get_fuel_report(
        imei=str(vehicle.imei),
        start=start_ts,
        stop=end_ts,
        addr=1,
        node=1,
    )

    if not response.get("success"):
        logger.error(f"Incomtek fuel cmd failed for {vehicle}: {response.get('msg')}")
        return 0

    fillings = response.get("fillings") or {}
    if not isinstance(fillings, dict) or not fillings:
        return 0

    tz = pytz.timezone(settings.TIME_ZONE)
    created_count = 0

    for _, row in fillings.items():
        if not isinstance(row, dict):
            continue

        try:
            timestamp = int(row.get("ts"))
            liters = _to_float(row.get("liters"))
            if liters is None or liters <= 0:
                continue

            raw_type_id = str(row.get("type_id"))
            if raw_type_id == "1":
                event_type = FuelEventType.FUELING
                liters = abs(liters)
                if liters < 5.0:
                    continue
            elif raw_type_id == "2":
                event_type = FuelEventType.DRAIN
                liters = abs(liters)
            else:
                continue

            event_dt = datetime.fromtimestamp(timestamp, tz)

            lat = _to_float(row.get("lat"))
            lon = _to_float(row.get("lon"))
            address = row.get("address")

            zone = None
            zone_name = None
            if lat is not None and lon is not None:
                zone = find_zone_for_point(lat, lon)
                if zone:
                    zone_name = zone.name

            defaults_data = {
                "liters_from_ink": liters,
                "lat": lat,
                "lon": lon,
                "address": address,
                "zone": zone,
                "zone_name": zone_name or address,
                "incomtek_zone_id": row.get("zone_id"),
            }

            try:
                fuel_event, created = FuelEvent.objects.update_or_create(
                    vehicle=vehicle,
                    created_at=event_dt,
                    event_type=event_type,
                    defaults=defaults_data
                )
            except FuelEvent.MultipleObjectsReturned:
                duplicates = FuelEvent.objects.filter(
                    vehicle=vehicle,
                    created_at=event_dt,
                    event_type=event_type,
                ).order_by("id")
                first_event = duplicates.first()
                duplicates.exclude(id=first_event.id).delete()
                for key, value in defaults_data.items():
                    setattr(first_event, key, value)
                first_event.save(update_fields=list(defaults_data.keys()))
                fuel_event = first_event
                created = False

            if created:
                created_count += 1

            if created and fuel_event.event_type == FuelEventType.DRAIN:
                vehicle_name = vehicle.plate_number or vehicle.name
                send_notification(
                    type_code="warning",
                    title="Обнаружен слив топлива",
                    message=(
                        f"{vehicle_name} — слив {round(float(fuel_event.liters_from_ink or 0), 2)} л "
                        f"в {event_dt.strftime('%d-%m-%Y %H:%M')}"
                    ),
                    vehicle=vehicle,
                )

        except (ValueError, TypeError) as e:
            logger.error(f"Error parsing fuel row for {vehicle}: {e}")
            continue

    return created_count
