from rest_framework import serializers
from .models import Vehicle, VehicleStatusHistory, FuelLevelHistory, TrackPoint, Event, Repair, VehicleAssignment, DriverShift
from django.contrib.auth import get_user_model
from django.db.models import Sum, F
from django.utils import timezone
from common.serializers import build_file_url
import re
from .type_mapping import (
    VEHICLE_TYPE_LABELS,
    get_vehicle_type_label,
    normalize_vehicle_type,
)

User = get_user_model()

VEHICLE_FUEL_CAPACITY_DEFAULT = 800.0

KM_SERVICE_TYPES = [
    'Lorry', 'Car', 'Light Truck', 'Truck',
]

DEFAULT_KM_INTERVAL = 5000

RUS_TO_LAT = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M",
    "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T",
    "У": "Y", "Х": "X"
})


class UserShortSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'full_name']

    def get_full_name(self, obj):
        return f"{obj.first_name or ''} {obj.surname or ''}".strip() or obj.username


class FrontendVehicleSerializer(serializers.Serializer):

    @staticmethod
    def _find_plate(text: str):
        if not text: return None
        t = text.upper().translate(RUS_TO_LAT)
        t = re.sub(r'[_()\-]', ' ', t)
        t = re.sub(r'\s+', ' ', t)
        m = re.search(r'\b\d{2}KG[0-9A-Z]{3,6}\b', t)
        if m: return m.group(0)
        return None

    @staticmethod
    def _clean_name(original: str, plate: str | None):
        if not original: return None
        s = original.translate(RUS_TO_LAT)
        s = re.sub(r'[_()\-]', ' ', s)
        if plate:
            s = re.sub(re.escape(plate), ' ', s, flags=re.IGNORECASE)
        s = re.sub(r'\s+', ' ', s).strip()
        s = re.sub(r'^[ОO]\s+', '', s)
        return s.strip() or None

    @staticmethod
    def split_name_and_plate(v: 'Vehicle'):
        plate = None
        if getattr(v, "plate_number", None):
            plate = FrontendVehicleSerializer._find_plate(str(v.plate_number))
        if not plate:
            plate = FrontendVehicleSerializer._find_plate(v.name or "")

        clean_name = FrontendVehicleSerializer._clean_name(v.name or "", plate)
        return clean_name or v.name, plate

    @staticmethod
    def from_vehicle_obj(v: 'Vehicle', capacity: float = VEHICLE_FUEL_CAPACITY_DEFAULT):
        clean_name, plate = FrontendVehicleSerializer.split_name_and_plate(v)

        driver = None
        try:
            if hasattr(v, 'current_assignment') and v.current_assignment:
                assign = v.current_assignment[0]
            else:
                assign = VehicleAssignment.objects.filter(vehicle=v, unassigned_at__isnull=True).select_related(
                    "driver").first()
            if assign and assign.driver:
                user = assign.driver
                driver = f"{(user.first_name or user.username).strip()} {user.surname or ''}".strip() or None
        except Exception:
            driver = None

        fuel = v.fuel_level
        fuel_value = 0
        if fuel is not None:
            try:
                fuel_value = int(round(float(fuel)))
            except (ValueError, TypeError):
                fuel_value = 0

        return {
            "name": clean_name or v.name,
            "number": plate,
            "driver": driver,
            "fuel_level": fuel_value
        }


