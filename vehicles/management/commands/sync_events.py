from django.core.management.base import BaseCommand
from services.pilot_client import pilot_request_sync
from vehicles.models import Vehicle, Event


class Command(BaseCommand):
    help = "Sync events"

    def handle(self, *args, **kwargs):
        for v in Vehicle.objects.all():
            data = pilot_request_sync("ag_events", 14, {"imei": v.imei})

            for e in data.get("data", []):
                Event.objects.update_or_create(
                    vehicle=v,
                    ts=e["sec"],
                    defaults={
                        "event_type": e["type"],
                        "value": str(e.get("value"))
                    }
                )

        self.stdout.write("Events synced")
