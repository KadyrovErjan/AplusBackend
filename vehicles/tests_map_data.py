from datetime import datetime

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from fuels.models import FuelZone
from vehicles.models import Vehicle
from vehicles.type_mapping import get_vehicle_type_label


class GlobalMapDataTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        FuelZone.objects.create(
            name="Test zone",
            zone_type=2,
            center_lat=42.8746,
            center_lon=74.5698,
            radius_m=2000,
        )
        self.vehicle = Vehicle.objects.create(
            veh_id=70001,
            imei="imei-70001",
            name="Unit-70001",
            plate_number="01KG700AA",
            type="Lorry",
            lat=42.8750,
            lon=74.5700,
            ignition=True,
            status="active",
            last_ignition_change_at=timezone.make_aware(datetime(2026, 2, 24, 12, 30)),
        )

    def test_map_data_formats_type_and_date(self):
        response = self.client.get("/api/map/data/")

        self.assertEqual(response.status_code, 200)
        zones = response.data.get("zones", [])
        self.assertTrue(zones)

        vehicles = zones[0].get("vehicles", [])
        self.assertTrue(vehicles)
        vehicle_payload = vehicles[0]

        self.assertEqual(vehicle_payload["veh_id"], self.vehicle.veh_id)
        self.assertEqual(vehicle_payload["type"], get_vehicle_type_label("Lorry"))
        self.assertEqual(vehicle_payload["last_ignition_change_at"], "24-02-2026")
