from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from django.db import models
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from django.db.models import Prefetch, Q, Count, Sum
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from django.http import HttpResponse
from datetime import datetime
from trips.services import get_filtered_trips_data
from .models import Vehicle, VehicleStatusHistory, FuelLevelHistory, TrackPoint, Event, Repair, VehicleAssignment
from fuels.models import FuelZone, FuelEvent
from fuels.serializers import FuelZoneSerializer
from fuels.utils.geo import is_point_in_circle, is_point_in_polygon_normalized
from django.db.models.functions import Coalesce
from vehicles.type_mapping import (
    REVERSE_VEHICLE_LABELS,
    get_vehicle_type_label,
    normalize_vehicle_type,
    resolve_vehicle_type_for_ui,
)

from .serializers import (
    VehicleStatusHistorySerializer,
    FuelLevelHistorySerializer,
    TrackPointSerializer,
    EventSerializer,
    VehicleDetailSerializer,
    RepairSerializer,
    VEHICLE_FUEL_CAPACITY_DEFAULT,
    FrontendVehicleSerializer,
    VehicleCardSerializer,
    MapVehicleSerializer,
)


def get_current_assignment_prefetch():
    return Prefetch(
        "vehicleassignment_set",
        queryset=VehicleAssignment.objects.filter(unassigned_at__isnull=True).select_related("driver"),
        to_attr="current_assignment",
    )


def filter_rented_vehicles(queryset):
    return queryset.filter(status=Vehicle.Status.RENTED)



class VehicleViewSet(viewsets.ViewSet):
    """
    Основной ViewSet для работы с техникой.
    """

    @swagger_auto_schema(
        tags=["🚚 Vehicles"],
        operation_summary="Список техники (Фильтры)",
        manual_parameters=[
            openapi.Parameter('search', openapi.IN_QUERY, description="Поиск (номер, VIN, IMEI)",
                              type=openapi.TYPE_STRING),
            openapi.Parameter('plate', openapi.IN_QUERY, description="Госномер", type=openapi.TYPE_STRING),
            openapi.Parameter(
                'category',
                openapi.IN_QUERY,
                description="Тип техники",
                type=openapi.TYPE_STRING,
                enum=list(REVERSE_VEHICLE_LABELS.keys()) if REVERSE_VEHICLE_LABELS else []
            ),
            openapi.Parameter(
                'status',
                openapi.IN_QUERY,
                description="Статус (active, idle, rented, repair)",
                type=openapi.TYPE_STRING,
                enum=['active', 'idle', 'rented', 'repair']
            ),
        ]
    )
    def list(self, request):
        vehicles = Vehicle.objects.all().order_by('veh_id').select_related('driver').prefetch_related(
            get_current_assignment_prefetch(),
        )

        search = request.query_params.get("search")
        if search:
            vehicles = vehicles.filter(
                Q(plate_number__icontains=search) |
                Q(name__icontains=search) |
                Q(vin__icontains=search) |
                Q(imei__icontains=search)
            )

        plate = request.query_params.get("plate")
        if plate:
            vehicles = vehicles.filter(plate_number__icontains=plate)

        category = request.query_params.get("category")
        if category:
            english_key = REVERSE_VEHICLE_LABELS.get(category, category)
            normalized_type = normalize_vehicle_type(english_key)
            if normalized_type:
                vehicles = vehicles.filter(type__iexact=normalized_type)

        status_ = request.query_params.get("status")
        if status_:
            if status_ == "active":
                vehicles = vehicles.filter(status=Vehicle.Status.ACTIVE)
            elif status_ == "idle":
                vehicles = vehicles.filter(status=Vehicle.Status.IDLE)
            elif status_ == "rented":
                vehicles = filter_rented_vehicles(vehicles)
            elif status_ == "repair":
                vehicles = vehicles.filter(status=Vehicle.Status.REPAIR)

        serializer = VehicleCardSerializer(vehicles, many=True, context={"request": request})

        return Response({"vehicles": serializer.data})

    @action(detail=False, methods=["get"])
    @swagger_auto_schema(tags=["🚚 Vehicles"], operation_summary="Фильтры для техники")
    def filters(self, request):
        raw_types = Vehicle.objects.exclude(type__isnull=True).exclude(type="").values_list("type",
                                                                                            flat=True).distinct().order_by(
            "type")
        normalized_types = {}
        for raw_type in raw_types:
            key = normalize_vehicle_type(raw_type)
            if not key or key in normalized_types:
                continue
            normalized_types[key] = {"key": key, "label": get_vehicle_type_label(key)}

        types = list(normalized_types.values())
        statuses = [
            {"key": "active", "label": "Активные"},
            {"key": "idle", "label": "В простое"},
            {"key": "rented", "label": "В аренде"},
            {"key": "repair", "label": "На ремонте"},
        ]
        return Response({"types": types, "statuses": statuses})

    @swagger_auto_schema(tags=["Пока что лишние"])
    @action(detail=True, methods=["get"])
    def status(self, request, pk=None):
        q = VehicleStatusHistory.objects.filter(vehicle__veh_id=pk).order_by("-ts")[:500]
        return Response(VehicleStatusHistorySerializer(q, many=True).data)

    @swagger_auto_schema(tags=["Пока что лишние"])
    @action(detail=True, methods=["get"])
    def fuel(self, request, pk=None):
        q = FuelLevelHistory.objects.filter(vehicle__veh_id=pk).order_by("-ts")[:500]
        return Response(FuelLevelHistorySerializer(q, many=True).data)

    @swagger_auto_schema(tags=["Пока что лишние"])
    @action(detail=True, methods=["get"])
    def events(self, request, pk=None):
        q = Event.objects.filter(vehicle__veh_id=pk).order_by("-ts")[:200]
        return Response(EventSerializer(q, many=True).data)

    @swagger_auto_schema(tags=["Пока что лишние"])
    @action(detail=True, methods=["get"])
    def track(self, request, pk=None):
        q = TrackPoint.objects.filter(vehicle__veh_id=pk).order_by("ts")
        return Response(TrackPointSerializer(q, many=True).data)

    @swagger_auto_schema(tags=["🚚 Vehicles"])
    @action(detail=False, methods=["get"], url_path="plates")
    def plates(self, request):
        search = request.query_params.get("search")
        qs = Vehicle.objects.exclude(plate_number__isnull=True).exclude(plate_number="")
        if search:
            qs = qs.filter(plate_number__icontains=search)
        plates = qs.values_list("plate_number", flat=True).distinct().order_by("plate_number")[:50]
        return Response({"plates": list(plates)})



