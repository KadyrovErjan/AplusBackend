import re
import time

from django.core.management.base import BaseCommand
from django.db import IntegrityError

from fuels.models import FuelZone
from fuels.utils.geo import find_zone_for_point
from services.pilot_client import pilot_request_sync
from services.pilot_parsers import (
    extract_istatus_engine_hours,
    extract_istatus_fuel_liters,
    extract_istatus_odometer_km,
)
from vehicles.models import Vehicle
from vehicles.type_mapping import normalize_vehicle_type

RUS_TO_LAT = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M",
    "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T",
    "У": "Y", "Х": "X"
})
PLATE_RE = re.compile(r"\b\d{2}KG[0-9A-Z]{3,6}\b")


def extract_plate(raw_text):
    if not raw_text:
        return None
    text = str(raw_text).upper().translate(RUS_TO_LAT)
    text = re.sub(r"[_()\-]", " ", text)
    text = re.sub(r"\s+", " ", text)
    match = PLATE_RE.search(text)
    if not match:
        return None
    return match.group(0)


class Command(BaseCommand):
    help = "Синхронизация данных транспорта и определение локации по FuelZone"

    def handle(self, *args, **options):
        geozones = list(FuelZone.objects.all())
        self.stdout.write(f"--- ИСПОЛЬЗУЕМ ГЕОЗОНЫ ИЗ FUELS: {len(geozones)} ---")

        try:
            response = pilot_request_sync("list", node=14)
            vehicles_data = response.get("list", [])
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"Ошибка API: {exc}"))
            return

        ts_now = int(time.time())
        count_updated = 0

        self.stdout.write(f"DEBUG: Получено сырых записей: {len(vehicles_data)}")

        for vehicle_data in vehicles_data:
            imei = vehicle_data.get("imei")
            vehicle_id = vehicle_data.get("id") or vehicle_data.get("veh_id") or vehicle_data.get("agentid")

            if not imei or not vehicle_id:
                continue

            vehicle_type = normalize_vehicle_type(vehicle_data.get("type") or vehicle_data.get("type_name"))
            vehicle_name = (vehicle_data.get("name") or vehicle_data.get("vehiclenumber") or "").strip() or None
            plate_number = extract_plate(vehicle_data.get("vehiclenumber") or vehicle_name)

            defaults = {
                "imei": imei,
                "name": vehicle_name,
                "type": vehicle_type,
            }
            if plate_number:
                defaults["plate_number"] = plate_number

            try:
                vehicle, _created = Vehicle.objects.update_or_create(
                    veh_id=vehicle_id,
                    defaults=defaults,
                )
            except IntegrityError:
                self.stdout.write(self.style.WARNING(f"Конфликт создания для {vehicle_id}. Пропускаем."))
                try:
                    vehicle = Vehicle.objects.get(veh_id=vehicle_id)
                except Vehicle.DoesNotExist:
                    continue

            status_data = vehicle_data.get("status", {})
            lat = float(status_data.get("lat", 0)) if status_data else float(vehicle_data.get("lat", 0))
            lon = float(status_data.get("lon", 0)) if status_data else float(vehicle_data.get("lon", 0))
            raw_mileage = vehicle_data.get("current_mileage")
            current_mileage = int(raw_mileage) if raw_mileage is not None else None

            if not vehicle.name and "vehiclenumber" in vehicle_data:
                vehicle.name = vehicle_data["vehiclenumber"]

            ignition = False
            if status_data:
                if "firing" in status_data:
                    ignition = bool(int(status_data["firing"]))
                elif "work" in status_data:
                    ignition = bool(int(status_data["work"]))

            fuel_val = None
            sensors = vehicle_data.get("sensors_status", [])
            if isinstance(sensors, list):
                for sensor in sensors:
                    sensor_name = str(sensor.get("name", "")).lower()
                    if "топливо" in sensor_name or "fuel" in sensor_name:
                        try:
                            fuel_val = float(sensor.get("dig_value", 0))
                            break
                        except Exception:
                            pass

            vehicle.lat = lat
            vehicle.lon = lon
            vehicle.mileage = current_mileage
            vehicle.ignition = ignition
            if vehicle.status not in {
                Vehicle.Status.RENTED,
                Vehicle.Status.REPAIR,
                Vehicle.Status.LOW_FUEL,
            }:
                vehicle.status = Vehicle.Status.ACTIVE if vehicle.ignition else Vehicle.Status.IDLE

            location_name = "Вне объекта"
            if lat is not None and lon is not None:
                zone = find_zone_for_point(lat, lon, zones=geozones)
                if zone:
                    location_name = zone.name

            vehicle.location_name = location_name
            vehicle.address = location_name

            try:
                istatus_resp = pilot_request_sync(
                    "istatus",
                    node=14,
                    params={
                        "imei": imei,
                        "unixtimestamp": ts_now,
                    },
                )

                if istatus_resp and str(istatus_resp.get("code", "")) == "0":
                    engine_hours = extract_istatus_engine_hours(istatus_resp)
                    if engine_hours is not None:
                        vehicle.total_engine_hours = round(engine_hours, 2)

                    odometer_km = extract_istatus_odometer_km(istatus_resp)
                    if odometer_km is not None:
                        vehicle.mileage = int(odometer_km)

                    istatus_fuel = extract_istatus_fuel_liters(istatus_resp)
                    if istatus_fuel is not None:
                        fuel_val = istatus_fuel

                    if odometer_km is None:
                        fallback_istatus = pilot_request_sync(
                            "istatus",
                            node=1,
                            params={
                                "imei": imei,
                                "unixtimestamp": ts_now,
                            },
                        )
                        if fallback_istatus and str(fallback_istatus.get("code", "")) == "0":
                            fallback_engine_hours = extract_istatus_engine_hours(fallback_istatus)
                            if engine_hours is None and fallback_engine_hours is not None:
                                vehicle.total_engine_hours = round(fallback_engine_hours, 2)

                            fallback_odometer_km = extract_istatus_odometer_km(fallback_istatus)
                            if fallback_odometer_km is not None:
                                vehicle.mileage = int(fallback_odometer_km)

                            fallback_fuel = extract_istatus_fuel_liters(fallback_istatus)
                            if fuel_val is None and fallback_fuel is not None:
                                fuel_val = fallback_fuel
            except Exception:
                pass

            if fuel_val is not None:
                vehicle.fuel_level = fuel_val
                vehicle.fuel = fuel_val

            vehicle.save()
            count_updated += 1

        self.stdout.write(self.style.SUCCESS(f"Готово! Обновлено машин: {count_updated}"))
