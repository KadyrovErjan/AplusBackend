from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Prefetch
from django.db.utils import IntegrityError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework import viewsets
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from services.notification_service import send_notification
from vehicles.models import VehicleAssignment

from .models import IssueReport, IssueReportPhoto, IssueReportProblem
from .serializers import IssueReportCardSerializer, IssueReportDetailSerializer

User = get_user_model()


class IssueReportViewSet(viewsets.GenericViewSet):
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    lookup_field = "id"
    lookup_url_kwarg = "id"

    def get_queryset(self):
        return (
            IssueReport.objects
            .select_related("vehicle", "driver")
            .prefetch_related(
                Prefetch(
                    "problems",
                    queryset=IssueReportProblem.objects.order_by("created_at").prefetch_related("photos"),
                ),
                "photos",
            )
            .all()
        )

    @swagger_auto_schema(tags=["🛠 Repairs"], operation_summary="Сообщить о поломке (Бот)")
    def create(self, request):
        telegram_id = request.data.get("telegram_id")
        description = request.data.get("description", "").strip()
        photos = request.FILES.getlist("photos")

        if not telegram_id or not description:
            return Response({"error": "required_fields"}, status=400)

        driver = get_object_or_404(User, telegram_id=telegram_id, role="driver")

        assignment = (
            VehicleAssignment.objects
            .filter(driver=driver, unassigned_at__isnull=True)
            .select_related("vehicle")
            .first()
        )
        if not assignment or not assignment.vehicle:
            return Response({"error": "driver_has_no_vehicle"}, status=400)

        vehicle = assignment.vehicle
        created_new = False
        active_statuses = ["pending", "in_progress"]

        with transaction.atomic():
            report = (
                IssueReport.objects
                .select_for_update()
                .filter(driver=driver, vehicle=vehicle, status__in=active_statuses)
                .order_by("created_at", "id")
                .first()
            )

            if not report:
                try:
                    report = IssueReport.objects.create(
                        driver=driver,
                        vehicle=vehicle,
                        description=description,
                        status="pending",
                        last_problem_at=timezone.now(),
                    )
                    created_new = True
                except IntegrityError:
                    report = (
                        IssueReport.objects
                        .select_for_update()
                        .filter(driver=driver, vehicle=vehicle, status__in=active_statuses)
                        .order_by("created_at", "id")
                        .first()
                    )
                    if not report:
                        raise

            problem = IssueReportProblem.objects.create(
                report=report,
                description=description,
            )

            IssueReport.objects.filter(id=report.id).update(
                description=description,
                last_problem_at=problem.created_at,
            )

            for photo in photos:
                IssueReportPhoto.objects.create(
                    report=report,
                    problem=problem,
                    photo=photo,
                )

        notification_title = "Требуется ремонт" if created_new else "Новая проблема по ремонту"
        notification_message = (
            f"{driver.first_name} сообщил о поломке: {description}"
            if created_new
            else f"{driver.first_name} добавил новую проблему: {description}"
        )
        send_notification(
            type_code="service",
            title=notification_title,
            message=notification_message,
            vehicle=vehicle,
        )

        return Response(
            {
                "status": "ok",
                "report_id": report.id,
                "created_new": created_new,
                "problem_id": problem.id,
            }
        )

    @swagger_auto_schema(tags=["🛠 Repairs"], operation_summary="Список поломок")
    def list(self, request):
        queryset = self.get_queryset().order_by("-last_problem_at", "-created_at")
        status_param = request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)

        serializer = IssueReportCardSerializer(
            queryset,
            many=True,
            context={"request": request},
        )
        return Response(serializer.data)

    @swagger_auto_schema(tags=["🛠 Repairs"], operation_summary="Детальная страница поломки")
    def retrieve(self, request, id=None):
        report = get_object_or_404(self.get_queryset(), id=id)
        serializer = IssueReportDetailSerializer(
            report,
            context={"request": request},
        )
        return Response(serializer.data)