def make_vehicle_entry(v: Vehicle):
    """Преобразует объект Vehicle в формат для фронтенда"""
    return FrontendVehicleSerializer.from_vehicle_obj(
        v,
        capacity=VEHICLE_FUEL_CAPACITY_DEFAULT
    )


class DashboardStatsView(APIView):
    """
    Главный дашборд.
    """

    @swagger_auto_schema(tags=["📊 Dashboard"], operation_summary="Главная статистика")
    def get(self, request):
        vehicles = Vehicle.objects.all().order_by("veh_id").prefetch_related(get_current_assignment_prefetch())
        total_vehicles_count = vehicles.count()

        active_qs = vehicles.filter(status=Vehicle.Status.ACTIVE)
        active_data = [make_vehicle_entry(v) for v in active_qs]

        idle_qs = vehicles.filter(status=Vehicle.Status.IDLE)
        idle_data = [make_vehicle_entry(v) for v in idle_qs]

        low_fuel_qs = vehicles.filter(status=Vehicle.Status.LOW_FUEL)
        low_fuel_data = [make_vehicle_entry(v) for v in low_fuel_qs]

        repair_qs = vehicles.filter(status=Vehicle.Status.REPAIR)
        repair_data = [make_vehicle_entry(v) for v in repair_qs]

        rented_qs = filter_rented_vehicles(vehicles)
        rented_data = [make_vehicle_entry(v) for v in rented_qs]

        return Response({
            "stats": {
                "vehicles_length": total_vehicles_count,
                "count_active": len(active_data),
                "count_idle": len(idle_data),
                "count_low_fuel": len(low_fuel_data),
                "count_rented": len(rented_data),
                "count_repair": len(repair_data),
            },
            "active": active_data,
            "idle": idle_data,
            "low_fuel": low_fuel_data,
            "rented": rented_data,
            "repair": repair_data,
        })