class VehicleCardSerializer(serializers.Serializer):
    id = serializers.IntegerField(source="veh_id")
    name = serializers.SerializerMethodField()
    plate_number = serializers.SerializerMethodField()

    type = serializers.SerializerMethodField()
    type_label = serializers.SerializerMethodField()

    status = serializers.SerializerMethodField()
    fuel_level = serializers.SerializerMethodField()
    driver = serializers.SerializerMethodField()
    location = serializers.SerializerMethodField()
    mileage = serializers.SerializerMethodField()

    def _frontend(self, obj):
        return FrontendVehicleSerializer.from_vehicle_obj(obj)

    def get_name(self, obj):
        return self._frontend(obj).get("name")

    def get_plate_number(self, obj):
        return self._frontend(obj).get("number")

    def get_fuel_level(self, obj):
        return self._frontend(obj).get("fuel_level")

    def get_type(self, obj):
        return normalize_vehicle_type(obj.type)

    def get_type_label(self, obj):
        return get_vehicle_type_label(obj.type) or obj.type

    def get_status(self, obj):
        return obj.status

    def get_driver(self, obj):
        assign = None
        if hasattr(obj, 'current_assignment') and obj.current_assignment:
            assign = obj.current_assignment[0]
        else:
            assign = VehicleAssignment.objects.filter(vehicle=obj, unassigned_at__isnull=True).select_related(
                'driver').first()

        if not assign or not assign.driver: return None
        return UserShortSerializer(assign.driver).data

    def get_location(self, obj):
        address = obj.address or obj.location_name or "Нет адреса"
        return {
            "lat": obj.lat,
            "lon": obj.lon,
            "address": address
        }

    def get_mileage(self, obj):
        normalized_type = normalize_vehicle_type(obj.type)
        is_km_based = normalized_type in KM_SERVICE_TYPES

        if is_km_based:
            current_km = obj.mileage or 0  # for calculation only
            interval = DEFAULT_KM_INTERVAL
            cycle_pos = current_km % interval
            to_service = int(interval - cycle_pos)

            to_service_str = f"{to_service} км"

            return {
                "total_km": obj.mileage,
                "to_service_km": to_service_str,
                "engine_hours_total": obj.total_engine_hours
            }
        else:
            current_hours = obj.total_engine_hours or 0.0  # for calculation only
            interval = obj.service_interval_hours
            last = obj.last_service_hours

            if last == 0:
                cycle_pos = current_hours % interval
                to_service = int(interval - cycle_pos)
            else:
                limit = last + interval
                to_service = int(limit - current_hours)

            to_service_str = f"{to_service} м/ч"

            return {
                "total_km": obj.mileage,
                "to_service_km": to_service_str,
                "engine_hours_total": obj.total_engine_hours
            }

class MapVehicleSerializer(serializers.ModelSerializer):
    driver_name = serializers.SerializerMethodField()
    plate_number = serializers.SerializerMethodField()
    type = serializers.SerializerMethodField()
    last_ignition_change_at = serializers.SerializerMethodField()

    class Meta:
        model = Vehicle
        fields = ["veh_id", "name", "plate_number", "type", "lat", "lon", "speed", "ignition",
                  "status", "driver_name", "last_ignition_change_at"]

    def get_plate_number(self, obj):
        return FrontendVehicleSerializer.from_vehicle_obj(obj).get("number")

    def get_driver_name(self, obj):
        return FrontendVehicleSerializer.from_vehicle_obj(obj).get("driver")

    def get_type(self, obj):
        return get_vehicle_type_label(obj.type)

    def get_last_ignition_change_at(self, obj):
        if not obj.last_ignition_change_at:
            return None
        return timezone.localtime(obj.last_ignition_change_at).strftime("%d-%m-%Y")




