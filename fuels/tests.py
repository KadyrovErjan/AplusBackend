from django.contrib.auth import get_user_model
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from unittest.mock import patch

from fuels.models import FuelReport, FuelReportPhoto, FuelZone, FuelEvent, FuelEventType
from fuels.services.incomtek_fuel_import import import_fuel_from_incomtek
from vehicles.models import Vehicle, VehicleAssignment


class FuelMultiPhotoTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.driver = User.objects.create_user(
            email="fuel_driver@example.com",
            password="test123",
            first_name="Бакыт",
            surname="Айтиев",
            role="driver",
            status="active",
            telegram_id=888001,
        )
        self.vehicle = Vehicle.objects.create(
            veh_id=9001,
            imei="imei-fuel-9001",
            name="Howo Test",
            plate_number="01KG001AA",
            type="Lorry",
        )
        VehicleAssignment.objects.create(driver=self.driver, vehicle=self.vehicle)

    def test_driver_fuel_report_saves_multiple_photos(self):
        gif_1x1 = (
            b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00ccc,"
            b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
        )
        photo1 = SimpleUploadedFile("fuel_1.gif", gif_1x1, content_type="image/gif")
        photo2 = SimpleUploadedFile("fuel_2.gif", gif_1x1, content_type="image/gif")

        response = self.client.post(
            "/api/users/reports/fuel/",
            data={
                "telegram_id": str(self.driver.telegram_id),
                "liters": "25.5",
                "photo_file_id": "legacy_file_id",
                "photo_file_ids": ["file_1", "file_2"],
                "photos": [photo1, photo2],
            },
        )

        self.assertEqual(response.status_code, 201)
        report = FuelReport.objects.get()
        self.assertEqual(report.liters_from_driver, 25.5)
        self.assertEqual(report.photos.count(), 2)
        self.assertEqual(FuelReportPhoto.objects.filter(report=report).count(), 2)

    def test_fuel_zone_detail_returns_all_driver_photos(self):
        gif_1x1 = (
            b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00ccc,"
            b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
        )
        zone = FuelZone.objects.create(name="Полигон test", zone_type=2)
        report = FuelReport.objects.create(
            driver=self.driver,
            vehicle=self.vehicle,
            liters=313,
            liters_from_driver=313,
            source=FuelReport.Source.MANUAL,
            confirmed=True,
            zone_id=zone.id,
            zone_name=zone.name,
        )

        for idx in range(3):
            FuelReportPhoto.objects.create(
                report=report,
                photo=SimpleUploadedFile(f"fuel_photo_{idx}.gif", gif_1x1, content_type="image/gif"),
                photo_file_id=f"file_{idx}",
            )

        date_str = timezone.localdate().strftime("%d-%m-%Y")
        response = self.client.get(f"/api/fuels/{zone.id}/?date={date_str}")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["vehicles_count"], 1)
        vehicle_data = payload["vehicles"][0]
        self.assertEqual(vehicle_data["liters_from_driver"], 313.0)
        self.assertEqual(len(vehicle_data["photos_from_driver"]), 3)
        self.assertEqual(vehicle_data["photo_from_driver"], vehicle_data["photos_from_driver"][0])


class IncomtekFuelImportTests(TestCase):
    def setUp(self):
        self.vehicle = Vehicle.objects.create(
            veh_id=9101,
            imei="353691843522243",
            name="Howo Import Test",
            plate_number="01KG910AA",
            type="Lorry",
        )

    @patch("fuels.services.incomtek_fuel_import.send_notification")
    @patch("fuels.services.incomtek_fuel_import.PilotClient.get_fuel_report")
    def test_import_creates_fueling_and_drain_events(self, mock_get_fuel_report, mock_send_notification):
        mock_get_fuel_report.return_value = {
            "success": True,
            "fillings": {
                "1700000000": {
                    "ts": 1700000000,
                    "type_id": 1,
                    "liters": "23.2862",
                    "lat": "51.9899",
                    "lon": "47.8203",
                    "address": "Балаково",
                },
                "1700003600": {
                    "ts": 1700003600,
                    "type_id": 2,
                    "liters": "12.0403",
                    "lat": "51.991",
                    "lon": "47.8186",
                    "address": "Балаково",
                },
            },
        }

        created = import_fuel_from_incomtek(
            vehicle=self.vehicle,
            start_ts=1699990000,
            end_ts=1700010000,
        )

        self.assertEqual(created, 2)
        self.assertEqual(FuelEvent.objects.filter(vehicle=self.vehicle).count(), 2)
        self.assertEqual(
            FuelEvent.objects.filter(vehicle=self.vehicle, event_type=FuelEventType.FUELING).count(),
            1,
        )
        self.assertEqual(
            FuelEvent.objects.filter(vehicle=self.vehicle, event_type=FuelEventType.DRAIN).count(),
            1,
        )

        drain_event = FuelEvent.objects.get(vehicle=self.vehicle, event_type=FuelEventType.DRAIN)
        self.assertAlmostEqual(float(drain_event.liters_from_ink), 12.0403, places=4)
        mock_send_notification.assert_called_once()

    @patch("fuels.services.incomtek_fuel_import.send_notification")
    @patch("fuels.services.incomtek_fuel_import.PilotClient.get_fuel_report")
    def test_import_is_idempotent_and_does_not_duplicate_drain_notification(self, mock_get_fuel_report, mock_send_notification):
        mock_get_fuel_report.return_value = {
            "success": True,
            "fillings": {
                "1700003600": {
                    "ts": 1700003600,
                    "type_id": 2,
                    "liters": "12.0403",
                    "lat": "51.991",
                    "lon": "47.8186",
                    "address": "Балаково",
                },
            },
        }

        first_created = import_fuel_from_incomtek(
            vehicle=self.vehicle,
            start_ts=1699990000,
            end_ts=1700010000,
        )
        second_created = import_fuel_from_incomtek(
            vehicle=self.vehicle,
            start_ts=1699990000,
            end_ts=1700010000,
        )

        self.assertEqual(first_created, 1)
        self.assertEqual(second_created, 0)
        self.assertEqual(
            FuelEvent.objects.filter(vehicle=self.vehicle, event_type=FuelEventType.DRAIN).count(),
            1,
        )
        mock_send_notification.assert_called_once()
