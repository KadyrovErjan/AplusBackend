from rest_framework import serializers
from fuels.models import FuelReport, FuelEvent, FuelConfirmation, FuelZone
from fuels.utils.geo import find_zone_for_point
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()

class FuelZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = FuelZone
        fields = ['id', 'external_id', 'name', 'zone_type', 'center_lat', 'center_lon', 'radius_m', 'polygon_coords']


class FuelReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = FuelReport
        fields = ("id", "liters", "liters_from_driver", "photo", "photo_file_id", "lat", "lon", "address", "created_at",)
        read_only_fields = ("id", "created_at")

    def create(self, validated_data):
        driver = self.context["driver"]
        vehicle = self.context["vehicle"]

        photo = validated_data.get("photo")
        photo_file_id = validated_data.get("photo_file_id")
        liters_from_driver = validated_data.get("liters_from_driver")
        liters = validated_data.get("liters") or liters_from_driver 
        lat = validated_data.get("lat")
        lon = validated_data.get("lon")
        address = validated_data.get("address")

        if liters is None:
            raise serializers.ValidationError("Поле liters или liters_from_driver обязателено.")

        zone = None
        zone_name = None
        if lat is not None and lon is not None:
            zone = find_zone_for_point(lat, lon)
            if zone:
                zone_name = zone.name

        return FuelReport.objects.create(
            driver=driver,
            vehicle=vehicle,
            liters=liters,
            liters_from_driver=liters_from_driver,
            photo=photo,
            photo_file_id=photo_file_id,
            lat=lat,
            lon=lon,
            address=address,
            source=FuelReport.Source.MANUAL,
            confirmed=True,
            zone_id=zone.id if zone else None,
            zone_name=zone_name or "Вне объекта",
        )


class FuelEventDetailSerializer(serializers.ModelSerializer):
    driver_id = serializers.SerializerMethodField()
    driver_name = serializers.SerializerMethodField()
    liters_from_driver = serializers.SerializerMethodField()
    photo = serializers.SerializerMethodField()
    photos = serializers.SerializerMethodField()
    photo_file_id = serializers.SerializerMethodField()

    class Meta:
        model = FuelEvent
        fields = ("id", "liters_from_ink", "liters_from_driver", "lat", "lon", "address", "photo", "photos", "photo_file_id",
                  "driver_id", "driver_name", "created_at", )

    def get_driver_id(self, obj):
        return obj.confirmation.driver.id if hasattr(obj, "confirmation") else None

    def get_driver_name(self, obj):
        if hasattr(obj, "confirmation"):
            u = obj.confirmation.driver
            return f"{u.first_name} {u.surname}".strip()
        return None

    def get_liters_from_driver(self, obj):
        return obj.confirmation.liters_from_driver if hasattr(obj, "confirmation") else None

    def get_photo(self, obj):
        if not hasattr(obj, "confirmation"):
            return None

        conf = obj.confirmation
        photo_obj = conf.photos.first() if hasattr(conf, "photos") else None
        request = self.context.get("request")
        if photo_obj and photo_obj.photo and request:
            return request.build_absolute_uri(photo_obj.photo.url)
        if conf.photo and request:
            return request.build_absolute_uri(conf.photo.url)
        return None

    def get_photos(self, obj):
        if not hasattr(obj, "confirmation"):
            return []
        request = self.context.get("request")
        conf = obj.confirmation
        urls = []
        if hasattr(conf, "photos"):
            urls = [request.build_absolute_uri(p.photo.url) for p in conf.photos.all() if p.photo and request]
        if not urls and conf.photo and request:
            urls = [request.build_absolute_uri(conf.photo.url)]
        return urls

    def get_photo_file_id(self, obj):
        if not hasattr(obj, "confirmation"):
            return None
        conf = obj.confirmation
        photo_obj = conf.photos.first() if hasattr(conf, "photos") else None
        if photo_obj and photo_obj.photo_file_id:
            return photo_obj.photo_file_id
        return conf.photo_file_id



class FuelConfirmationShortSerializer(serializers.ModelSerializer):
    photo = serializers.SerializerMethodField()

    class Meta:
        model = FuelConfirmation
        fields = ("driver_id", "liters_from_driver", "photo", "photo_file_id", "created_at")

    driver_id = serializers.SerializerMethodField()

    def get_driver_id(self, obj):
        return obj.driver.id

    def get_photo(self, obj):
        request = self.context.get("request")
        if obj.photo and request:
            return request.build_absolute_uri(obj.photo.url)
        return None

class FuelEventSerializer(serializers.ModelSerializer):
    driver_id = serializers.SerializerMethodField()
    driver_name = serializers.SerializerMethodField()
    photo = serializers.SerializerMethodField()
    photos = serializers.SerializerMethodField()
    photo_file_id = serializers.SerializerMethodField()
    created_at = serializers.SerializerMethodField()

    class Meta:
        model = FuelEvent
        fields = ("id", "liters_from_ink", "lat", "lon", "address", "zone_id", "zone_name", "driver_id",
                  "driver_name", "photo", "photos", "photo_file_id", "created_at", )

    def get_driver_id(self, obj):
        if hasattr(obj, "confirmation") and obj.confirmation:
            return obj.confirmation.driver.id
        return None

    def get_driver_name(self, obj):
        if hasattr(obj, "confirmation") and obj.confirmation:
            u = obj.confirmation.driver
            return f"{u.first_name or ''} {u.surname or ''}".strip() or None
        return None

    def get_photo(self, obj):
        if not hasattr(obj, "confirmation") or not obj.confirmation:
            return None
        conf = obj.confirmation
        request = self.context.get("request")
        photo_obj = conf.photos.first() if hasattr(conf, "photos") else None
        if photo_obj and photo_obj.photo and request:
            return request.build_absolute_uri(photo_obj.photo.url)
        if conf.photo and request:
            return request.build_absolute_uri(conf.photo.url)
        return None

    def get_photos(self, obj):
        if not hasattr(obj, "confirmation") or not obj.confirmation:
            return []
        request = self.context.get("request")
        conf = obj.confirmation
        urls = []
        if hasattr(conf, "photos"):
            urls = [request.build_absolute_uri(p.photo.url) for p in conf.photos.all() if p.photo and request]
        if not urls and conf.photo and request:
            urls = [request.build_absolute_uri(conf.photo.url)]
        return urls

    def get_photo_file_id(self, obj):
        if not hasattr(obj, "confirmation") or not obj.confirmation:
            return None
        conf = obj.confirmation
        photo_obj = conf.photos.first() if hasattr(conf, "photos") else None
        if photo_obj and photo_obj.photo_file_id:
            return photo_obj.photo_file_id
        return conf.photo_file_id

    def get_created_at(self, obj):
        if not obj.created_at:
            return None
        return timezone.localtime(obj.created_at).strftime("%d-%m-%Y %H:%M")
