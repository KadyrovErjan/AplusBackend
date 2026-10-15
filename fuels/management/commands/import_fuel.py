
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from vehicles.models import Vehicle
from fuels.services.incomtek_fuel_import import import_fuel_from_incomtek


class Command(BaseCommand):
    help = "Import fuel events from Incomtek (cmd=fuel)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=1,
            help="How many days back to import (default: 1)",
        )

        parser.add_argument(
            "--vehicle-id",
            type=int,
            help="veh_id of a single vehicle (optional)",
        )

    def handle(self, *args, **options):
        days = options["days"]
        vehicle_id = options.get("vehicle_id")

        end_dt = timezone.now()
        start_dt = end_dt - timedelta(days=days)

        start_ts = int(start_dt.timestamp())
        end_ts = int(end_dt.timestamp())

        if vehicle_id:
            vehicles = Vehicle.objects.filter(veh_id=vehicle_id)
        else:
            vehicles = Vehicle.objects.exclude(imei__isnull=True).exclude(imei="")

        if not vehicles.exists():
            self.stdout.write(self.style.WARNING("No vehicles found"))
            return

        total_created = 0

        for vehicle in vehicles:
            self.stdout.write(
                f"⛽ Importing fuel for vehicle {vehicle.veh_id} ({vehicle.imei})"
            )

            created = import_fuel_from_incomtek(
                vehicle=vehicle,
                start_ts=start_ts,
                end_ts=end_ts,
            )

            self.stdout.write(
                f"   ➜ created {created} fuel events"
            )

            total_created += created

        self.stdout.write(
            self.style.SUCCESS(
                f"✅ Import finished. Total created: {total_created}"
            )
        )
