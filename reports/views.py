import logging
from collections import defaultdict
from datetime import datetime

from django.db.models import Sum, Count, Prefetch
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from maintenance.models import IssueReport
from vehicles.models import Vehicle, Repair, DriverShift, VehicleAssignment
from vehicles.serializers import FrontendVehicleSerializer, VehicleCardSerializer
from fuels.models import FuelEvent, FuelReport
from fuels.services.zone_filters import apply_zone_filter, parse_zone_filter_params, NO_ZONE_ID
from trips.services import get_filtered_trips_data, VEHICLE_TYPE_LABELS
from .excel_builder import ExcelBuilder, parse_date_param

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Swagger params
# ---------------------------------------------------------------------------
DATE_PARAMS = [
    openapi.Parameter("date_from", openapi.IN_QUERY, type=openapi.TYPE_STRING, format="date", description="С даты (DD-MM-YYYY)"),
    openapi.Parameter("date_to", openapi.IN_QUERY, type=openapi.TYPE_STRING, format="date", description="По дату (DD-MM-YYYY)"),
]

TRIP_FILTER_PARAMS = DATE_PARAMS + [
    openapi.Parameter("status", openapi.IN_QUERY, type=openapi.TYPE_STRING, enum=["active", "completed"]),
    openapi.Parameter("plate_number", openapi.IN_QUERY, type=openapi.TYPE_STRING),
    openapi.Parameter("vehicle_type", openapi.IN_QUERY, type=openapi.TYPE_STRING, enum=list(VEHICLE_TYPE_LABELS.values())),
    openapi.Parameter("driver_name", openapi.IN_QUERY, type=openapi.TYPE_STRING),
    openapi.Parameter("zone_from", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description="ID первой зоны Incomtek (external_id)"),
    openapi.Parameter("zone_to", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description="ID второй зоны Incomtek (external_id)"),
    openapi.Parameter("route_from", openapi.IN_QUERY, type=openapi.TYPE_STRING, description="Legacy: название первой зоны (если zone_from не передан)"),
    openapi.Parameter("route_to", openapi.IN_QUERY, type=openapi.TYPE_STRING, description="Legacy: название второй зоны (если zone_to не передан)"),
    openapi.Parameter("group_by", openapi.IN_QUERY, type=openapi.TYPE_STRING, enum=["none", "day", "week", "month"], description="Агрегация"),
]

