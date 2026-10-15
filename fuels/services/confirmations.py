from fuels.models import FuelReport, FuelEvent
from vehicles.models import Vehicle, VehicleAssignment
from django.utils import timezone

def confirm_by_driver(fuel_report_id: int = None, vehicle_imei: str = None, ts: int = None,
                      liters_from_driver: float = None, photo=None, driver=None):
    """
    Создаёт или обновляет FuelReport на основании данных от водителя.
    Пытается связать запись с существующим FuelEvent (и его zone) по vehicle+ts (или по активному назначению водителя).
    """
    vehicle = None
    if vehicle_imei:
        try:
            vehicle = Vehicle.objects.get(imei=vehicle_imei)
        except Vehicle.DoesNotExist:
            vehicle = None

    if fuel_report_id:
        fr = FuelReport.objects.get(id=fuel_report_id)
    else:
        if not vehicle and driver:
            try:
                assign = VehicleAssignment.objects.filter(driver=driver, unassigned_at__isnull=True).select_related("vehicle").order_by("-assigned_at").first()
                if assign:
                    vehicle = assign.vehicle
            except Exception:
                vehicle = None

        fr = None
        if vehicle and ts:
            window_start = timezone.datetime.fromtimestamp(ts - 3600, tz=timezone.get_current_timezone())
            window_end = timezone.datetime.fromtimestamp(ts + 3600, tz=timezone.get_current_timezone())
            fr = FuelReport.objects.filter(vehicle=vehicle, created_at__range=(window_start, window_end)).order_by("-created_at").first()

    if not fr:
        zone_id = None
        zone_name = None
        if vehicle and ts:
            lo = timezone.datetime.fromtimestamp(ts - 3600, tz=timezone.get_current_timezone())
            hi = timezone.datetime.fromtimestamp(ts + 3600, tz=timezone.get_current_timezone())
            ev = FuelEvent.objects.filter(vehicle=vehicle, created_at__range=(lo, hi)).order_by("-created_at").first()
            if ev:
                zone_id = ev.zone_id
                zone_name = ev.zone_name

        if not zone_id and vehicle and not ts:
            ev = FuelEvent.objects.filter(vehicle=vehicle).order_by("-created_at").first()
            if ev and abs((timezone.now() - ev.created_at).total_seconds()) <= 3600:
                zone_id = ev.zone_id
                zone_name = ev.zone_name

        fr = FuelReport.objects.create(
            vehicle=vehicle,
            driver=driver,
            liters_from_driver=liters_from_driver,
            liters=liters_from_driver or 0,
            photo=photo,
            source=FuelReport.Source.MANUAL,
            confirmed=True,
            zone_id=zone_id,
            zone_name=zone_name or "Вне объекта",
            created_at=timezone.now()
        )
        return fr

    updated = False
    if liters_from_driver is not None:
        fr.liters_from_driver = liters_from_driver
        if not fr.liters:
            fr.liters = liters_from_driver
        updated = True

    if driver and not fr.driver:
        fr.driver = driver
        updated = True

    if photo and not getattr(fr, "photo", None):
        fr.photo = photo
        updated = True

    if not fr.zone_id and fr.vehicle:
        ev = FuelEvent.objects.filter(vehicle=fr.vehicle).order_by("-created_at").first()
        if ev and abs((ev.created_at - fr.created_at).total_seconds()) <= 3600:
            fr.zone_id = ev.zone_id
            fr.zone_name = ev.zone_name
            updated = True

    if updated:
        fr.save(update_fields=["liters_from_driver", "liters", "driver", "photo", "zone_id", "zone_name"])

    return fr
