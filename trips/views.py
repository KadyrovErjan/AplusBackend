import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.contrib.auth import get_user_model
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

from vehicles.models import DriverShift
from vehicles.models import VehicleAssignment
from trips.tasks import sync_single_vehicle_task

logger = logging.getLogger(__name__)
User = get_user_model()
from vehicles.serializers import FrontendVehicleSerializer
from trips.services import (
    get_trips_statistics,
    get_filtered_trips_data,
    format_date,
    VEHICLE_TYPE_LABELS,
    _find_vehicle_for_shift,
    get_trips_with_fallback,
    build_timeline_from_trips
)


def _normalize_note(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:1000]


class TripsStatisticsView(APIView):
    @swagger_auto_schema(tags=["🛣 Trips"])
    def get(self, request):
        data = get_trips_statistics()
        return Response(data)


TRIP_FILTER_PARAMS = [
    openapi.Parameter("date_from", openapi.IN_QUERY, type=openapi.TYPE_STRING, format="date", description="С даты (DD-MM-YYYY)"),
    openapi.Parameter("date_to", openapi.IN_QUERY, type=openapi.TYPE_STRING, format="date", description="По дату (DD-MM-YYYY)"),
    openapi.Parameter("status", openapi.IN_QUERY, type=openapi.TYPE_STRING, enum=["active", "completed"]),
    openapi.Parameter("plate_number", openapi.IN_QUERY, type=openapi.TYPE_STRING),
    openapi.Parameter("vehicle_type", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                      enum=list(VEHICLE_TYPE_LABELS.values())),
    openapi.Parameter("driver_name", openapi.IN_QUERY, type=openapi.TYPE_STRING),
    openapi.Parameter("route_from", openapi.IN_QUERY, type=openapi.TYPE_STRING),
    openapi.Parameter("route_to", openapi.IN_QUERY, type=openapi.TYPE_STRING),
    openapi.Parameter("zone_from", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description="ID первой зоны (для сверки)"),
    openapi.Parameter("zone_to", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description="ID второй зоны (для сверки)"),
]


class TripsHistoryView(APIView):
    """
    GET  /api/trips/history/ — список смен с результатами сверки (GPS vs Водитель).
    POST /api/trips/history/ — водитель вводит количество рейсов за сегодня.
    """

    @swagger_auto_schema(
        tags=["🛣 Trips"],
        operation_summary="История рейсов (Фильтр)",
        manual_parameters=TRIP_FILTER_PARAMS
    )
    def get(self, request):
        data = get_filtered_trips_data(request.query_params)
        return Response({"trips": data})

    @swagger_auto_schema(
        tags=["🛣 Trips"],
        operation_summary="Ввод количества рейсов за день",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["telegram_id", "trips_count"],
            properties={
                "telegram_id": openapi.Schema(type=openapi.TYPE_INTEGER),
                "trips_count": openapi.Schema(type=openapi.TYPE_INTEGER),
                "note": openapi.Schema(type=openapi.TYPE_STRING, description="Короткое примечание к рейсам"),
            },
        ),
    )
    def post(self, request):
        """
        Водитель один раз в день вводит количество рейсов через Telegram-бот.
        Данные сохраняются в DriverShift.trips_count последней смены за сегодня.
        Защита от дубликатов: если у любой смены сегодня уже есть trips_count — отказ.
        """
        telegram_id = request.data.get("telegram_id")
        trips_count = request.data.get("trips_count")
        note = _normalize_note(request.data.get("note"))

        if telegram_id is None:
            return Response({"error": "telegram_id_required"}, status=400)
        try:
            telegram_id = int(telegram_id)
        except (TypeError, ValueError):
            return Response({"error": "telegram_id_invalid"}, status=400)

        if trips_count is None:
            return Response({"error": "trips_count_required"}, status=400)
        try:
            trips_count = int(trips_count)
        except (TypeError, ValueError):
            return Response({"error": "trips_count_invalid"}, status=400)
        if trips_count < 0:
            return Response({"error": "trips_count_invalid"}, status=400)

        user = User.objects.filter(telegram_id=telegram_id).first()
        if not user:
            return Response({"error": "user_not_found"}, status=404)
        if user.role != "driver":
            return Response({"error": "user_not_driver"}, status=403)
        if user.status != "active":
            return Response({"error": "user_not_active"}, status=403)

        today = timezone.localdate()

        today_shifts = DriverShift.objects.filter(
            driver=user,
            started_at__date=today,
        ).order_by("-started_at")

        if today_shifts.filter(trips_count__isnull=False).exists():
            return Response({"error": "already_submitted_today"}, status=400)

        latest_shift = today_shifts.first()
        if not latest_shift:
            return Response({"error": "no_shift_today"}, status=400)

        latest_shift.trips_count = trips_count
        latest_shift.trips_note = note
        latest_shift.save(update_fields=["trips_count", "trips_note"])

        if latest_shift.vehicle and latest_shift.vehicle.veh_id:
            sync_single_vehicle_task.delay(latest_shift.vehicle.veh_id)

        return Response({
            "status": "ok",
            "date": today.strftime("%d-%m-%Y"),
            "claimed_trips": trips_count,
            "note": latest_shift.trips_note,
            "shift_id": latest_shift.id,
        }, status=201)


