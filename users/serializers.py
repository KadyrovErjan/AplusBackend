from rest_framework import serializers
from django.contrib.auth import authenticate, get_user_model
from rest_framework_simplejwt.tokens import RefreshToken
from vehicles.models import VehicleRequest
from vehicles.models import Vehicle, VehicleAssignment
from vehicles.serializers import FrontendVehicleSerializer 
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from common.serializers import build_file_url
from fuels.models import FuelReport, FuelEvent, FuelConfirmation, FuelReportPhoto

User = get_user_model()

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id", "username", "first_name", "surname", "phone", "telegram_id",
            "role", "status", "language", "created_at"
        ]


class DriverListSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    current_vehicle = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "telegram_id", "first_name", "surname", "full_name", "phone", "status", "current_vehicle"]

    def get_full_name(self, obj):
        return f"{obj.first_name or ''} {obj.surname or ''}".strip()

    def get_current_vehicle(self, obj):
        assign = VehicleAssignment.objects.filter(driver=obj, unassigned_at__isnull=True).select_related("vehicle").first()
        if not assign:
            return None
        v = assign.vehicle
        frontend = FrontendVehicleSerializer.from_vehicle_obj(v)
        return {
            "veh_id": v.veh_id,
            "name": frontend.get("name"),
            "plate_number": frontend.get("number"),
        }


class VehicleRequestListSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = VehicleRequest
        fields = ["id", "user", "vehicle_id", "status", "created_at"]


class RegisterSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(required=True)
    password = serializers.CharField(write_only=True)
    role = serializers.ChoiceField(choices=User.ROLE_CHOICES, default="driver")

    class Meta:
        model = User
        fields = ["email", "password", "role"]

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Пользователь с таким email уже существует.")
        return value

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as err:
            raise serializers.ValidationError(err.messages)
        return value

    def create(self, validated_data):
        email = validated_data["email"]
        password = validated_data["password"]
        role = validated_data["role"]

        base = email.split("@")[0].replace(".", "_").replace("-", "_")
        username = f"user_{base}"

        i = 1
        original = username
        while User.objects.filter(username=username).exists():
            username = f"{original}_{i}"
            i += 1

        user = User(
            username=username,
            email=email,
            role=role,
        )
        user.set_password(password)
        user.save()

        return user


class EmailLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        email = data["email"].lower()
        password = data["password"]

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise serializers.ValidationError("Неверный email или пароль.")

        if not user.check_password(password):
            raise serializers.ValidationError("Неверный email или пароль.")

        return {"user": user}

    def to_representation(self, validated_data):
        user = validated_data["user"]
        refresh = RefreshToken.for_user(user)

        return {
            "user": {
                "id": user.id,
                "email": user.email,
                "role": user.role,
            },
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        }


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()

    def validate(self, attrs):
        self.token = attrs["refresh"]
        return attrs

    def save(self, **kwargs):
        try:
            token = RefreshToken(self.token)
            token.blacklist()
        except Exception:
            raise serializers.ValidationError(
                {"detail": "Недействительный или уже отозванный токен"}
            )


class FullUserRegisterSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = "__all__"


class VehicleRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = VehicleRequest
        fields = ["user", "vehicle_id", "status", "created_at"]


class ApproveRequestSerializer(serializers.Serializer):
    request_id = serializers.IntegerField()

class RejectVehicleRequestSerializer(serializers.Serializer):
    request_id = serializers.IntegerField()



def format_shift(shift):
    start = timezone.localtime(shift.started_at)
    end = timezone.localtime(shift.ended_at) if shift.ended_at else None
    now = timezone.localtime(timezone.now())

    def d(dt):
        return dt.strftime("%d-%m-%Y")

    def t(dt):
        return dt.strftime("%H:%M")

    if end is None:
        if start.date() == now.date():
            return f"на смене · сегодня с {t(start)}"
        else:
            return f"на смене · с {d(start)} {t(start)}"

    if start.date() == end.date():
        if start.date() == now.date():
            return f"смена закончилась: {t(start)} – {t(end)}"
        else:
            return f"смена закончилась: {d(start)} {t(start)} – {t(end)}"

    return f"смена закончилась: {d(start)} {t(start)} – {d(end)} {t(end)}"


class DriverCardSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.SerializerMethodField()
    status = serializers.CharField()
    phone = serializers.CharField()
    telegram = serializers.SerializerMethodField()
    avatar = serializers.SerializerMethodField()
    vehicle = serializers.SerializerMethodField()
    shift = serializers.SerializerMethodField()

    def get_avatar(self, obj):
        request = self.context.get("request")
        return build_file_url(request, obj.selfie)

    def get_shift(self, obj):
        active_shift = (
            obj.shifts
            .filter(ended_at__isnull=True)
            .order_by("-started_at")
            .first()
        )

        if active_shift:
            return {
                "active": True,
                "label": format_shift(active_shift)
            }

        last_shift = (
            obj.shifts
            .filter(ended_at__isnull=False)
            .order_by("-ended_at")
            .first()
        )

        if last_shift:
            return {
                "active": False,
                "label": format_shift(last_shift)
            }

        return {
            "active": False,
            "label": "не на смене"
        }

    def get_telegram(self, obj):
        if not obj.telegram_username:
            return None

        username = obj.telegram_username.lstrip("@")

        return {
            "tgName": f"@{username}",
            "tgLink": f"https://t.me/{username}",
        }

    def get_name(self, obj):
        return f"{obj.first_name} {obj.surname}".strip()

    def get_vehicle(self, obj):
        assign = (
            VehicleAssignment.objects
            .filter(driver=obj, unassigned_at__isnull=True)
            .select_related("vehicle")
            .first()
        )
        if not assign:
            return None

        v = assign.vehicle
        frontend = FrontendVehicleSerializer.from_vehicle_obj(v)
        return {
            "id": v.veh_id,
            "name": frontend.get("name"),
            "number": frontend.get("number")
        }



class DriverDetailSerializer(serializers.ModelSerializer):
    birthDate = serializers.DateField(source="birthdate", read_only=True)
    passportID = serializers.CharField(source="passport_id", read_only=True)
    telegram_Id = serializers.CharField(source="telegram_id", read_only=True)

    name = serializers.SerializerMethodField()
    photoSelfie = serializers.SerializerMethodField()
    frontpassportPhoto = serializers.SerializerMethodField()
    backpassportPhoto = serializers.SerializerMethodField()
    driver_license_front = serializers.SerializerMethodField()
    driver_license_back = serializers.SerializerMethodField()
    hireDate = serializers.SerializerMethodField()
    currentVehicle = serializers.SerializerMethodField()
    numberVehicle = serializers.SerializerMethodField()
    vehicle_Id = serializers.SerializerMethodField()
    telegram = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'name', 'phone', 'telegram', 'telegram_Id', 'birthDate', 'passportID', 'iin',  'address',
                  'photoSelfie', 'frontpassportPhoto', 'backpassportPhoto', 'driver_license_front',
                  'driver_license_back', 'status', 'hireDate', 'currentVehicle', 'numberVehicle', 'vehicle_Id' ]

    def get_photoSelfie(self, obj):
        return build_file_url(self.context.get("request"), obj.selfie)

    def get_frontpassportPhoto(self, obj):
        return build_file_url(self.context.get("request"), obj.passport_front)

    def get_backpassportPhoto(self, obj):
        return build_file_url(self.context.get("request"), obj.passport_back)

    def get_driver_license_front(self, obj):
        return build_file_url(self.context.get("request"), obj.driver_license_front)

    def get_driver_license_back(self, obj):
        return build_file_url(self.context.get("request"), obj.driver_license_back)

    def get_hireDate(self, obj):
        if obj.created_at:
            return obj.created_at.date()
        return None

    def get_name(self, obj):
        return f"{obj.first_name} {obj.surname}".strip()

    def _assignment(self, obj):
        return (
            VehicleAssignment.objects
            .filter(driver=obj, unassigned_at__isnull=True)
            .select_related("vehicle")
            .first()
        )

    def get_telegram(self, obj):
        if obj.telegram_username:
            return f"https://t.me/{obj.telegram_username}"
        if obj.telegram_id:
            return f"https://t.me/user?id={obj.telegram_id}"
        return None

    def get_currentVehicle(self, obj):
        a = self._assignment(obj)
        if not a: return None
        return FrontendVehicleSerializer.from_vehicle_obj(a.vehicle).get("name")

    def get_numberVehicle(self, obj):
        a = self._assignment(obj)
        if not a: return None
        return FrontendVehicleSerializer.from_vehicle_obj(a.vehicle).get("number")

    def get_vehicle_Id(self, obj):
        a = self._assignment(obj)
        return a.vehicle.id if a else None


