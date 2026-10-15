from collections import defaultdict
from datetime import datetime

from django.db.models import Q
from django.utils import timezone
from django.utils.timezone import localtime

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import generics
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from fuels.models import FuelEvent, FuelReport
from fuels.models import FuelZone
from fuels.serializers import FuelZoneSerializer
from fuels.services.zone_filters import parse_zone_filter_params, apply_zone_filter, NO_ZONE_ID
from vehicles.serializers import FrontendVehicleSerializer, VehicleCardSerializer
from vehicles.models import Vehicle
from fuels.utils.geo import is_point_in_circle, is_point_in_polygon_normalized
from vehicles.serializers import MapVehicleSerializer
import time
from fuels.services.incomtek_fuel_import import import_fuel_from_incomtek
from django.db import transaction
import openpyxl
from openpyxl.styles import Font, PatternFill, Border, Side
from django.http import HttpResponse


class FuelZoneListView(generics.ListAPIView):
    """
    GET /api/objects/
    Возвращает список геозон, и ВНУТРИ каждой зоны список техники, которая там находится.
    """
    serializer_class = FuelZoneSerializer

    @swagger_auto_schema(
        tags=["🌍 Objects"],
        operation_summary="Список объектов с техникой внутри",
        operation_description="Возвращает массив зон. У каждой зоны есть поле 'vehicles', содержащее список машин, находящихся сейчас в этой зоне."
    )
    def get(self, request, *args, **kwargs):
        queryset = FuelZone.objects.all().order_by('name')

        search = request.query_params.get('search')
        if search:
            queryset = queryset.filter(name__icontains=search)

        zones_data = FuelZoneSerializer(queryset, many=True).data

        for z in zones_data:
            z["vehicles"] = []

        vehicles = (
            Vehicle.objects
            .exclude(lat__isnull=True)
            .exclude(lon__isnull=True)
            .select_related() 
        )

        vehicles_data = MapVehicleSerializer(vehicles, many=True).data

        for v in vehicles_data:
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

        return Response(zones_data)


class FuelZoneVehiclesView(APIView):
    """
    Возвращает список техники, которая ПРЯМО СЕЙЧАС находится внутри указанной зоны.
    """

    @swagger_auto_schema(
        tags=["🌍 Objects"],
        operation_summary="Техника внутри объекта (сейчас)",
        operation_description="Проверяет текущие координаты всех активных машин и возвращает тех, кто находится внутри геозоны."
    )
    def get(self, request, zone_id):
        try:
            zone = FuelZone.objects.get(id=zone_id)
        except FuelZone.DoesNotExist:
            return Response({"error": "Zone not found"}, 404)

        vehicles = Vehicle.objects.filter(lat__isnull=False, lon__isnull=False)

        vehicles_inside = []

        for v in vehicles:
            is_inside = False

            if zone.zone_type == 2:
                is_inside = is_point_in_circle(
                    v.lat, v.lon,
                    zone.center_lat, zone.center_lon,
                    zone.radius_m
                )

            elif zone.zone_type == 1:
                is_inside = is_point_in_polygon_normalized(
                    v.lat, v.lon,
                    zone.polygon_coords
                )

            if is_inside:
                vehicles_inside.append(v)

        serializer = VehicleCardSerializer(vehicles_inside, many=True, context={"request": request})

        return Response({
            "zone_id": zone.id,
            "zone_name": zone.name,
            "count": len(vehicles_inside),
            "vehicles": serializer.data
        })


class FuelZoneSimpleListView(generics.ListAPIView):
    """
    GET /api/fuels/zones/
    Возвращает список геозон (ID, Name, coords) для селекторов.
    Без тяжелых вычислений техники внутри.
    """
    serializer_class = FuelZoneSerializer

    def get_queryset(self):
        return FuelZone.objects.all().order_by('name')


class FuelZoneNamesView(APIView):
    """
    GET /api/fuels/names/
    Возвращает список названий всех геозон (для выпадающих списков).
    """
    @swagger_auto_schema(tags=["⛽ Fuels Analytics"])
    def get(self, request):
        search = request.query_params.get("search")
        qs = FuelZone.objects.values_list("name", flat=True).distinct()
        if search:
            qs = qs.filter(name__icontains=search)
        names_list = list(qs.order_by("name"))
        return Response({"names": names_list})


DATE_FMT = "%d-%m-%Y"
DATETIME_FMT = "%d-%m-%Y %H:%M"


