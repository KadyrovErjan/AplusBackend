import asyncio
import logging
import math
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "aplus_backend.settings")
django.setup()

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.utils import timezone

from fuels.services import register_fuel_event
from services.notification_service import send_notification
from services.pilot_client import pilot_request
from services.pilot_parsers import (
    extract_istatus_engine_hours,
    extract_istatus_fuel_liters,
    extract_istatus_odometer_km,
)
from vehicles.models import Vehicle, VehicleStatusHistory

ENABLE_REALTIME_FUEL_EVENTS = os.getenv("ENABLE_REALTIME_FUEL_EVENTS", "0") == "1"
ENABLE_REALTIME_DRAIN_ALERTS = os.getenv("ENABLE_REALTIME_DRAIN_ALERTS", "0") == "1"

LAST_FUEL_STATE = {}
POLL_INTERVAL = 10.0

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("polling_worker")


async def fetch_status(vehicle: Vehicle):
    ts = int(timezone.now().timestamp())

    status_task = pilot_request("status", 14, {"imei": vehicle.imei})
    istatus_task = pilot_request("istatus", 14, {"imei": vehicle.imei, "unixtimestamp": ts})

    status_data, istatus_data = await asyncio.gather(status_task, istatus_task, return_exceptions=True)

    if isinstance(status_data, Exception):
        log.error(f"Error fetching status for {vehicle.imei}: {status_data}")
        return None

    arr = status_data.get("data") or status_data.get("list") or []
    if not arr:
        return None

    row = arr[0]
    status = row.get("status", {})
    sensors = row.get("sensors_status", [])

    fuel = None
    for sensor in sensors:
        if sensor.get("name") == "топливо":
            try:
                fuel = float(sensor.get("dig_value"))
            except Exception:
                fuel = None
            break

    engine_hours = None
    mileage = None
    if not isinstance(istatus_data, Exception):
        if fuel is None:
            fuel = extract_istatus_fuel_liters(istatus_data)

        parsed_engine_hours = extract_istatus_engine_hours(istatus_data)
        if parsed_engine_hours is not None:
            engine_hours = parsed_engine_hours
            log.debug(f"{vehicle.name}: engine_hours={engine_hours:.2f}h from istatus")

        parsed_mileage = extract_istatus_odometer_km(istatus_data)
        if parsed_mileage is not None:
            mileage = int(parsed_mileage)
            log.debug(f"{vehicle.name}: mileage={mileage}km from istatus")

        if mileage is None:
            try:
                fallback_istatus = await pilot_request("istatus", 1, {"imei": vehicle.imei, "unixtimestamp": ts})
            except Exception as exc:
                log.error(f"Fallback istatus failed for {vehicle.imei}: {exc}")
            else:
                if fuel is None:
                    fuel = extract_istatus_fuel_liters(fallback_istatus)

                if engine_hours is None:
                    fallback_engine_hours = extract_istatus_engine_hours(fallback_istatus)
                    if fallback_engine_hours is not None:
                        engine_hours = fallback_engine_hours

                fallback_mileage = extract_istatus_odometer_km(fallback_istatus)
                if fallback_mileage is not None:
                    mileage = int(fallback_mileage)
                    log.debug(f"{vehicle.name}: mileage={mileage}km from fallback istatus")
    else:
        log.error(f"Error fetching istatus for {vehicle.imei}: {istatus_data}")

    try:
        lat = float(status.get("lat"))
        lon = float(status.get("lon"))
    except (TypeError, ValueError):
        lat, lon = None, None

    return {
        "ts": int(status.get("unixtimestamp", 0)),
        "lat": lat,
        "lon": lon,
        "speed": float(status.get("speed") or 0),
        "ignition": int(status.get("firing", 0)) == 1,
        "fuel": fuel,
        "engine_hours": engine_hours,
        "mileage": mileage,
    }


@sync_to_async
def get_all_vehicles():
    return list(Vehicle.objects.all())


