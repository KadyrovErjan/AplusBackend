from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from fuels.models import FuelZone
from trips.models import Trip
from trips.services import RouteAnalyticsService, get_filtered_trips_data
from vehicles.models import DriverShift, Vehicle


class TripsServicesTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            email="driver@example.com",
            password="test123",
            first_name="Асан",
            surname="Усонов",
            role="driver",
            status="active",
        )
        self.vehicle = Vehicle.objects.create(
            veh_id=7539,
            imei="imei-7539",
            name="Хово",
            plate_number="01KG603BO",
            type="Lorry",
        )
        self.started_at = timezone.now() - timedelta(hours=3)
        self.ended_at = timezone.now() - timedelta(hours=1)
        self.shift = DriverShift.objects.create(
            driver=self.user,
            vehicle=self.vehicle,
            started_at=self.started_at,
            ended_at=self.ended_at,
            status="completed",
            trips_count=0,
            distance_km=42.6,
            engine_hours=7.8,
        )
        cache.clear()

    def _date_filters(self):
        day = self.started_at.date().isoformat()
        return {"date_from": day, "date_to": day}

    def _date_filters_dd_mm_yyyy(self):
        day = self.started_at.strftime("%d-%m-%Y")
        return {"date_from": day, "date_to": day}

    @staticmethod
    def _report_173_payload():
        return {
            "success": True,
            "data": {
                "Хово_01KG603BO": {
                    "Thursday, 12.02.2026 21:00 - Friday, 13.02.2026 21:00": [
                        {
                            "start": 1770953942,
                            "end": 1770965626,
                            "number": 5,
                            "mileage": 0,
                            "tr": [
                                "1770953942 - 1770954128",
                                "1770955199 - 1770955333",
                                "1770956351 - 1770956504",
                                "1770957708 - 1770957815",
                                "1770965461 - 1770965626",
                            ],
                            "route": "Аристократ => Полигон",
                        },
                        {
                            "start": 1770954572,
                            "end": 1770966157,
                            "number": 5,
                            "mileage": 0,
                            "tr": [
                                "1770954572 - 1770954669",
                                "1770955711 - 1770955800",
                                "1770956885 - 1770956975",
                                "1770958294 - 1770958422",
                                "1770966066 - 1770966157",
                            ],
                            "route": "Полигон => Аристократ",
                        },
                    ]
                }
            },
        }

    def test_history_uses_shift_distance_for_verified_distance(self):
        rows = get_filtered_trips_data(self._date_filters())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stats"]["verified_trips"], 0)
        self.assertEqual(rows[0]["stats"]["verified_distance_km"], 42.6)

    def test_history_deduplicates_identical_shifts(self):
        self.shift.trips_count = 9
        self.shift.trips_note = "Основная смена"
        self.shift.save(update_fields=["trips_count", "trips_note"])

        DriverShift.objects.create(
            driver=self.user,
            vehicle=self.vehicle,
            started_at=self.started_at,
            ended_at=self.ended_at,
            movement_started_at=self.started_at,
            status="completed",
            trips_count=None,
            trips_note=None,
            distance_km=0.0,
            engine_hours=0.0,
        )

        rows = get_filtered_trips_data(self._date_filters())

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], self.shift.id)
        self.assertEqual(rows[0]["stats"]["claimed_trips"], 9)
        self.assertEqual(rows[0]["stats"]["trips_note"], "Основная смена")

    def test_parse_173_counts_only_selected_direction(self):
        service = RouteAnalyticsService()
        stats = service._parse_173_response(
            response=self._report_173_payload(),
            vehicle=self.vehicle,
            tz=timezone.get_current_timezone(),
            zone_from_name="Аристократ",
            zone_to_name="Полигон",
        )
        self.assertEqual(stats["count"], 5)

    @patch("trips.services.PilotClient.get_route_report")
    def test_history_supports_legacy_route_names(self, mock_get_route_report):
        FuelZone.objects.create(name="Аристократ", external_id=1952)
        FuelZone.objects.create(name="Полигон", external_id=2000)
        mock_get_route_report.return_value = self._report_173_payload()

        rows = get_filtered_trips_data(
            {
                **self._date_filters(),
                "route_from": "Аристократ",
                "route_to": "Полигон",
            }
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stats"]["verified_trips"], 5)
        self.assertEqual(rows[0]["stats"]["verification_status"], "ok")
        self.assertEqual(rows[0]["stats"]["verified_distance_km"], 42.6)

    @patch("trips.services.PilotClient.get_route_report")
    def test_history_accepts_dd_mm_yyyy_date_filters(self, mock_get_route_report):
        FuelZone.objects.create(name="Аристократ", external_id=1952)
        FuelZone.objects.create(name="Полигон", external_id=2000)
        mock_get_route_report.return_value = self._report_173_payload()

        rows = get_filtered_trips_data(
            {
                **self._date_filters_dd_mm_yyyy(),
                "zone_from": "1952",
                "zone_to": "2000",
            }
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stats"]["verified_trips"], 5)

    @patch("trips.services.RouteAnalyticsService.get_verified_route_stats")
    def test_history_filters_out_rows_without_selected_route(self, mock_get_verified_route_stats):
        FuelZone.objects.create(name="Аристократ", external_id=1952)
        FuelZone.objects.create(name="Полигон", external_id=2000)
        other_shift = DriverShift.objects.create(
            driver=self.user,
            vehicle=self.vehicle,
            started_at=self.started_at + timedelta(minutes=5),
            ended_at=self.ended_at + timedelta(minutes=5),
            status="completed",
            trips_count=2,
            distance_km=12.5,
            engine_hours=1.5,
        )
        mock_get_verified_route_stats.return_value = {
            self.shift.id: {"count": 5, "distance_km": 0.0, "validated": True, "error": None},
            other_shift.id: {"count": 0, "distance_km": 0.0, "validated": True, "error": None},
        }

        rows = get_filtered_trips_data(
            {
                **self._date_filters_dd_mm_yyyy(),
                "zone_from": "1952",
                "zone_to": "2000",
            },
            include_object_route=True,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], self.shift.id)
        self.assertEqual(rows[0]["stats"]["verified_trips"], 5)
        self.assertEqual(rows[0]["object_route"], "Аристократ - Полигон")


    def test_history_defaults_missing_date_to_to_today(self):
        today = timezone.localdate()
        future_day = today + timedelta(days=1)
        future_start = timezone.make_aware(datetime.combine(future_day, datetime.min.time()))
        future_end = future_start + timedelta(hours=8)

        DriverShift.objects.create(
            driver=self.user,
            vehicle=self.vehicle,
            started_at=future_start,
            ended_at=future_end,
            status="completed",
            trips_count=1,
            distance_km=5.0,
            engine_hours=1.0,
        )

        rows = get_filtered_trips_data({"date_from": today.strftime("%d-%m-%Y")})

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], self.shift.id)

    @patch("trips.services.RouteAnalyticsService.get_verified_route_stats")
    def test_history_uses_local_trip_queryset_for_selected_zone_pair(self, mock_get_verified_route_stats):
        FuelZone.objects.create(name="Аристократ", external_id=1952)
        FuelZone.objects.create(name="Полигон", external_id=2000)

        Trip.objects.create(
            vehicle=self.vehicle,
            start_zone_name="Аристократ",
            end_zone_name="Полигон",
            start_at=self.started_at + timedelta(minutes=5),
            arrival_at=self.started_at + timedelta(minutes=35),
            end_at=self.started_at + timedelta(minutes=45),
            trip_duration_seconds=1800,
            dwell_duration_seconds=600,
            distance_km=3.5,
        )

        rows = get_filtered_trips_data(
            {
                **self._date_filters_dd_mm_yyyy(),
                "zone_from": "1952",
                "zone_to": "2000",
            },
            include_object_route=True,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stats"]["verified_trips"], 1)
        self.assertEqual(rows[0]["object_route"], "Аристократ - Полигон")
        mock_get_verified_route_stats.assert_not_called()

    @patch("trips.services.RouteAnalyticsService.get_verified_route_stats")
    def test_history_includes_overlapping_shift_when_trip_is_inside_requested_day(self, mock_get_verified_route_stats):
        zone_from = FuelZone.objects.create(name="Аристократ", external_id=1946)
        zone_to = FuelZone.objects.create(name="Полигон", external_id=1943)
        day = timezone.localdate()
        shift_start = timezone.make_aware(datetime.combine(day - timedelta(days=1), datetime.max.time())) - timedelta(minutes=30)
        shift_end = timezone.make_aware(datetime.combine(day, datetime.min.time())) + timedelta(hours=2)

        self.shift.started_at = shift_start
        self.shift.ended_at = shift_end
        self.shift.save(update_fields=["started_at", "ended_at"])

        Trip.objects.create(
            vehicle=self.vehicle,
            start_zone_name=zone_from.name,
            end_zone_name=zone_to.name,
            start_at=timezone.make_aware(datetime.combine(day, datetime.min.time())) + timedelta(minutes=20),
            arrival_at=timezone.make_aware(datetime.combine(day, datetime.min.time())) + timedelta(minutes=50),
            end_at=timezone.make_aware(datetime.combine(day, datetime.min.time())) + timedelta(minutes=55),
            trip_duration_seconds=1800,
            dwell_duration_seconds=300,
            distance_km=4.0,
        )

        rows = get_filtered_trips_data(
            {
                "date_from": day.strftime("%d-%m-%Y"),
                "date_to": day.strftime("%d-%m-%Y"),
                "zone_from": str(zone_from.external_id),
                "zone_to": str(zone_to.external_id),
            },
            include_object_route=True,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], self.shift.id)
        self.assertEqual(rows[0]["stats"]["verified_trips"], 1)
        mock_get_verified_route_stats.assert_not_called()


class TripsHistoryNotesApiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            email="driver-note@example.com",
            password="test123",
            first_name="Тилек",
            surname="Торжуков",
            role="driver",
            status="active",
            telegram_id=99887766,
        )
        self.vehicle = Vehicle.objects.create(
            veh_id=9911,
            imei="imei-9911",
            name="Хово",
            plate_number="01KG166BN",
            type="Lorry",
        )
        self.shift = DriverShift.objects.create(
            driver=self.user,
            vehicle=self.vehicle,
            started_at=timezone.now() - timedelta(hours=2),
            status="completed",
        )
        self.client = APIClient()

    @patch("trips.views.sync_single_vehicle_task.delay")
    def test_post_history_saves_trips_note(self, mock_sync_delay):
        payload = {
            "telegram_id": self.user.telegram_id,
            "trips_count": 6,
            "note": "Пробка на разгрузке, работал в штатном режиме",
        }

        response = self.client.post("/api/trips/history/", payload, format="json")

        self.assertEqual(response.status_code, 201)
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.trips_count, 6)
        self.assertEqual(self.shift.trips_note, payload["note"])
        self.assertEqual(response.data.get("note"), payload["note"])
        mock_sync_delay.assert_called_once_with(self.vehicle.veh_id)

    def test_filtered_data_returns_trips_note(self):
        self.shift.trips_count = 3
        self.shift.trips_note = "Рейс с ожиданием на объекте"
        self.shift.save(update_fields=["trips_count", "trips_note"])

        day = self.shift.started_at.date().isoformat()
        rows = get_filtered_trips_data({"date_from": day, "date_to": day})

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stats"]["trips_note"], "Рейс с ожиданием на объекте")