class TripDetailView(APIView):
    """
    GET  /api/trips/history/<pk>/ — детальная информация о смене с таймлайном GPS.
    POST /api/trips/history/<pk>/ — обновить trips_count конкретной смены.
    """

    @swagger_auto_schema(tags=["🛣 Trips"], operation_summary="Детальная информация о рейсе")
    def get(self, request, pk):
        shift = get_object_or_404(DriverShift, pk=pk)
        driver_name = f"{shift.driver.first_name} {shift.driver.surname}".strip()

        current_vehicle = _find_vehicle_for_shift(shift)

        vehicle_data = None
        if current_vehicle:
            frontend_veh = FrontendVehicleSerializer.from_vehicle_obj(current_vehicle)
            label = VEHICLE_TYPE_LABELS.get(current_vehicle.type, current_vehicle.type)
            vehicle_data = {
                "id": current_vehicle.veh_id,
                "name": frontend_veh.get("name"),
                "plate_number": frontend_veh.get("number"),
                "type": current_vehicle.type,
                "type_label": label
            }

        timeline = []
        verified_trips_count = 0
        verified_distance = float(shift.distance_km or 0.0)
        verified_engine_hours = round(float(shift.engine_hours or 0.0), 1)

        if current_vehicle:
            search_end = shift.ended_at or timezone.now()
            trips, source = get_trips_with_fallback(
                vehicle=current_vehicle,
                start_dt=shift.started_at,
                end_dt=search_end,
                persist=True
            )

            verified_trips_count = len(trips)
            timeline = build_timeline_from_trips(trips)

        if shift.status == "active" and current_vehicle:
            timeline.append({
                "zone_name": "Текущий статус (GPS)",
                "enter_time": "Сейчас",
                "exit_time": None,
                "date": timezone.localtime().strftime("%d-%m-%Y"),
                "duration": "...",
                "distance": "-",
                "coordinates": {"lat": current_vehicle.lat, "lon": current_vehicle.lon},
                "status": "current"
            })

        return Response({
            "id": shift.id,
            "driver": driver_name,
            "vehicle": vehicle_data,
            "work_time": {
                "start": format_date(shift.started_at),
                "end": format_date(shift.ended_at),
                "movement_start": format_date(shift.movement_started_at or shift.started_at),
            },
            "status": shift.status,
            "stats": {
                "claimed_trips": shift.trips_count or 0,
                "trips_note": shift.trips_note,
                "verified_trips": verified_trips_count,
                "verified_distance_km": round(verified_distance, 1),
                "engine_hours": verified_engine_hours,
                "late_minutes": shift.late_minutes_effective,
                "late_label": shift.late_label,
                "worked_norm_minutes": shift.worked_norm_minutes_effective,
                "worked_norm_label": shift.worked_norm_label,
            },
            "timeline": timeline
        })

    @swagger_auto_schema(
        tags=["🛣 Trips"],
        operation_summary="Обновить количество рейсов смены",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["trips_count"],
            properties={
                "trips_count": openapi.Schema(type=openapi.TYPE_INTEGER),
                "note": openapi.Schema(type=openapi.TYPE_STRING, description="Короткое примечание к рейсам"),
            },
        ),
    )
    def post(self, request, pk):
        """
        Обновить trips_count конкретной смены (для админа или ручной правки).
        """
        shift = get_object_or_404(DriverShift, pk=pk)
        trips_count = request.data.get("trips_count")

        if trips_count is None:
            return Response({"error": "trips_count_required"}, status=400)
        try:
            trips_count = int(trips_count)
        except (TypeError, ValueError):
            return Response({"error": "trips_count_invalid"}, status=400)
        if trips_count < 0:
            return Response({"error": "trips_count_invalid"}, status=400)

        shift.trips_count = trips_count
        update_fields = ["trips_count"]
        if "note" in request.data:
            shift.trips_note = _normalize_note(request.data.get("note"))
            update_fields.append("trips_note")
        shift.save(update_fields=update_fields)

        return Response({
            "status": "ok",
            "shift_id": shift.id,
            "claimed_trips": trips_count,
            "note": shift.trips_note,
        })