class PendingRequestCardSerializer(serializers.ModelSerializer):
    request_id = serializers.IntegerField(source="id")

    driver = serializers.SerializerMethodField()
    vehicle = serializers.SerializerMethodField()
    telegram = serializers.SerializerMethodField()
    status = serializers.CharField()
    selfie = serializers.SerializerMethodField()
    passport_front = serializers.SerializerMethodField()
    passport_back = serializers.SerializerMethodField()
    driver_license_front = serializers.SerializerMethodField()
    driver_license_back = serializers.SerializerMethodField()

    class Meta:
        model = VehicleRequest
        fields = ["request_id", "driver", "vehicle", "telegram", "status", "created_at", "selfie",
                  "passport_front", "passport_back", "driver_license_front", "driver_license_back"]

    def get_driver(self, obj):
        u = obj.user
        return {
            "id": u.id,
            "full_name": f"{u.first_name} {u.surname}".strip(),
            "phone": u.phone,
        }


    def get_vehicle(self, obj):
        try:
            v = Vehicle.objects.get(veh_id=obj.vehicle_id)
        except Vehicle.DoesNotExist:
            return None

        frontend = FrontendVehicleSerializer.from_vehicle_obj(v)

        return {
            "id": v.veh_id,
            "name": frontend["name"],
            "number": frontend["number"],
            "type": v.type,
        }

    def get_telegram(self, obj):
        u = obj.user

        if u.telegram_username:
            username = u.telegram_username.lstrip("@")
            return {
                "tgName": f"@{username}",
                "tgLink": f"https://t.me/{username}",
            }

        if u.telegram_id:
            return {
                "tgName": f"id:{u.telegram_id}",
                "tgLink": f"https://t.me/user?id={u.telegram_id}",
            }

        return None

    def get_selfie(self, obj):
        request = self.context.get("request")
        return build_file_url(request, obj.user.selfie)

    def get_passport_front(self, obj):
        request = self.context.get("request")
        return build_file_url(request, obj.user.passport_front)

    def get_passport_back(self, obj):
        request = self.context.get("request")
        return build_file_url(request, obj.user.passport_back)

    def get_driver_license_front(self, obj):
        request = self.context.get("request")
        return build_file_url(request, obj.user.driver_license_front)

    def get_driver_license_back(self, obj):
        request = self.context.get("request")
        return build_file_url(request, obj.user.driver_license_back)