class AvailableVehiclesView(APIView):
    @swagger_auto_schema(tags=["🚚 Vehicles"], operation_summary="Список свободной техники (для бота)")
    def get(self, request):
        from vehicles.models import VehicleRequest
        from .models import VehicleAssignment

        assigned_ids = VehicleAssignment.objects.filter(unassigned_at__isnull=True).values_list("vehicle__veh_id",
                                                                                                flat=True)
        pending_ids = VehicleRequest.objects.filter(status="pending").values_list("vehicle_id", flat=True)

        excluded_ids = set([str(x) for x in assigned_ids] + [str(x) for x in pending_ids])
        excluded_ids_int = [int(x) for x in excluded_ids if x.isdigit()]

        vehicles = Vehicle.objects.exclude(veh_id__in=excluded_ids_int).order_by("veh_id")

        type_ = normalize_vehicle_type(request.query_params.get("type"))

        result = []
        for v in vehicles:
            resolved_type = resolve_vehicle_type_for_ui(v.type, v.name, v.veh_id)
            if type_ and resolved_type != type_:
                continue

            frontend = FrontendVehicleSerializer.from_vehicle_obj(v)
            display_name = (
                frontend.get("number")
                or frontend.get("name")
                or v.plate_number
                or v.name
                or f"ID:{v.veh_id}"
            )
            result.append({
                "id": v.veh_id,
                "name": frontend.get("name") or v.name,
                "type": resolved_type,
                "type_label": get_vehicle_type_label(resolved_type),
                "plate_number": frontend.get("number") or v.plate_number,
                "display_name": display_name,
            })
        return Response({"vehicles": result})


class VehicleDetailView(APIView):
    @swagger_auto_schema(tags=["🚚 Vehicles"], operation_summary="Детальная инфо о технике")
    def get(self, request, vehicle_id):
        vehicle = get_object_or_404(Vehicle, veh_id=vehicle_id)
        serializer = VehicleDetailSerializer(vehicle, context={"request": request})
        return Response(serializer.data)


class VehicleRepairsView(APIView):
    @swagger_auto_schema(tags=["🛠 Repairs"], operation_summary="История ремонтов машины")
    def get(self, request, vehicle_id):
        try:
            v = Vehicle.objects.get(veh_id=vehicle_id)
        except Vehicle.DoesNotExist:
            return Response({"error": "vehicle_not_found"}, 404)
        q = Repair.objects.filter(vehicle=v).order_by("-date")
        return Response(RepairSerializer(q, many=True).data)

    @swagger_auto_schema(tags=["🛠 Repairs"], operation_summary="Создать заявку на ремонт")
    def post(self, request, vehicle_id):
        try:
            v = Vehicle.objects.get(veh_id=vehicle_id)
        except Vehicle.DoesNotExist:
            return Response({"error": "vehicle_not_found"}, 404)
        data = request.data.copy()
        data["vehicle"] = v.id
        ser = RepairSerializer(data=data)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        ser.save()
        return Response(ser.data, status=status.HTTP_201_CREATED)



class GlobalMapView(APIView):
    """
    GET /api/map/data/
    Возвращает список зон, и ВНУТРИ каждой зоны — список машин, которые там находятся.
    Машины вне зон попадают в отдельный список "others".
    """

    @swagger_auto_schema(
        tags=["🗺 Map"],
        operation_summary="Данные для карты (Сгруппированные)",
        operation_description="Возвращает зоны с вложенными в них машинами. Машины, которые не попали ни в одну зону, лежат в 'vehicles_without_zone'."
    )
    def get(self, request):
        zones = FuelZone.objects.all()
        zones_data = FuelZoneSerializer(zones, many=True).data

        for z in zones_data:
            z["vehicles"] = []

        vehicles = (
            Vehicle.objects
            .exclude(lat__isnull=True)
            .exclude(lon__isnull=True)
            .prefetch_related("vehicleassignment_set__driver")
        )
        vehicles_data = MapVehicleSerializer(vehicles, many=True).data

        vehicles_without_zone = []

        for v in vehicles_data:
            assigned = False

            v_lat = v.get("lat")
            v_lon = v.get("lon")

            for z in zones_data:
                is_inside = False

                if z.get("zone_type") == 2:
                    is_inside = is_point_in_circle(
                        v_lat, v_lon,
                        z.get("center_lat"), z.get("center_lon"),
                        z.get("radius_m")
                    )

                elif z.get("zone_type") == 1:
                    is_inside = is_point_in_polygon_normalized(
                        v_lat, v_lon,
                        z.get("polygon_coords")
                    )

                if is_inside:
                    z["vehicles"].append(v)
                    assigned = True
                    break

            if not assigned:
                vehicles_without_zone.append(v)

        return Response({
            "zones": zones_data,
            "vehicles_without_zone": vehicles_without_zone,
            "total_vehicles": len(vehicles_data)
        })