@sync_to_async
def update_vehicle(vehicle: Vehicle, s: dict):
    new_ignition = s["ignition"]

    if vehicle.ignition != new_ignition or vehicle.last_ignition_change_at is None:
        vehicle.last_ignition_change_at = timezone.now()

    vehicle.lat = s["lat"]
    vehicle.lon = s["lon"]
    vehicle.speed = s["speed"]
    vehicle.fuel = s["fuel"]
    vehicle.ignition = new_ignition

    if s.get("engine_hours") is not None:
        vehicle.total_engine_hours = s["engine_hours"]

    if s.get("mileage") is not None:
        vehicle.mileage = s["mileage"]

    vehicle.save(
        update_fields=[
            "lat",
            "lon",
            "speed",
            "fuel",
            "ignition",
            "last_ignition_change_at",
            "total_engine_hours",
            "mileage",
        ]
    )


def haversine(lat1, lon1, lat2, lon2):
    radius = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@sync_to_async
def save_history_if_needed(vehicle: Vehicle, s: dict):
    last = VehicleStatusHistory.objects.filter(vehicle=vehicle).order_by("-ts").first()
    if not last:
        return VehicleStatusHistory.objects.create(
            vehicle=vehicle,
            ts=s["ts"],
            lat=s["lat"],
            lon=s["lon"],
            speed=s["speed"],
            ignition=s["ignition"],
            fuel=s["fuel"],
        )

    if s["ts"] - last.ts < 5:
        return None
    dist = haversine(last.lat, last.lon, s["lat"], s["lon"])
    if dist < 1:
        return None

    return VehicleStatusHistory.objects.create(
        vehicle=vehicle,
        ts=s["ts"],
        lat=s["lat"],
        lon=s["lon"],
        speed=s["speed"],
        ignition=s["ignition"],
        fuel=s["fuel"],
    )


async def process_vehicle(channel_layer, vehicle: Vehicle):
    try:
        status = await fetch_status(vehicle)
        if not status:
            return

        prev = LAST_FUEL_STATE.get(vehicle.id)

        if prev and status["fuel"] is not None and prev["fuel"] is not None:
            delta = status["fuel"] - prev["fuel"]
            dt = status["ts"] - prev["ts"]

            if ENABLE_REALTIME_FUEL_EVENTS and delta >= 20 and dt <= 300 and status["speed"] <= 1:
                log.info(f"⛽ FUEL REFILL: {vehicle.name} +{delta}L")
                await sync_to_async(register_fuel_event)(
                    vehicle=vehicle,
                    ts=status["ts"],
                    liters=delta,
                    lat=status["lat"],
                    lon=status["lon"],
                )

            if ENABLE_REALTIME_DRAIN_ALERTS and delta <= -10 and dt <= 600 and status["speed"] <= 1:
                log.warning(f"⚠️ FUEL DRAIN: {vehicle.name} {delta}L")
                await sync_to_async(send_notification)(
                    type_code="warning",
                    title="Обнаружен слив топлива",
                    message=(
                        f"{vehicle.plate_number or vehicle.name} — "
                        f"зафиксирован слив {abs(round(delta, 1))} л."
                    ),
                    vehicle=vehicle,
                )

        LAST_FUEL_STATE[vehicle.id] = {"fuel": status["fuel"], "ts": status["ts"]}

        await update_vehicle(vehicle, status)

        if status["lat"] is None or status["lon"] is None:
            return

        obj = await save_history_if_needed(vehicle, status)
        if not obj:
            return

        payload = {
            "type": "vehicle.update",
            "data": {
                "vehicle_id": vehicle.veh_id,
                "lat": obj.lat,
                "lon": obj.lon,
                "speed": obj.speed,
                "ignition": obj.ignition,
                "fuel": obj.fuel,
                "status": vehicle.status,
                "ts": obj.ts,
            }
        }
        await channel_layer.group_send("vehicles", {"type": "send_update", "data": payload})
        await channel_layer.group_send(f"vehicle_{vehicle.veh_id}", {"type": "send_update", "data": payload})

    except Exception:
        log.exception(f"❌ error polling vehicle {vehicle.veh_id}")


async def polling_loop():
    log.info("🚀 polling_worker started")
    channel_layer = get_channel_layer()
    while True:
        try:
            vehicles = await get_all_vehicles()
            tasks = [process_vehicle(channel_layer, v) for v in vehicles]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:
            log.exception("polling loop crashed")
            await asyncio.sleep(10)
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(polling_loop())