class VehicleDetailSerializer(serializers.Serializer):
    id = serializers.IntegerField(source="veh_id")
    name = serializers.SerializerMethodField()
    number = serializers.SerializerMethodField()
    type = serializers.CharField(allow_null=True)
    driver = serializers.SerializerMethodField()
    driverId = serializers.SerializerMethodField()
    telegram_id = serializers.SerializerMethodField()
    fuelLevel = serializers.SerializerMethodField()
    status = serializers.CharField()
    mileage = serializers.IntegerField(allow_null=True)
    lastService = serializers.IntegerField(source="mileage", allow_null=True)
    nextService = serializers.SerializerMethodField()
    location = serializers.CharField(source="location_name", allow_null=True)
    latitude = serializers.FloatField(source="lat", allow_null=True)
    longitude = serializers.FloatField(source="lon", allow_null=True)
    vin = serializers.CharField(allow_null=True)
    purchaseDate = serializers.DateField(source="purchase_date", allow_null=True)
    photos = serializers.SerializerMethodField()
    techPassportPhotos = serializers.SerializerMethodField()
    engineHoursTotal = serializers.FloatField(source="total_engine_hours", allow_null=True)
    maintenanceCostPerMonth = serializers.SerializerMethodField()
    fuelConsumptionPerKm = serializers.SerializerMethodField()
    avgKmPerMonth = serializers.SerializerMethodField()

    def _frontend(self, obj):
        return FrontendVehicleSerializer.from_vehicle_obj(obj)

    def get_name(self, obj):
        return self._frontend(obj).get("name")

    def get_number(self, obj):
        return self._frontend(obj).get("number")

    def _get_assignment(self, obj):
        return VehicleAssignment.objects.filter(vehicle=obj, unassigned_at__isnull=True).select_related(
            "driver").first()

    def get_driver(self, obj):
        a = self._get_assignment(obj)
        if not a: return None
        u = a.driver
        return f"{u.first_name} {u.surname}".strip()

    def get_driverId(self, obj):
        a = self._get_assignment(obj)
        return a.driver.id if a else None

    def get_telegram_id(self, obj):
        a = self._get_assignment(obj)
        return a.driver.telegram_id if a else None

    def get_photos(self, obj):
        a = self._get_assignment(obj)
        if not a: return []
        request = self.context.get("request")
        return [build_file_url(request, a.driver.selfie)] if a.driver.selfie else []

    def get_techPassportPhotos(self, obj):
        a = self._get_assignment(obj)
        if not a: return []
        request = self.context.get("request")
        photos = []
        if a.driver.passport_front: photos.append(build_file_url(request, a.driver.passport_front))
        if a.driver.passport_back: photos.append(build_file_url(request, a.driver.passport_back))
        return photos

    def get_nextService(self, obj):
        return (obj.mileage or 0) + 5000

    def get_maintenanceCostPerMonth(self, obj):
        now = timezone.now()
        repairs = Repair.objects.filter(
            vehicle=obj,
            date__gte=(now - timezone.timedelta(days=365)).date(),
        ).aggregate(
            total=Sum(F("cost_parts") + F("cost_work"))
        )
        total = repairs["total"]
        if total is None:
            return None
        return round(float(total) / 12, 2)

    def get_fuelConsumptionPerKm(self, obj):
        try:
            from fuels.models import FuelEvent
        except ImportError:
            return None
        now = timezone.now()
        since = now - timezone.timedelta(days=30)
        try:
            total_liters = FuelEvent.objects.filter(
                vehicle=obj,
                event_type=1,  
                created_at__gte=since,
            ).aggregate(total=Sum("liters_from_ink"))["total"]
        except Exception:
            return None
        total_km = DriverShift.objects.filter(
            vehicle=obj,
            status="completed",
            started_at__gte=since,
        ).aggregate(total=Sum("distance_km"))["total"]
        if not total_liters or not total_km or total_km < 1:
            return None
        return round(total_liters / total_km, 2)

    def get_avgKmPerMonth(self, obj):
        now = timezone.now()
        since = now - timezone.timedelta(days=30)
        result = DriverShift.objects.filter(
            vehicle=obj,
            status="completed",
            started_at__gte=since,
        ).aggregate(total=Sum("distance_km"))
        total = result["total"]
        if total is None:
            return None
        return round(float(total), 1)

    def get_fuelLevel(self, obj):
        if obj.fuel is not None:
            return int(round(obj.fuel))
        return 0


class VehicleStatusHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = VehicleStatusHistory
        fields = "__all__"


class FuelLevelHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = FuelLevelHistory
        fields = "__all__"


class TrackPointSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrackPoint
        fields = "__all__"


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = "__all__"


class RepairSerializer(serializers.ModelSerializer):
    class Meta:
        model = Repair
        fields = ["id", "vehicle", "date", "title", "description", "parts", "cost_parts", "cost_work", "status"]
        read_only_fields = ["id", "vehicle"]