class FuelListView(APIView):
    """
    GET /api/fuels/
    """

    @swagger_auto_schema(
        tags=["⛽ Fuels Analytics"],
        manual_parameters=[
            openapi.Parameter("zone_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description=f"ID объекта. {NO_ZONE_ID} = Вне объекта"),
            openapi.Parameter(
                "zone_ids",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description=f"CSV или repeated список ID объектов. Пример: zone_ids=1,2 или zone_ids=1&zone_ids=2. {NO_ZONE_ID} = Вне объекта",
            ),
            openapi.Parameter("zone_name", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("date_from", openapi.IN_QUERY, type=openapi.TYPE_STRING, description="DD-MM-YYYY"),
            openapi.Parameter("date_to", openapi.IN_QUERY, type=openapi.TYPE_STRING, description="DD-MM-YYYY"),
            openapi.Parameter("group_by", openapi.IN_QUERY, type=openapi.TYPE_STRING, enum=['day', 'total'],
                              default='day'),
        ]
    )
    def get(self, request):
        group_mode = request.query_params.get("group_by", "day")

        date_from_str = request.query_params.get("date_from")
        date_to_str = request.query_params.get("date_to")

        today = timezone.now().date()
        start_date = today
        end_date = today

        try:
            if date_from_str:
                start_date = datetime.strptime(date_from_str, "%d-%m-%Y").date()
                end_date = datetime.strptime(date_to_str, "%d-%m-%Y").date() if date_to_str else today
        except ValueError:
            pass

        start_dt = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
        end_dt = timezone.make_aware(datetime.combine(end_date, datetime.max.time()))

        events_qs = FuelEvent.objects.filter(
            created_at__range=(start_dt, end_dt),
            event_type=1
        ).select_related("vehicle")

        reports_qs = FuelReport.objects.filter(created_at__range=(start_dt, end_dt)).select_related("vehicle", "driver")

        zone_filter, zone_error = parse_zone_filter_params(request.query_params)
        if zone_error:
            return Response({"error": zone_error}, 400)

        events_qs, reports_qs = apply_zone_filter(events_qs, reports_qs, zone_filter)

        zones = {}

        def get_group_key(item_zone_id, item_dt, item_zone_name):
            z_id = item_zone_id if item_zone_id is not None else NO_ZONE_ID
            local_dt = localtime(item_dt)
            date_str = local_dt.strftime(DATE_FMT)

            if z_id == NO_ZONE_ID:
                key_suffix = date_str
                z_name = "Вне объекта"
            else:
                z_name = item_zone_name or f"Zone {z_id}"
                key_suffix = date_str if group_mode == 'day' else "total"

            return (z_id, key_suffix), z_name, date_str

        def process_item(item, liters, is_event=True):
            i_zid = item.zone_id
            i_zname = item.zone_name if is_event else getattr(item, 'zone_name', None)

            key, z_name, date_str = get_group_key(i_zid, item.created_at, i_zname)

            if key not in zones:
                if group_mode == 'total' and key[0] != NO_ZONE_ID:
                    d_disp = f"{start_date.strftime(DATE_FMT)} - {end_date.strftime(DATE_FMT)}"
                else:
                    d_disp = date_str

                zones[key] = {
                    "zone_id": key[0],
                    "zone_name": z_name,
                    "date": d_disp,
                    "total_liters_from_ink": 0.0,
                    "total_liters_from_drivers": 0.0,
                    "vehicles_set": set(),
                    "_sort_ts": item.created_at.timestamp()
                }

            if zones[key]["zone_name"].startswith("Zone ") and z_name and not z_name.startswith("Zone"):
                zones[key]["zone_name"] = z_name

            val = float(liters or 0)
            if is_event:
                if item.event_type == 1:
                    zones[key]["total_liters_from_ink"] += val
            else:
                zones[key]["total_liters_from_drivers"] += val

            if item.vehicle_id:
                zones[key]["vehicles_set"].add(item.vehicle_id)

            if item.created_at.timestamp() > zones[key]["_sort_ts"]:
                zones[key]["_sort_ts"] = item.created_at.timestamp()

        for e in events_qs: process_item(e, e.liters_from_ink, True)
        for r in reports_qs: process_item(r, r.liters_from_driver, False)

        out = []
        for v in zones.values():
            if v["total_liters_from_ink"] == 0 and v["total_liters_from_drivers"] == 0:
                continue

            out.append({
                "zone_id": v["zone_id"],
                "zone_name": v["zone_name"],
                "date": v["date"],
                "total_liters_from_ink": round(v["total_liters_from_ink"], 2),
                "total_liters_from_drivers": round(v["total_liters_from_drivers"], 2),
                "vehicles_count": len(v["vehicles_set"]),
                "_ts": v["_sort_ts"]
            })

        out.sort(key=lambda x: x["_ts"], reverse=True)
        for i in out: del i["_ts"]

        return Response(out)


class FuelZoneDetailView(APIView):
    """
    GET /api/fuels/<zone_id>/
    Возвращает детализацию строго за ОДИН день.
    Параметр ?date=DD-MM-YYYY обязателен.
    ТОЛЬКО ЗАПРАВКИ (Сливы исключены).
    """

    @swagger_auto_schema(
        tags=["⛽ Fuels Analytics"],
        operation_summary="Детализация зоны за 1 день (без сливов)",
        manual_parameters=[
            openapi.Parameter("date", openapi.IN_QUERY, type=openapi.TYPE_STRING, description="DD-MM-YYYY"),
        ]
    )
    def get(self, request, zone_id):
        try:
            zone_id = int(zone_id)
        except ValueError:
            return Response({"error": "Invalid ID"}, 400)

        date_str = request.query_params.get("date")
        today = timezone.now().date()
        target_date = today

        if date_str:
            try:
                target_date = datetime.strptime(date_str, "%d-%m-%Y").date()
            except ValueError:
                pass

        start_dt = timezone.make_aware(datetime.combine(target_date, datetime.min.time()))
        end_dt = timezone.make_aware(datetime.combine(target_date, datetime.max.time()))

        if zone_id == NO_ZONE_ID:
            zone_filter_events = {"zone__isnull": True}
            zone_filter_reports = {"zone_id__isnull": True}
            zone_name_display = "Вне объекта"
        else:
            zone_filter_events = {"zone_id": zone_id}
            zone_filter_reports = {"zone_id": zone_id}
            fz = FuelZone.objects.filter(id=zone_id).first()
            zone_name_display = fz.name if fz else "Unknown Zone"

        events_qs = FuelEvent.objects.filter(
            created_at__range=(start_dt, end_dt),
            event_type=1,
            **zone_filter_events
        ).select_related("vehicle").order_by('created_at')

        reports_qs = FuelReport.objects.filter(
            created_at__range=(start_dt, end_dt),
            **zone_filter_reports
        ).filter(
            Q(liters_from_driver__gt=0) | Q(photo__isnull=False) | Q(photos__isnull=False)
        ).select_related("vehicle", "driver").prefetch_related("photos").distinct()

        agg_data = defaultdict(lambda: {
            "vehicle_id": None,
            "vehicle_name": "Unknown",
            "plate_number": "---",
            "liters_from_ink": 0.0,
            "liters_from_driver": 0.0,
            "photo_from_driver": None,
            "photos_from_driver": [],
            "sensor_events": []
        })

        total_zone_ink = 0.0
        total_zone_driver = 0.0

        for e in events_qs:
            vid = e.vehicle_id
            if not vid: continue

            if agg_data[vid]["vehicle_id"] is None:
                agg_data[vid]["vehicle_id"] = vid
                if e.vehicle:
                    fv = FrontendVehicleSerializer.from_vehicle_obj(e.vehicle)
                    agg_data[vid]["vehicle_name"] = fv.get("name")
                    agg_data[vid]["plate_number"] = fv.get("number")

            val = float(e.liters_from_ink or 0)
            dt_local = localtime(e.created_at)

            agg_data[vid]["sensor_events"].append({
                "id": e.id,
                "created_at": dt_local.strftime("%H:%M"),
                "timestamp": dt_local.strftime(DATETIME_FMT),
                "liters": round(val, 2),
                "type": "Refill"
            })

            agg_data[vid]["liters_from_ink"] += val
            total_zone_ink += val

        for r in reports_qs:
            vid = r.vehicle_id
            if not vid: continue

            if agg_data[vid]["vehicle_id"] is None:
                agg_data[vid]["vehicle_id"] = vid
                if r.vehicle:
                    fv = FrontendVehicleSerializer.from_vehicle_obj(r.vehicle)
                    agg_data[vid]["vehicle_name"] = fv.get("name")
                    agg_data[vid]["plate_number"] = fv.get("number")

            val = float(r.liters_from_driver or 0)
            agg_data[vid]["liters_from_driver"] += val
            total_zone_driver += val

            photo_urls = [
                request.build_absolute_uri(item.photo.url)
                for item in r.photos.all()
                if item.photo
            ]
            if not photo_urls and r.photo:
                photo_urls = [request.build_absolute_uri(r.photo.url)]

            for url in photo_urls:
                if url not in agg_data[vid]["photos_from_driver"]:
                    agg_data[vid]["photos_from_driver"].append(url)

            if agg_data[vid]["photo_from_driver"] is None and agg_data[vid]["photos_from_driver"]:
                agg_data[vid]["photo_from_driver"] = agg_data[vid]["photos_from_driver"][0]

        vehicles_list = []
        for vid, data in agg_data.items():
            data["liters_from_ink"] = round(data["liters_from_ink"], 2)
            data["liters_from_driver"] = round(data["liters_from_driver"], 2)
            vehicles_list.append(data)

        vehicles_list.sort(key=lambda x: max(x["liters_from_ink"], x["liters_from_driver"]), reverse=True)

        return Response({
            "zone_id": zone_id,
            "zone_name": zone_name_display,
            "date": target_date.strftime(DATE_FMT),
            "total_liters_from_ink": round(total_zone_ink, 2),
            "total_liters_from_drivers": round(total_zone_driver, 2),
            "vehicles_count": len(vehicles_list),
            "vehicles": vehicles_list
        })
