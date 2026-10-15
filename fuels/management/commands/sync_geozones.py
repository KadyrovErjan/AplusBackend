from django.core.management.base import BaseCommand
from fuels.models import FuelZone
from services.pilot_client import pilot_request_sync


class Command(BaseCommand):
    help = "Sync Geozones from Incomtek into FuelZone table"

    def handle(self, *args, **kwargs):
        self.stdout.write("Requesting geozones from Pilot (Node 14)...")

        response = pilot_request_sync("geofencelist", 14)

        if not response:
            self.stdout.write(self.style.ERROR("Empty response from API"))
            return

        zones_data = response.get("data", [])

        if not zones_data:
            self.stdout.write(self.style.WARNING(f"No zones found in response. Keys: {response.keys()}"))
            return

        count = 0
        for z in zones_data:
            try:
                geom = z.get("geometry", {})
                g_type = geom.get("type")
                coords = geom.get("coordinates")

                defaults = {
                    "name": z.get("zonename"),
                    "zone_type": 2 if g_type == "Circle" else 1,
                }

                if g_type == "Circle":
                    if coords and len(coords) == 2:
                        defaults["center_lon"] = float(coords[0])
                        defaults["center_lat"] = float(coords[1])
                    defaults["radius_m"] = float(geom.get("radius", 0))
                    defaults["polygon_coords"] = None

                elif g_type == "Polygon":
                    if coords and len(coords) > 0:
                        defaults["polygon_coords"] = coords[0] 
                    defaults["center_lat"] = None
                    defaults["center_lon"] = None
                    defaults["radius_m"] = None

                obj, created = FuelZone.objects.update_or_create(
                    external_id=z.get("id"),
                    defaults=defaults
                )
                action = "Created" if created else "Updated"
                count += 1

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error parsing zone {z.get('id')}: {e}"))

        self.stdout.write(self.style.SUCCESS(f"Successfully synced {count} zones to FuelZone table"))