class PendingRequestDetailSerializer(serializers.ModelSerializer):
    request_id = serializers.IntegerField(source="id")

    driver = serializers.SerializerMethodField()
    vehicle = serializers.SerializerMethodField()
    telegram = serializers.SerializerMethodField()
    status = serializers.CharField()

    class Meta:
        model = VehicleRequest
        fields = ["request_id", "driver", "vehicle", "telegram", "status", "created_at",]

    def get_driver(self, obj):
        u = obj.user
        request = self.context.get("request")

        return {
            "id": u.id,
            "full_name": f"{u.first_name} {u.surname}".strip(),
            "phone": u.phone,
            "birthdate": u.birthdate,
            "passport_id": u.passport_id,
            "iin": u.iin,
            "address": u.address,
            "selfie": build_file_url(request, u.selfie),
            "passport_front": build_file_url(request, u.passport_front),
            "passport_back": build_file_url(request, u.passport_back),
            "driver_license_front": build_file_url(request, u.driver_license_front),
            "driver_license_back": build_file_url(request, u.driver_license_back),
        }

    def get_vehicle(self, obj):
        try:
            v = Vehicle.objects.get(veh_id=obj.vehicle_id)
        except Vehicle.DoesNotExist:
            return None

        frontend = FrontendVehicleSerializer.from_vehicle_obj(v)
        return {
            "id": v.veh_id,
            "name": frontend["name"],
            "number": frontend["number"],
            "type": v.type,
        }

    def get_telegram(self, obj):
        u = obj.user
        if u.telegram_username:
            return {
                "tgName": f"@{u.telegram_username}",
                "tgLink": f"https://t.me/{u.telegram_username}",
            }
        if u.telegram_id:
            return {
                "tgName": f"@id{u.telegram_id}",
                "tgLink": f"https://t.me/user?id={u.telegram_id}",
            }
        return None


class DriverFuelReportSerializer(serializers.ModelSerializer):
    liters = serializers.FloatField(write_only=True)
    photos = serializers.ListField(
        child=serializers.ImageField(),
        write_only=True,
        required=False,
    )
    photo_file_ids = serializers.ListField(
        child=serializers.CharField(),
        write_only=True,
        required=False,
    )

    class Meta:
        model = FuelReport
        fields = (
            "id",
            "liters",             
            "photo",
            "photo_file_id",
            "photos",
            "photo_file_ids",
            "created_at",
        )
        read_only_fields = ("id", "created_at")

    def create(self, validated_data):
        driver = self.context["driver"]
        vehicle = self.context["vehicle"]

        liters = validated_data.pop("liters")

        photos = validated_data.pop("photos", []) or []
        photo_file_ids = validated_data.pop("photo_file_ids", []) or []

        photo = validated_data.get("photo")
        photo_file_id = validated_data.get("photo_file_id")

        if photo and not photos:
            photos = [photo]
        if photo_file_id and not photo_file_ids:
            photo_file_ids = [photo_file_id]

        primary_photo = photos[0] if photos else photo
        primary_photo_file_id = photo_file_ids[0] if photo_file_ids else photo_file_id

        report = FuelReport.objects.create(
            driver=driver,
            vehicle=vehicle,

            liters=liters,
            liters_from_driver=liters,

            photo=primary_photo,
            photo_file_id=primary_photo_file_id,

            source=FuelReport.Source.MANUAL,
            confirmed=True,
        )

        photo_rows = []
        for idx, item_photo in enumerate(photos):
            item_file_id = photo_file_ids[idx] if idx < len(photo_file_ids) else None
            photo_rows.append(
                FuelReportPhoto(
                    report=report,
                    photo=item_photo,
                    photo_file_id=item_file_id,
                )
            )
        if photo_rows:
            FuelReportPhoto.objects.bulk_create(photo_rows)

        return report


class FuelConfirmationSerializer(serializers.Serializer):
    liters_from_driver = serializers.FloatField()
    photo = serializers.ImageField(required=False)
    photo_file_id = serializers.CharField(required=False)

    def create(self, validated_data):
        driver = self.context["driver"]
        vehicle = self.context["vehicle"]

        from django.utils.timezone import now
        from datetime import timedelta

        ts_from = now() - timedelta(minutes=10)
        ts_to = now() + timedelta(minutes=10)

        fuel_event = (
            FuelEvent.objects
            .filter(
                vehicle=vehicle,
                created_at__range=(ts_from, ts_to)
            )
            .exclude(confirmation__isnull=False)
            .order_by("created_at")
            .first()
        )

        if not fuel_event:
            raise serializers.ValidationError(
                "Не найдена заправка для подтверждения"
            )

        return FuelConfirmation.objects.create(
            fuel_event=fuel_event,
            driver=driver,
            liters_from_driver=validated_data["liters_from_driver"],
            photo=validated_data.get("photo"),
            photo_file_id=validated_data.get("photo_file_id"),
        )