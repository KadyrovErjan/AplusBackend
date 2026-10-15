from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from maintenance.models import IssueReport, IssueReportPhoto, IssueReportProblem
from vehicles.models import Vehicle, VehicleAssignment


class RepairsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.driver = self._create_driver(
            email="driver.repairs@example.com",
            telegram_id=999111,
            first_name="Ильяс",
            surname="Токтогонов",
        )
        self.vehicle = self._create_vehicle(veh_id=7001, imei="imei-7001")
        VehicleAssignment.objects.create(driver=self.driver, vehicle=self.vehicle)

    @staticmethod
    def _create_driver(email, telegram_id, first_name, surname):
        User = get_user_model()
        return User.objects.create_user(
            email=email,
            password="test12345",
            first_name=first_name,
            surname=surname,
            role="driver",
            status="active",
            telegram_id=telegram_id,
        )

    @staticmethod
    def _create_vehicle(veh_id, imei):
        return Vehicle.objects.create(
            veh_id=veh_id,
            imei=imei,
            name=f"Vehicle {veh_id}",
            plate_number=f"PLATE-{veh_id}",
            type="Lorry",
        )

    @patch("maintenance.views.send_notification")
    def test_issue_report_reuses_active_report_same_driver_vehicle(self, mock_notify):
        first = self.client.post(
            "/api/maintenance/issues/",
            {"telegram_id": self.driver.telegram_id, "description": "Пробита шина"},
            format="json",
        )
        second = self.client.post(
            "/api/maintenance/issues/",
            {"telegram_id": self.driver.telegram_id, "description": "Давление масла низкое"},
            format="json",
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(IssueReport.objects.count(), 1)
        self.assertEqual(IssueReportProblem.objects.count(), 2)

        report = IssueReport.objects.get()
        self.assertEqual(first.data["report_id"], report.id)
        self.assertEqual(second.data["report_id"], report.id)
        self.assertTrue(first.data["created_new"])
        self.assertFalse(second.data["created_new"])
        self.assertEqual(report.description, "Давление масла низкое")
        self.assertIsNotNone(report.last_problem_at)
        self.assertEqual(mock_notify.call_count, 2)

    @patch("maintenance.views.send_notification")
    def test_issue_report_creates_new_after_resolved(self, mock_notify):
        first = self.client.post(
            "/api/maintenance/issues/",
            {"telegram_id": self.driver.telegram_id, "description": "Не крутит стартер"},
            format="json",
        )
        self.assertEqual(first.status_code, 200)

        old_report = IssueReport.objects.get(id=first.data["report_id"])
        old_report.status = "resolved"
        old_report.resolved_at = timezone.now()
        old_report.save(update_fields=["status", "resolved_at"])

        second = self.client.post(
            "/api/maintenance/issues/",
            {"telegram_id": self.driver.telegram_id, "description": "Подтекает масло"},
            format="json",
        )
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.data["created_new"])

        self.assertEqual(IssueReport.objects.count(), 2)
        new_report = IssueReport.objects.get(id=second.data["report_id"])
        self.assertNotEqual(new_report.id, old_report.id)
        self.assertEqual(new_report.status, "pending")
        self.assertEqual(
            IssueReport.objects.filter(
                driver=self.driver,
                vehicle=self.vehicle,
                status__in=["pending", "in_progress"],
            ).count(),
            1,
        )
        self.assertEqual(mock_notify.call_count, 2)

    @patch("maintenance.views.send_notification")
    def test_issue_report_returns_error_without_active_vehicle_assignment(self, mock_notify):
        driver_no_vehicle = self._create_driver(
            email="driver.no.vehicle@example.com",
            telegram_id=999112,
            first_name="Арсен",
            surname="Калиев",
        )
        response = self.client.post(
            "/api/maintenance/issues/",
            {"telegram_id": driver_no_vehicle.telegram_id, "description": "Нет тормозов"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"], "driver_has_no_vehicle")
        self.assertEqual(IssueReport.objects.count(), 0)
        mock_notify.assert_not_called()

    @patch("maintenance.views.send_notification")
    def test_issue_report_handles_integrity_race_and_reuses_existing(self, mock_notify):
        original_create = IssueReport.objects.create
        state = {"raised": False}

        def create_with_race(**kwargs):
            if not state["raised"]:
                state["raised"] = True
                original_create(**kwargs)
                raise IntegrityError("simulated race")
            return original_create(**kwargs)

        with patch("maintenance.views.IssueReport.objects.create", side_effect=create_with_race):
            response = self.client.post(
                "/api/maintenance/issues/",
                {"telegram_id": self.driver.telegram_id, "description": "Глохнет двигатель"},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["created_new"])
        self.assertEqual(IssueReport.objects.count(), 1)
        self.assertEqual(IssueReportProblem.objects.count(), 1)
        mock_notify.assert_called_once()

    def test_repairs_list_returns_card_fields_and_preview_photo(self):
        report = IssueReport.objects.create(
            driver=self.driver,
            vehicle=self.vehicle,
            description="Проблема в гидравлике",
            status="pending",
            last_problem_at=timezone.now(),
        )
        problem = IssueReportProblem.objects.create(
            report=report,
            description="Проблема в гидравлике",
        )
        IssueReportPhoto.objects.create(
            report=report,
            problem=problem,
            photo="issue_reports/first-problem.jpg",
        )

        response = self.client.get("/api/maintenance/issues/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        item = response.data[0]
        self.assertEqual(set(item.keys()), {"id", "status", "preview_photo", "vehicle", "driver"})
        self.assertEqual(item["status"], "pending")
        self.assertIsInstance(item["vehicle"], dict)
        self.assertIsInstance(item["driver"], dict)
        self.assertIn("issue_reports/first-problem.jpg", item["preview_photo"])

    def test_repair_detail_returns_open_day_problems_in_ascending_order(self):
        report = IssueReport.objects.create(
            driver=self.driver,
            vehicle=self.vehicle,
            description="legacy",
            status="pending",
            last_problem_at=timezone.now(),
        )

        local_now = timezone.localtime(timezone.now())
        open_day_start = (local_now - timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
        later_same_day = open_day_start + timedelta(hours=3)
        next_day = open_day_start + timedelta(days=1, hours=1)
        IssueReport.objects.filter(id=report.id).update(created_at=open_day_start)

        second = IssueReportProblem.objects.create(report=report, description="вторая")
        first = IssueReportProblem.objects.create(report=report, description="первая")
        out_of_day = IssueReportProblem.objects.create(report=report, description="вне дня")

        IssueReportProblem.objects.filter(id=first.id).update(created_at=open_day_start + timedelta(hours=1))
        IssueReportProblem.objects.filter(id=second.id).update(created_at=later_same_day)
        IssueReportProblem.objects.filter(id=out_of_day.id).update(created_at=next_day)

        IssueReportPhoto.objects.create(report=report, problem=first, photo="issue_reports/first.jpg")
        IssueReportPhoto.objects.create(report=report, problem=second, photo="issue_reports/second.jpg")
        IssueReportPhoto.objects.create(report=report, problem=out_of_day, photo="issue_reports/third.jpg")

        response = self.client.get(f"/api/maintenance/issues/{report.id}/")
        self.assertEqual(response.status_code, 200)

        problems = response.data["problems"]
        self.assertEqual([item["description"] for item in problems], ["первая", "вторая"])
        self.assertEqual(response.data["description"], "первая")
        self.assertEqual(len(problems), 2)
        self.assertIn("issue_reports/first.jpg", response.data["photos"][0])
        self.assertTrue(response.data["problems"][0]["sent_at"] <= response.data["problems"][1]["sent_at"])

    def test_repair_detail_legacy_report_without_problems(self):
        report = IssueReport.objects.create(
            driver=self.driver,
            vehicle=self.vehicle,
            description="Только legacy описание",
            status="resolved",
            last_problem_at=timezone.now(),
        )
        IssueReportPhoto.objects.create(
            report=report,
            photo="issue_reports/legacy.jpg",
        )

        response = self.client.get(f"/api/maintenance/issues/{report.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["description"], "Только legacy описание")
        self.assertEqual(response.data["problems"], [])
        self.assertIn("issue_reports/legacy.jpg", response.data["photos"][0])
