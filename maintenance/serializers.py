from django.utils import timezone
from rest_framework import serializers

from common.serializers import build_file_url
from vehicles.serializers import FrontendVehicleSerializer

from .models import IssueReport, IssueReportPhoto, IssueReportProblem


class IssueReportPhotoSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = IssueReportPhoto
        fields = ["url"]

    def get_url(self, obj):
        return build_file_url(self.context.get("request"), obj.photo)


class IssueReportProblemSerializer(serializers.ModelSerializer):
    sent_at = serializers.SerializerMethodField()
    photos = serializers.SerializerMethodField()

    class Meta:
        model = IssueReportProblem
        fields = ["description", "sent_at", "photos"]

    def get_sent_at(self, obj):
        return timezone.localtime(obj.created_at).strftime("%d-%m-%Y %H:%M")

    def get_photos(self, obj):
        request = self.context.get("request")
        return [build_file_url(request, photo.photo) for photo in obj.photos.all()]


class IssueReportCardSerializer(serializers.ModelSerializer):
    status = serializers.CharField()
    preview_photo = serializers.SerializerMethodField()
    vehicle = serializers.SerializerMethodField()
    driver = serializers.SerializerMethodField()

    class Meta:
        model = IssueReport
        fields = (
            "id",
            "status",
            "preview_photo",
            "vehicle",
            "driver",
        )

    def get_preview_photo(self, obj):
        request = self.context.get("request")

        problems = list(obj.problems.all())
        first_problem = problems[0] if problems else None
        if first_problem:
            first_problem_photo = first_problem.photos.first()
            if first_problem_photo:
                return build_file_url(request, first_problem_photo.photo)

        first_photo = obj.photos.first()
        if first_photo:
            return build_file_url(request, first_photo.photo)
        return None

    def get_vehicle(self, obj):
        if not obj.vehicle:
            return None
        frontend = FrontendVehicleSerializer.from_vehicle_obj(obj.vehicle)
        return {
            "name": frontend.get("name"),
            "number": frontend.get("number"),
        }

    def get_driver(self, obj):
        if not obj.driver:
            return None
        return {"full_name": f"{obj.driver.first_name} {obj.driver.surname}".strip()}


class IssueReportDetailSerializer(serializers.ModelSerializer):
    created_at = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()
    photos = serializers.SerializerMethodField()
    vehicle = serializers.SerializerMethodField()
    driver = serializers.SerializerMethodField()
    problems = serializers.SerializerMethodField()

    class Meta:
        model = IssueReport
        fields = (
            "id",
            "created_at",
            "status",
            "description",
            "photos",
            "vehicle",
            "driver",
            "problems",
        )

    def get_created_at(self, obj):
        return timezone.localtime(obj.created_at).strftime("%d-%m-%Y %H:%M")

    def _get_open_day_problems(self, obj):
        cache_attr = "_open_day_problems_cache"
        if hasattr(obj, cache_attr):
            return getattr(obj, cache_attr)

        open_day = timezone.localdate(obj.created_at)
        selected = [
            problem for problem in obj.problems.all()
            if timezone.localdate(problem.created_at) == open_day
        ]
        selected.sort(key=lambda problem: problem.created_at)
        setattr(obj, cache_attr, selected)
        return selected

    def get_photos(self, obj):
        request = self.context.get("request")
        problems = self._get_open_day_problems(obj)
        if problems:
            first_problem = problems[0]
            return [build_file_url(request, photo.photo) for photo in first_problem.photos.all()]

        return [build_file_url(request, photo.photo) for photo in obj.photos.all()]

    def get_vehicle(self, obj):
        if not obj.vehicle:
            return None
        frontend = FrontendVehicleSerializer.from_vehicle_obj(obj.vehicle)
        return {
            "id": obj.vehicle.veh_id,
            "name": frontend.get("name"),
            "plate_number": frontend.get("number"),
        }

    def get_driver(self, obj):
        user = obj.driver
        if not user:
            return None
        return {
            "id": user.id,
            "full_name": f"{user.first_name} {user.surname}".strip(),
            "phone": user.phone,
            "telegram_link": f"https://t.me/{user.telegram_username}" if user.telegram_username else None,
        }

    def get_description(self, obj):
        problems = self._get_open_day_problems(obj)
        if problems:
            return problems[0].description
        return obj.description

    def get_problems(self, obj):
        problems = self._get_open_day_problems(obj)
        serializer = IssueReportProblemSerializer(
            problems,
            many=True,
            context=self.context,
        )
        return serializer.data