FUEL_FILTER_PARAMS = DATE_PARAMS + [
    openapi.Parameter("zone_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description=f"ID объекта. {NO_ZONE_ID} = Вне объекта"),
    openapi.Parameter(
        "zone_ids",
        openapi.IN_QUERY,
        type=openapi.TYPE_STRING,
        description=f"Список ID объектов (CSV или repeated). Пример: zone_ids=1,2 или zone_ids=1&zone_ids=2. {NO_ZONE_ID} = Вне объекта",
    ),
    openapi.Parameter("zone_name", openapi.IN_QUERY, type=openapi.TYPE_STRING, description="Legacy фильтр по названию объекта"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_date_range(request):
    d_from = parse_date_param(request.query_params.get("date_from"))
    d_to = parse_date_param(request.query_params.get("date_to"))
    today = timezone.now().date()
    return d_from or today, d_to or today


def _build_report_filename(report_type, date_from, date_to):
    return (
        f"отчет_{report_type}_от_{date_from.strftime('%d-%m-%Y')}"
        f"_до_{date_to.strftime('%d-%m-%Y')}.xlsx"
    )


def _format_minutes(minutes):
    if minutes is None:
        return "-"
    total = abs(int(minutes))
    h = total // 60
    m = total % 60
    sign = "+" if minutes > 0 else "-" if minutes < 0 else ""
    return f"{sign}{h}ч {m}м".strip()


def _driver_name(obj):
    def _user_name(user):
        if not user:
            return "-"
        first_name = str(getattr(user, "first_name", "") or "").strip()
        surname = str(getattr(user, "surname", getattr(user, "last_name", "")) or "").strip()
        full_name = f"{first_name} {surname}".strip()
        if full_name:
            return full_name
        username = str(getattr(user, "username", "") or "").strip()
        if username:
            return username
        email = str(getattr(user, "email", "") or "").strip()
        return email or "-"

    def _vehicle_driver_name(vehicle):
        if not vehicle:
            return "-"

        vehicle_driver_name = _user_name(getattr(vehicle, "driver", None))
        if vehicle_driver_name != "-":
            return vehicle_driver_name

        if hasattr(vehicle, "report_active_assignments"):
            assignments = vehicle.report_active_assignments
        elif hasattr(vehicle, "current_assignment"):
            assignments = vehicle.current_assignment
        else:
            assignments = list(
                VehicleAssignment.objects
                .filter(vehicle=vehicle, unassigned_at__isnull=True)
                .select_related("driver")
                .order_by("-assigned_at", "-id")[:1]
            )

        if assignments:
            assignment_driver_name = _user_name(getattr(assignments[0], "driver", None))
            if assignment_driver_name != "-":
                return assignment_driver_name

        frontend_driver = FrontendVehicleSerializer.from_vehicle_obj(obj if isinstance(obj, Vehicle) else vehicle).get("driver")
        return frontend_driver or "-"

    if isinstance(obj, Vehicle):
        return _vehicle_driver_name(obj)

    driver = getattr(obj, "driver", None)
    driver_name = _user_name(driver)
    if driver_name != "-":
        return driver_name

    related_vehicle = getattr(obj, "vehicle", None)
    if related_vehicle:
        return _vehicle_driver_name(related_vehicle)

    if getattr(obj, "fuel_event_id", None) and getattr(obj, "fuel_event", None):
        return _vehicle_driver_name(getattr(obj.fuel_event, "vehicle", None))

    return "-"


def _vehicle_name_and_plate(vehicle):
    if not vehicle:
        return "-", "-"
    name, plate = FrontendVehicleSerializer.split_name_and_plate(vehicle)
    return (name or "-"), (plate or "-")


def _vehicle_total_km(vehicle, mileage_serializer=None):
    if not vehicle:
        return 0

    serializer = mileage_serializer or VehicleCardSerializer()
    mileage_data = serializer.get_mileage(vehicle) or {}
    return mileage_data.get("total_km") or 0


def _fuel_object_label(zone_obj=None, zone_name=None, address=None, vehicle=None):
    if zone_obj and getattr(zone_obj, "name", None):
        return zone_obj.name
    if isinstance(zone_name, (list, tuple, set)):
        normalized_zone = ""
    else:
        normalized_zone = str(zone_name or "").strip()
    if normalized_zone in {"[]", "[ ]"}:
        normalized_zone = ""
    if normalized_zone and normalized_zone.lower() != "вне объекта":
        return normalized_zone
    normalized_address = str(address or "").strip()
    if normalized_address:
        return normalized_address
    vehicle_address = str(getattr(vehicle, "address", "") or "").strip()
    if vehicle_address:
        return vehicle_address
    return "Вне объекта"


def _fuel_row_key(vehicle, plate, vehicle_name):
    normalized_plate = str(plate or "").strip()
    if normalized_plate and normalized_plate != "-":
        return normalized_plate
    if vehicle and getattr(vehicle, "veh_id", None):
        return f"veh:{vehicle.veh_id}"
    return vehicle_name or "unknown"


def _should_replace_fuel_object(current_value, new_value, vehicle=None):
    normalized_new = str(new_value or "").strip()
    if not normalized_new:
        return False

    normalized_current = str(current_value or "").strip()
    if normalized_current in {"", "-", "[]", "Р’РЅРµ РѕР±СЉРµРєС‚Р°"}:
        return True

    vehicle_address = str(getattr(vehicle, "address", "") or "").strip()
    return bool(vehicle_address and normalized_current == vehicle_address and normalized_new != vehicle_address)


def _normalize_excel_datetime(value):
    if value in (None, ""):
        return None

    if isinstance(value, (int, float)):
        dt_value = datetime.fromtimestamp(value, tz=timezone.get_current_timezone())
    elif isinstance(value, datetime):
        dt_value = value
    elif isinstance(value, str):
        text_value = value.strip()
        if not text_value:
            return None
        try:
            dt_value = datetime.fromisoformat(text_value)
        except ValueError:
            for fmt in ("%d.%m.%Y %H:%M", "%d-%m-%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
                try:
                    dt_value = datetime.strptime(text_value, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
    else:
        return None

    if timezone.is_aware(dt_value):
        dt_value = timezone.localtime(dt_value)

    return dt_value.replace(tzinfo=None)


# ===========================================================================
# 1. VEHICLES
# ===========================================================================
class VehicleExcelExportView(APIView):
    @swagger_auto_schema(
        tags=["Reports"],
        operation_summary="Скачать Excel список техники",
        manual_parameters=DATE_PARAMS + [
            openapi.Parameter("type", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("status", openapi.IN_QUERY, type=openapi.TYPE_STRING),
        ],
    )
    def get(self, request):
        d_from, d_to = _get_date_range(request)

        queryset = (
            Vehicle.objects.all()
            .select_related("driver")
            .prefetch_related(
                Prefetch(
                    "vehicleassignment_set",
                    queryset=VehicleAssignment.objects.filter(unassigned_at__isnull=True).select_related("driver"),
                    to_attr="current_assignment",
                )
            )
            .order_by("veh_id")
        )
        v_type = request.query_params.get("type")
        status = request.query_params.get("status")
        if v_type:
            queryset = queryset.filter(type=v_type)
        if status:
            queryset = queryset.filter(status=status)

        builder = ExcelBuilder("Парк техники", "4F81BD", request)
        mileage_serializer = VehicleCardSerializer(context={"request": request})
        headers = [
            "№", "Техника", "Госномер", "Водитель",
            "Пробег за период (км)", "Моточасы (всего)", "Последнее ТО",
        ]
        builder.write_headers(headers)

        center = {1, 3, 5, 6, 7}
        num_fmt = {5: "#,##0", 6: "#,##0.00", 7: "#,##0.00"}

        for row_index, v in enumerate(queryset, start=1):
            vehicle_name, plate = _vehicle_name_and_plate(v)
            builder.write_row([
                row_index,
                vehicle_name,
                plate,
                _driver_name(v),
                _vehicle_total_km(v, mileage_serializer),
                v.total_engine_hours,
                v.last_service_hours,
            ], number_formats=num_fmt, center_columns=center)

        builder.add_totals_row([5, 6], label_col=2, label="ИТОГО")
        builder.auto_fit_columns()
        builder.freeze_header()
        builder.add_auto_filter()

        filename = _build_report_filename("о_технике", d_from, d_to)
        return builder.to_response(filename)


# ===========================================================================
# 2. ANNUAL
# ===========================================================================
class AnnualAnalyticsExportView(APIView):
    @swagger_auto_schema(
        tags=["Reports"],
        operation_summary="Скачать Сводный Годовой Отчет",
        manual_parameters=[
            openapi.Parameter("year", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description="Год отчета"),
        ],
    )
    def get(self, request):
        current_year = timezone.now().year
        year_param = request.query_params.get("year")
        try:
            target_year = int(year_param) if year_param else current_year
        except ValueError:
            target_year = current_year

        start_date = datetime(target_year, 1, 1).date()
        end_date = datetime(target_year, 12, 31).date()
        start_dt = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
        end_dt = timezone.make_aware(datetime.combine(end_date, datetime.max.time()))

        vehicles = Vehicle.objects.all().order_by("type", "name")

        # Batch aggregations (4 queries instead of 4*N)
        distance_map = dict(
            DriverShift.objects
            .filter(started_at__date__gte=start_date, started_at__date__lte=end_date)
            .values_list("vehicle_id")
            .annotate(total=Sum("distance_km"))
        )

        fuel_map = dict(
            FuelEvent.objects
            .filter(event_type=1, created_at__range=(start_dt, end_dt))
            .values_list("vehicle_id")
            .annotate(total=Sum("liters_from_ink"))
        )

        repair_agg = {
            r["vehicle_id"]: r
            for r in Repair.objects
            .filter(date__range=(start_date, end_date))
            .values("vehicle_id")
            .annotate(count=Count("id"), total_parts=Sum("cost_parts"), total_work=Sum("cost_work"))
        }

        issue_map = dict(
            IssueReport.objects
            .filter(created_at__range=(start_dt, end_dt))
            .values_list("vehicle_id")
            .annotate(count=Count("id"))
        )

        builder = ExcelBuilder(f"Сводный отчет {target_year}", "7030A0", request)
        headers = [
            "№", "Техника", "Госномер", "Тип",
            "Пробег (км)", "Заправлено (л)", "Ср. расход (л/100км)",
            "Кол-во ремонтов", "Стоимость ремонтов (сом)", "Заявок от водителей",
        ]
        builder.write_headers(headers)

        center = set(range(1, 11))
        num_fmt = {5: "#,##0.00", 6: "#,##0.00", 7: "#,##0.00", 9: "#,##0.00"}

        for idx, v in enumerate(vehicles, 1):
            total_km = float(distance_map.get(v.id) or 0)
            total_fuel = float(fuel_map.get(v.id) or 0)
            r_data = repair_agg.get(v.id, {})
            repair_count = r_data.get("count", 0)
            repair_cost = float(r_data.get("total_parts") or 0) + float(r_data.get("total_work") or 0)
            issue_count = issue_map.get(v.id, 0)
            avg_consumption = (total_fuel / total_km) * 100 if total_km > 0 else 0

            builder.write_row([
                idx,
                v.name or "-",
                v.plate_number or "-",
                v.type or "-",
                round(total_km, 2),
                round(total_fuel, 2),
                round(avg_consumption, 2),
                repair_count,
                round(repair_cost, 2),
                issue_count,
            ], number_formats=num_fmt, center_columns=center)

        builder.add_totals_row([5, 6, 8, 9, 10], label_col=2, label="ИТОГО ПО ПАРКУ")
        builder.auto_fit_columns()
        builder.freeze_header()
        builder.add_auto_filter()

        return builder.to_response(f"отчет_по_годовой_аналитике_за_{target_year}.xlsx")


# ===========================================================================
# 3. FUELS
# ===========================================================================
class FuelExcelExportView(APIView):
    @swagger_auto_schema(
        tags=["Reports"],
        operation_summary="Скачать Excel отчет по заправкам",
        manual_parameters=FUEL_FILTER_PARAMS,
    )
    def get(self, request):
        d_from, d_to = _get_date_range(request)

        # FuelEvent (sensor) + FuelConfirmation
        fuel_events = (
            FuelEvent.objects
            .filter(event_type=1, created_at__date__gte=d_from, created_at__date__lte=d_to)
            .select_related("vehicle", "vehicle__driver", "zone", "confirmation", "confirmation__driver")
            .prefetch_related(
                Prefetch(
                    "vehicle__vehicleassignment_set",
                    queryset=VehicleAssignment.objects.filter(unassigned_at__isnull=True).select_related("driver").order_by("-assigned_at", "-id"),
                    to_attr="report_active_assignments",
                ),
                "confirmation__photos",
            )
            .order_by("created_at")
        )

        # FuelReport (manual driver reports)
        fuel_reports = (
            FuelReport.objects
            .filter(created_at__date__gte=d_from, created_at__date__lte=d_to)
            .select_related("vehicle", "vehicle__driver", "driver")
            .prefetch_related(
                Prefetch(
                    "vehicle__vehicleassignment_set",
                    queryset=VehicleAssignment.objects.filter(unassigned_at__isnull=True).select_related("driver").order_by("-assigned_at", "-id"),
                    to_attr="report_active_assignments",
                ),
                "photos",
            )
            .order_by("created_at")
        )

        zone_filter, zone_error = parse_zone_filter_params(request.query_params)
        if zone_error:
            return Response({"error": zone_error}, status=400)

        fuel_events, fuel_reports = apply_zone_filter(fuel_events, fuel_reports, zone_filter)

        # Keep one row per fueling, not one row per vehicle for the whole range.
        merge_window_seconds = 24 * 60 * 60
        rows = []

        def build_row(vehicle, item_date=None):
            vehicle_name, plate = _vehicle_name_and_plate(vehicle)
            return {
                "match_key": _fuel_row_key(vehicle, plate, vehicle_name),
                "date": item_date,
                "vehicle_name": vehicle_name,
                "plate": plate,
                "driver": "-",
                "object_name": "-",
                "fuel_type": "дт",
                "pn_shnos": None,
                "liters_ink": 0.0,
                "liters_driver": 0.0,
                "photo_urls": [],
            }

        def find_matching_row(vehicle, item_date):
            if not vehicle or not item_date:
                return None

            vehicle_name, plate = _vehicle_name_and_plate(vehicle)
            row_key = _fuel_row_key(vehicle, plate, vehicle_name)
            matched_rows = []
            for row in rows:
                if row["match_key"] != row_key or row["date"] is None:
                    continue
                delta_seconds = abs((row["date"] - item_date).total_seconds())
                if delta_seconds <= merge_window_seconds:
                    matched_rows.append((delta_seconds, row))

            if not matched_rows:
                return None

            matched_rows.sort(key=lambda item: item[0])
            return matched_rows[0][1]

        def merge_row(row, vehicle, item_date, driver_name, object_name, liters_ink=0, liters_driver=0, photo_urls=None):
            if item_date and (row["date"] is None or item_date < row["date"]):
                row["date"] = item_date

            if driver_name and driver_name != "-" and row["driver"] == "-":
                row["driver"] = driver_name

            if _should_replace_fuel_object(row["object_name"], object_name, vehicle):
                row["object_name"] = object_name

            if liters_ink and not row["liters_ink"]:
                row["liters_ink"] = float(liters_ink)

            if liters_driver and not row["liters_driver"]:
                row["liters_driver"] = float(liters_driver)

            for url in photo_urls or []:
                if url and url not in row["photo_urls"]:
                    row["photo_urls"].append(url)

        for fe in fuel_events:
            conf = getattr(fe, "confirmation", None)
            photo_urls = []
            if conf:
                conf_photos = [p.photo.url for p in conf.photos.all() if p.photo]
                if conf_photos:
                    photo_urls = conf_photos
                elif conf.photo:
                    photo_urls = [conf.photo.url]
            row = build_row(fe.vehicle, fe.created_at)
            rows.append(row)
            merge_row(
                row=row,
                vehicle=fe.vehicle,
                item_date=fe.created_at,
                driver_name=_driver_name(conf or fe),
                object_name=_fuel_object_label(
                    zone_obj=fe.zone,
                    zone_name=fe.zone_name,
                    address=fe.address,
                    vehicle=fe.vehicle,
                ),
                liters_ink=fe.liters_from_ink or 0,
                liters_driver=conf.liters_from_driver if conf else 0,
                photo_urls=photo_urls,
            )

        for fr in fuel_reports:
            report_photos = [p.photo.url for p in fr.photos.all() if p.photo]
            if report_photos:
                photo_urls = report_photos
            elif fr.photo:
                photo_urls = [fr.photo.url]
            else:
                photo_urls = []
            row = find_matching_row(fr.vehicle, fr.created_at)
            if row is None:
                row = build_row(fr.vehicle, fr.created_at)
                rows.append(row)
            merge_row(
                row=row,
                vehicle=fr.vehicle,
                item_date=fr.created_at,
                driver_name=_driver_name(fr),
                object_name=_fuel_object_label(
                    zone_name=fr.zone_name,
                    address=fr.address,
                    vehicle=fr.vehicle,
                ),
                liters_ink=fr.liters_from_ink or 0,
                liters_driver=fr.liters_from_driver or 0,
                photo_urls=photo_urls,
            )

        rows.sort(key=lambda r: (r["date"] is None, r["date"] or timezone.now()))
        max_photos = max((len(r["photo_urls"]) for r in rows), default=0)
        if max_photos == 0:
            max_photos = 1

        builder = ExcelBuilder("Заправки", "70AD47", request)
        base_headers = [
            "Дата", "Техника", "Госномер", "Водитель", "Объект",
            "Вид заправки", "ПН-ШНОС",
            "Заправка Incomtek (л)", "Заправка водитель (л)",
        ]
        photo_headers = [f"Фото {i + 1}" for i in range(max_photos)]
        headers = base_headers + photo_headers
        builder.write_headers(headers)

        num_fmt = {1: "DD.MM.YYYY HH:MM", 8: "#,##0.00", 9: "#,##0.00"}
        center = {1, 3, 6, 8, 9}

        for r in rows:
            row_values = [
                _normalize_excel_datetime(r["date"]),
                r["vehicle_name"],
                r["plate"],
                r["driver"],
                r["object_name"],
                "дт",
                r["pn_shnos"],
                r["liters_ink"],
                r["liters_driver"],
            ] + [None] * max_photos

            row_num = builder.write_row(
                row_values,
                number_formats=num_fmt,
                center_columns=center,
            )

            photo_start_col = len(base_headers) + 1
            for i in range(max_photos):
                col = photo_start_col + i
                if i < len(r["photo_urls"]):
                    abs_url = builder.build_absolute_url(r["photo_urls"][i])
                    builder.write_hyperlink_cell(row_num, col, abs_url, f"Фото {i + 1}")
                else:
                    builder.write_hyperlink_cell(row_num, col, "-")

        builder.add_totals_row([8, 9], label_col=2, label="ИТОГО")
        builder.auto_fit_columns()
        builder.freeze_header()
        builder.add_auto_filter()

        filename = _build_report_filename("о_заправке", d_from, d_to)
        return builder.to_response(filename)


# ===========================================================================
# 4. REPAIRS (Issues)
# ===========================================================================
class RepairExcelExportView(APIView):
    @swagger_auto_schema(
        tags=["Reports"],
        operation_summary="Скачать Excel отчет по заявкам",
        manual_parameters=DATE_PARAMS + [
            openapi.Parameter("vehicle_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
    )
    def get(self, request):
        queryset = (
            IssueReport.objects.all()
            .select_related("vehicle", "driver")
            .prefetch_related("photos")
            .order_by("-created_at")
        )

        d_from = parse_date_param(request.query_params.get("date_from"))
        d_to = parse_date_param(request.query_params.get("date_to"))
        if d_from is None and d_to is None:
            today = timezone.localdate()
            d_from = today
            d_to = today

        filename_d_from = d_from or timezone.localdate()
        filename_d_to = d_to or timezone.localdate()
        vehicle_id = request.query_params.get("vehicle_id")

        if d_from:
            queryset = queryset.filter(created_at__date__gte=d_from)
        if d_to:
            queryset = queryset.filter(created_at__date__lte=d_to)
        if vehicle_id:
            queryset = queryset.filter(vehicle__id=vehicle_id)

        # Pre-scan for max photo count
        issues_with_photos = []
        max_photos = 0
        for issue in queryset:
            photos = list(issue.photos.all())
            max_photos = max(max_photos, len(photos))
            issues_with_photos.append((issue, photos))

        if max_photos == 0:
            max_photos = 1

        builder = ExcelBuilder("Заявки на ремонт", "ED7D31", request)

        base_headers = [
            "№", "Дата/Время", "Техника", "Госномер",
            "Водитель", "Описание проблемы", "Цена",
        ]
        photo_headers = [f"Фото {i + 1}" for i in range(max_photos)]
        builder.write_headers(base_headers + photo_headers)

        for row_index, (issue, photos) in enumerate(issues_with_photos, start=1):
            vehicle_name, plate = _vehicle_name_and_plate(issue.vehicle)
            row_values = [
                row_index,
                issue.created_at.strftime("%d-%m-%Y %H:%M"),
                vehicle_name,
                plate,
                _driver_name(issue),
                issue.description or "-",
                0,
            ]
            # Pad with None placeholders for photos
            row_values += [None] * max_photos

            row_num = builder.write_row(row_values, center_columns={1, 2, 4, 7})

            # Write photo hyperlinks
            photo_start_col = len(base_headers) + 1
            for i in range(max_photos):
                col = photo_start_col + i
                if i < len(photos) and photos[i].photo:
                    url = builder.build_absolute_url(photos[i].photo.url)
                    builder.write_hyperlink_cell(row_num, col, url, f"Фото {i + 1}")
                else:
                    builder.write_hyperlink_cell(row_num, col, "-")

        builder.auto_fit_columns()
        builder.freeze_header()
        builder.add_auto_filter()

        filename = _build_report_filename("о_ремонтах", filename_d_from, filename_d_to)
        return builder.to_response(filename)


# ===========================================================================
# 5. TRIPS
# ===========================================================================
def _aggregate_trips(trips_data, group_by):
    """
    Агрегирует список trips по периоду.
    group_by: 'day' | 'week' | 'month'
    Возвращает список словарей с агрегированными данными.
    """
    groups = defaultdict(lambda: {
        "vehicle": None,
        "driver": "",
        "started_at_dt": None,
        "period_label": "",
        "object_routes": set(),
        "notes": [],
        "notes_seen": set(),
        "claimed_sum": 0,
        "verified_sum": 0,
        "late_sum": 0,
        "worked_sum": 0,
        "count": 0,
    })

    for trip in trips_data:
        dt = trip.get("started_at_dt")
        if not dt:
            continue

        vehicle = trip.get("vehicle") or {}
        v_id = vehicle.get("id", "unknown")

        if group_by == "day":
            key = (v_id, dt.strftime("%Y-%m-%d"))
            label = dt.strftime("%d.%m.%Y")
        elif group_by == "week":
            iso = dt.isocalendar()
            key = (v_id, f"{iso[0]}-W{iso[1]:02d}")
            label = f"Неделя {iso[1]}, {iso[0]}"
        else:  # month
            key = (v_id, f"{dt.year}-{dt.month:02d}")
            label = f"{dt.strftime('%B')} {dt.year}"

        g = groups[key]
        stats = trip.get("stats") or {}

        if g["vehicle"] is None:
            g["vehicle"] = vehicle
            g["driver"] = trip.get("driver", "")
            g["started_at_dt"] = dt
            g["period_label"] = label

        g["claimed_sum"] += stats.get("claimed_trips", 0) or 0
        g["verified_sum"] += stats.get("verified_trips", 0) or 0
        g["late_sum"] += stats.get("late_minutes", 0) or 0
        g["worked_sum"] += stats.get("worked_norm_minutes", 0) or 0
        object_route = str(trip.get("object_route") or "").strip()
        if object_route:
            g["object_routes"].add(object_route)
        note_text = str(
            trip.get("note")
            or stats.get("trips_note")
            or ""
        ).strip()
        if note_text and note_text not in g["notes_seen"]:
            g["notes_seen"].add(note_text)
            g["notes"].append(note_text)
        g["count"] += 1

    result = []
    for key, g in sorted(groups.items(), key=lambda x: x[0]):
        cnt = g["count"] or 1
        routes = sorted(g["object_routes"])
        if not routes:
            object_route = "Выберите zone_from и zone_to для сверки"
        elif len(routes) == 1:
            object_route = routes[0]
        else:
            object_route = "Смешанные маршруты"
        notes_text = " | ".join(g["notes"])

        result.append({
            "period_label": g["period_label"],
            "vehicle": g["vehicle"],
            "driver": g["driver"],
            "object_route": object_route,
            "note": notes_text,
            "claimed_trips": g["claimed_sum"],
            "verified_trips": g["verified_sum"],
            "late_minutes": round(g["late_sum"] / cnt),
            "worked_norm_minutes": g["worked_sum"],
        })
    return result


class TripsExcelExportView(APIView):
    @swagger_auto_schema(
        tags=["Reports"],
        operation_summary="Скачать Excel отчет по рейсам",
        manual_parameters=TRIP_FILTER_PARAMS,
    )
    def get(self, request):
        trips = get_filtered_trips_data(request.query_params, include_object_route=True)
        group_by = request.query_params.get("group_by", "none")
        d_from, d_to = _get_date_range(request)
        trip_count = len(trips)
        matched_trip_count = sum(
            int((item.get("stats") or {}).get("verified_trips", 0) or 0)
            for item in trips
        )
        if trip_count == 0:
            logger.warning(
                "Trips report returned no rows after filters: rows=%s matched_trips=%s date_from=%s date_to=%s zone_from=%s zone_to=%s",
                trip_count,
                matched_trip_count,
                request.query_params.get("date_from"),
                request.query_params.get("date_to"),
                request.query_params.get("zone_from"),
                request.query_params.get("zone_to"),
            )
        else:
            logger.info(
                "Trips report rows before Excel export: rows=%s matched_trips=%s",
                trip_count,
                matched_trip_count,
            )
        trips = sorted(
            trips,
            key=lambda item: (
                item.get("started_at_dt") is None,
                item.get("started_at_dt") or timezone.now(),
                item.get("id") or 0,
            ),
        )

        builder = ExcelBuilder("Отчет по рейсам", "4F81BD", request)
        headers = [
            "Дата", "Техника", "Госномер", "Водитель",
            "Объект (откуда → куда)", "Вид работы", "Примечание",
            "Рейсы (водитель)", "Рейсы (Incomtek)",
            "Опоздание", "Отработано", "Норма", "Переработка/Недоработка",
        ]
        builder.write_headers(headers)

        num_fmt = {8: "#,##0", 9: "#,##0"}
        center = {1, 3, 8, 9, 10, 11, 12, 13}
        text_columns = {10, 11, 12, 13}
        totals = {
            "claimed": 0,
            "verified": 0,
        }

        def _write_trip_row(values):
            row_num = builder.write_row(
                values,
                number_formats=num_fmt,
                center_columns=center,
            )
            for col_num in text_columns:
                cell = builder.ws.cell(row=row_num, column=col_num)
                if cell.value is None:
                    continue
                cell.value = str(cell.value)
                cell.data_type = "s"
                cell.number_format = "@"
            return row_num

        if group_by in ("day", "week", "month"):
            agg_rows = _aggregate_trips(trips, group_by)
            for row in agg_rows:
                vehicle = row["vehicle"] or {}
                claimed = row["claimed_trips"]
                verified = row["verified_trips"]
                late = int(row.get("late_minutes", 0) or 0)
                worked = row["worked_norm_minutes"]
                norm = 480
                overtime = worked - norm

                totals["claimed"] += claimed
                totals["verified"] += verified

                _write_trip_row([
                    row["period_label"],
                    vehicle.get("name", "-"),
                    vehicle.get("plate_number", "-"),
                    row["driver"],
                    row.get("object_route", "Выберите zone_from и zone_to для сверки"),
                    row.get("work_type", ""),
                    row.get("note", ""),
                    claimed,
                    verified,
                    _format_minutes(late),
                    _format_minutes(worked),
                    _format_minutes(norm),
                    _format_minutes(overtime),
                ])
        else:
            for trip in trips:
                vehicle = trip.get("vehicle") or {}
                stats = trip.get("stats") or {}
                claimed = stats.get("claimed_trips", 0) or 0
                verified = stats.get("verified_trips", 0) or 0
                late = int(stats.get("late_minutes", 0) or 0)
                worked = stats.get("worked_norm_minutes", 0) or 0
                norm = 480
                overtime = worked - norm

                totals["claimed"] += claimed
                totals["verified"] += verified

                _write_trip_row([
                    trip["started_at_dt"].strftime("%d.%m.%Y") if trip.get("started_at_dt") else "",
                    vehicle.get("name", "-"),
                    vehicle.get("plate_number", "-"),
                    trip.get("driver", "-"),
                    trip.get("object_route", "Выберите zone_from и zone_to для сверки"),
                    trip.get("work_type", ""),
                    trip.get("note", stats.get("trips_note", "") or ""),
                    claimed,
                    verified,
                    _format_minutes(late),
                    _format_minutes(worked),
                    _format_minutes(norm),
                    _format_minutes(overtime),
                ])

        totals_row = _write_trip_row([
            "",
            "ИТОГО",
            "",
            "",
            "",
            "",
            "",
            totals["claimed"],
            totals["verified"],
            "",
            "",
            "",
            "",
        ])
        for col_num in range(1, len(headers) + 1):
            builder.ws.cell(row=totals_row, column=col_num).font = builder._bold_font

        builder.auto_fit_columns()
        builder.freeze_header()
        builder.add_auto_filter()

        filename = _build_report_filename("о_рейсах", d_from, d_to)
        return builder.to_response(filename)
