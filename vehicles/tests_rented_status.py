from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.forms import Select
from django.test import RequestFactory, TestCase
from rest_framework.test import APIRequestFactory

from vehicles.admin import VehicleAdmin
from vehicles.models import Vehicle, VehicleAssignment, VehicleRentHistory
from vehicles.serializers import VehicleCardSerializer
from vehicles.views import DashboardStatsView, VehicleViewSet


class RentedStatusTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="driver@example.com",
            password="test12345",
            first_name="Ильяс",
            surname="Токтогонов",
            role="driver",
            status="active",
            telegram_id=1001,
        )
        self.vehicle = Vehicle.objects.create(
            veh_id=9001,
            imei="imei-9001",
            name="Vehicle 9001",
            plate_number="01KG001AAA",
            type="Lorry",
            status=Vehicle.Status.ACTIVE,
        )

    def test_vehicle_status_rented_is_taken_only_from_field(self):
        VehicleAssignment.objects.create(vehicle=self.vehicle, driver=self.user)

        serialized = VehicleCardSerializer(self.vehicle).data

        self.assertEqual(serialized["status"], Vehicle.Status.ACTIVE)

        self.vehicle.status = Vehicle.Status.RENTED
        self.vehicle.save()
        serialized = VehicleCardSerializer(Vehicle.objects.get(pk=self.vehicle.pk)).data

        self.assertEqual(serialized["status"], Vehicle.Status.RENTED)

    def test_dashboard_counts_rented_from_status_field(self):
        VehicleAssignment.objects.create(vehicle=self.vehicle, driver=self.user)

        request = APIRequestFactory().get("/api/vehicles/dashboard/")
        response = DashboardStatsView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["stats"]["count_rented"], 0)

        self.vehicle.status = Vehicle.Status.RENTED
        self.vehicle.save()

        response = DashboardStatsView.as_view()(request)
        self.assertEqual(response.data["stats"]["count_rented"], 1)
        self.assertEqual(len(response.data["rented"]), 1)
        self.assertEqual(response.data["rented"][0]["number"], "01KG001AAA")

    def test_vehicle_filter_rented_uses_status_field(self):
        VehicleAssignment.objects.create(vehicle=self.vehicle, driver=self.user)

        request = APIRequestFactory().get("/api/vehicles/", {"status": "rented"})
        response = VehicleViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["vehicles"], [])

        self.vehicle.status = Vehicle.Status.RENTED
        self.vehicle.save()

        response = VehicleViewSet.as_view({"get": "list"})(request)
        self.assertEqual(len(response.data["vehicles"]), 1)
        self.assertEqual(response.data["vehicles"][0]["status"], Vehicle.Status.RENTED)

    def test_rent_history_is_created_and_closed_on_status_change(self):
        self.vehicle.status = Vehicle.Status.RENTED
        self.vehicle.save()

        history = VehicleRentHistory.objects.get(vehicle=self.vehicle)
        self.assertIsNotNone(history.started_at)
        self.assertIsNone(history.finished_at)

        self.vehicle.status = Vehicle.Status.ACTIVE
        self.vehicle.save()

        history.refresh_from_db()
        self.assertIsNotNone(history.finished_at)

    def test_sync_does_not_overwrite_manual_rented_status(self):
        self.vehicle.status = Vehicle.Status.RENTED
        self.vehicle.save()
        self.vehicle.ignition = False
        self.vehicle.save(update_fields=["ignition"])

        if self.vehicle.status not in {
            Vehicle.Status.RENTED,
            Vehicle.Status.REPAIR,
            Vehicle.Status.LOW_FUEL,
        }:
            self.vehicle.status = Vehicle.Status.ACTIVE if self.vehicle.ignition else Vehicle.Status.IDLE
        self.vehicle.save()

        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.status, Vehicle.Status.RENTED)

    def test_vehicle_admin_status_uses_select_widget(self):
        model_admin = VehicleAdmin(Vehicle, AdminSite())
        form_class = model_admin.get_form(RequestFactory().get("/admin/vehicles/vehicle/add/"))
        self.assertIsInstance(form_class.base_fields["status"].widget, Select)
