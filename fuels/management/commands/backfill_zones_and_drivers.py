from django.core.management.base import BaseCommand
from fuels.models import FuelReport, FuelZone
from django.utils import timezone
from fuels.services.processor import _call_incomtek_run, _pick_zone_from_run, get_current_driver_for_vehicle
import time

class Command(BaseCommand):
    help = "Backfill zone_id/zone_name/driver for existing FuelReport where they are null."

    def handle(self, *args, **options):
        qs = FuelReport.objects.filter(zone_id__isnull=True) | FuelReport.objects.filter(driver__isnull=True)
        qs = qs.order_by("created_at")
        for fr in qs:
            ts = int(fr.created_at.timestamp())
            imei = fr.vehicle.imei if fr.vehicle else None
            if imei:
                try:
                    run_json = _call_incomtek_run(imei, ts)
                    zone = _pick_zone_from_run(run_json)
                    if zone:
                        ext_id = zone.get("startuid") or zone.get("stopuid") or zone.get("uid") or zone.get("id")
                        zone_obj = FuelZone.objects.filter(external_id=ext_id).first() if ext_id else None
                        if zone_obj:
                            fr.zone_id = zone_obj.id
                            fr.zone_name = zone_obj.name
                        else:
                            fr.zone_name = zone.get("startname") or zone.get("stopname") or fr.zone_name
                except Exception:
                    pass
            if not fr.driver and fr.vehicle:
                driver = get_current_driver_for_vehicle(fr.vehicle)
                if driver:
                    fr.driver = driver
            fr.save(update_fields=["zone_id", "zone_name", "driver"])
            self.stdout.write(f"Updated FuelReport {fr.id}")
            time.sleep(0.1)
