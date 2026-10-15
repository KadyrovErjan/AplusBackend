from rest_framework import status, viewsets
import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from django.utils import timezone
from rest_framework.parsers import JSONParser, FormParser, MultiPartParser
from .models import CustomUser
from vehicles.models import (
    VehicleRequest,
    DriverShift,
)
from fuels.models import FuelReport
from .serializers import (
    RegisterSerializer,
    EmailLoginSerializer,
    LogoutSerializer,
    UserSerializer,
    FullUserRegisterSerializer,
    DriverCardSerializer,
    DriverDetailSerializer,
    PendingRequestCardSerializer,
    RejectVehicleRequestSerializer,
    ApproveRequestSerializer,
    PendingRequestDetailSerializer,
    DriverFuelReportSerializer
)
from vehicles.models import Vehicle, VehicleAssignment
from django.db.models import Q
from django.db import transaction, connection
from django.db.utils import IntegrityError
from rest_framework.permissions import AllowAny
from django.shortcuts import get_object_or_404
from notifications.models import Notification
from django.contrib.auth import get_user_model
from django.conf import settings
from fuels.services.confirmations import confirm_by_driver
from services.notification_service import send_notification
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.views import TokenRefreshView
from rest_framework_simplejwt.exceptions import InvalidToken
from trips.tasks import sync_single_vehicle_task
from .telegram_auth_service import TelegramAuthService

User = get_user_model()
logger = logging.getLogger(__name__)

LEGACY_FUELREPORT_TABLE = "users_fuelreport"

def _null_driver_in_legacy_fuel_reports(driver_id: int) -> None:
    if FuelReport._meta.db_table == LEGACY_FUELREPORT_TABLE:
        return

    if LEGACY_FUELREPORT_TABLE not in connection.introspection.table_names():
        return

    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {LEGACY_FUELREPORT_TABLE} SET driver_id = NULL WHERE driver_id = %s",
            [driver_id],
        )


def _normalize_note(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:1000]




class CookieTokenRefreshView(TokenRefreshView):
    """
    Кастомный рефреш: берет refresh_token из куки,
    валидирует и отдает новый access_token тоже в куку.
    """

    def post(self, request, *args, **kwargs):
        refresh_token = request.COOKIES.get('refresh_token')

        if not refresh_token:
            return Response({"detail": "No refresh token"}, status=status.HTTP_401_UNAUTHORIZED)

        request.data['refresh'] = refresh_token

        try:
            response = super().post(request, *args, **kwargs)
        except InvalidToken as e:
            return Response({"detail": str(e)}, status=status.HTTP_401_UNAUTHORIZED)

        new_access_token = response.data.get('access')

        del response.data['access']
        if 'refresh' in response.data:
            del response.data['refresh'] 

        response.set_cookie(
            key='access_token',
            value=new_access_token,
            httponly=True,
            secure=not settings.DEBUG,
            samesite='Lax',
            max_age=30 * 60
        )

        return response


class CustomRegisterView(APIView):
    """
    Регистрация пользователя (НЕ Telegram).
    """
    @swagger_auto_schema(
        tags=["🔐 Authentication"],
        operation_summary="Регистрация пользователя (НЕ Telegram)",
        operation_description="""
Эндпоинт для обычной регистрации пользователя (не Telegram).

Фронт отправляет:
- email
- password
- role (driver / mechanic / admin)

Что делает бэкенд:
1. Приводит email → lowercase
2. Проверяет уникальность email
3. Проверяет корректность пароля через Django validators
4. Создаёт пользователя через ModelSerializer
5. Сразу выдаёт JWT-токены (автоматический логин)

Возвращает:
- id
- username (генерируется автоматически)
- email
- role
- access token
- refresh token
        """,
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                "email": openapi.Schema(type=openapi.TYPE_STRING, example="driver@example.com"),
                "password": openapi.Schema(type=openapi.TYPE_STRING, example="Qwerty123!"),
                "role": openapi.Schema(type=openapi.TYPE_STRING, example="driver"),
            },
            required=["email", "password"],
        ),
        responses={
            201: openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "user": openapi.Schema(
                        type=openapi.TYPE_OBJECT,
                        properties={
                            "id": openapi.Schema(type=openapi.TYPE_INTEGER),
                            "username": openapi.Schema(type=openapi.TYPE_STRING),
                            "email": openapi.Schema(type=openapi.TYPE_STRING),
                            "role": openapi.Schema(type=openapi.TYPE_STRING),
                        }
                    ),
                    "access": openapi.Schema(type=openapi.TYPE_STRING),
                    "refresh": openapi.Schema(type=openapi.TYPE_STRING),
                }
            ),
            400: "Ошибка валидации",
        }
    )
    def post(self, request):
        data = request.data.copy()
        email = data.get("email")

        if email:
            data["email"] = email.strip().lower()

        serializer = RegisterSerializer(data=data)

        if not serializer.is_valid():
            return Response(
                {"errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = serializer.save()

        refresh = RefreshToken.for_user(user)
        access = refresh.access_token

        response = Response(
            {
                "success": True,
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "role": user.role,
                }
            },
            status=status.HTTP_201_CREATED,
        )

        response.set_cookie(
            key='access_token',
            value=str(access),
            httponly=True,
            secure=not settings.DEBUG,
            samesite='Lax',
            max_age=30 * 60
        )

        response.set_cookie(
            key='refresh_token',
            value=str(refresh),
            httponly=True,
            secure=not settings.DEBUG,
            samesite='Lax',
            path='/api/users/login/refresh/',
            max_age=7 * 24 * 60 * 60
        )

        return response



class CustomLoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        tags=["🔐 Authentication"],
        operation_summary="Авторизация по email",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                "email": openapi.Schema(type=openapi.TYPE_STRING),
                "password": openapi.Schema(type=openapi.TYPE_STRING),
            },
            required=["email", "password"]
        ),
    )
    def post(self, request):
        serializer = EmailLoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        user = serializer.validated_data["user"]

        refresh = RefreshToken.for_user(user)
        access_token = str(refresh.access_token)
        refresh_token = str(refresh)

        user_data = UserSerializer(user).data

        response = Response({
            "success": True,
            "access": access_token,
            "refresh": refresh_token,
            "user": user_data
        })

        response.set_cookie(
            key='access_token',
            value=access_token,
            httponly=True,
            secure=not settings.DEBUG,
            samesite='Lax',
            max_age=30 * 60
        )

        response.set_cookie(
            key='refresh_token',
            value=refresh_token,
            httponly=True,
            secure=not settings.DEBUG,
            samesite='Lax',
            path='/api/users/login/refresh/',
            max_age=7 * 24 * 60 * 60
        )

        return response


class LogoutView(APIView):
    """
    Выход из системы.
    Берет refresh_token из куки, добавляет в черн список и удаляет куки.
    """

    @swagger_auto_schema(
        tags=["🔐 Authentication"],
        operation_summary="Выход (удаление кук)",
    )
    def post(self, request):
        response = Response({"detail": "Вы вышли из системы."}, status=status.HTTP_200_OK)

        refresh_token = request.COOKIES.get('refresh_token')

        if refresh_token:
            try:
                token = RefreshToken(refresh_token)
                token.blacklist()
            except TokenError:
                pass

        response.delete_cookie('access_token')
        response.delete_cookie('refresh_token', path='/api/users/login/refresh/')

        return response


class FullRegisterView(APIView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    @swagger_auto_schema(
        tags=["🔐 Authentication"],
        operation_summary="Полная регистрация водителя (Telegram)",
        operation_description="""
Используется Telegram-ботом.  

Что делает:
1. Создаёт или обновляет пользователя.  
2. Устанавливает статус registration → pending_vehicle.  
3. Создаёт заявку (VehicleRequest).  

После этого админ через адм. панель подтверждает заявку.
        """,
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=[
                "telegram_id", "name", "surname", "birthdate", "phone",
                "passport_id", "iin", "address",
                "passport_front", "passport_back",
                "driver_license_front", "driver_license_back", "selfie",
                "language", "vehicle_id"
            ],
        ),
    )
    def post(self, request):
        data = request.data
        files = request.FILES

        required_text = [
            "telegram_id", "name", "surname", "birthdate",
            "phone", "passport_id", "iin", "address",
            "language", "vehicle_id",
        ]

        missing = [f for f in required_text if f not in data]
        if missing:
            return Response({"error": f"missing fields: {missing}"}, 400)

        required_files = ["passport_front", "passport_back", "selfie"]

        has_new_license = "driver_license_front" in files or "driver_license_front" in data
        has_old_license = "driver_license" in files or "driver_license" in data

        if not has_new_license and not has_old_license:
            return Response({"error": "missing fields: driver_license_front or driver_license"}, 400)

        telegram_id = data["telegram_id"]

        try:
            user = User.objects.get(telegram_id=telegram_id)
            creating = False
        except User.DoesNotExist:
            user = User(telegram_id=telegram_id)
            creating = True

        if creating:
            user.username = f"user_{telegram_id}"
            user.set_password("default_password")

        user.email = f"{telegram_id}@tg.local"
        user.telegram_username = data.get("telegram_username")
        user.first_name = data["name"]
        user.surname = data["surname"]
        user.birthdate = data["birthdate"]
        user.phone = data["phone"]
        user.passport_id = data["passport_id"]
        user.iin = data["iin"]
        user.address = data["address"]

        if "passport_front" in files:
            user.passport_front = files["passport_front"]
        elif "passport_front" in data and isinstance(data["passport_front"], str):
            user.passport_front = data["passport_front"]

        if "passport_back" in files:
            user.passport_back = files["passport_back"]
        elif "passport_back" in data and isinstance(data["passport_back"], str):
            user.passport_back = data["passport_back"]

        if "driver_license_front" in files:
            user.driver_license_front = files["driver_license_front"]
        elif "driver_license_front" in data:
            user.driver_license_front = data["driver_license_front"]
        elif "driver_license" in files:
            user.driver_license_front = files["driver_license"]
            user.driver_license = files["driver_license"]
        elif "driver_license" in data:
            user.driver_license_front = data["driver_license"]
            user.driver_license = data["driver_license"]

        if "driver_license_back" in files:
            user.driver_license_back = files["driver_license_back"]
        elif "driver_license_back" in data:
            user.driver_license_back = data["driver_license_back"]

        if "selfie" in files:
            user.selfie = files["selfie"]
        elif "selfie" in data and isinstance(data["selfie"], str):
            user.selfie = data["selfie"]

        user.language = data["language"]
        user.role = "driver"
        user.status = "pending_vehicle"
        user.save()

        req = VehicleRequest.objects.create(
            user=user,
            vehicle_id=data["vehicle_id"],
            status="pending"
        )
        send_notification(
            type_code="driver_request",
            title="Новая заявка водителя",
            message=f"{user.first_name} {user.surname} подал заявку на регистрацию."
        )
        Admin = get_user_model()
        admins = Admin.objects.filter(role="admin")

        for admin in admins:
            Notification.objects.create(
                user=admin,
                type="driver_request",
                title="Новая заявка водителя",
                message=f"{user.first_name} {user.surname} подал заявку на регистрацию"
            )

        return Response({
            "status": "ok",
            "user_id": user.id,
            "vehicle_request_id": req.id,
        })


class TelegramAdminStartView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        tags=["🔐 Authentication"],
        operation_summary="Привязка Telegram к администратору (/start admin)",
        operation_description="""
Используется Telegram-ботом в сценарии `/start admin`.

Бот отправляет:
- telegram_id
- admin_secret
- telegram_username (необязательно)
        """,
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["telegram_id", "admin_secret"],
            properties={
                "telegram_id": openapi.Schema(type=openapi.TYPE_INTEGER, example=123456789),
                "admin_secret": openapi.Schema(type=openapi.TYPE_STRING, example="super_admin_himeno"),
                "telegram_username": openapi.Schema(type=openapi.TYPE_STRING, example="admin_user"),
            },
        ),
        responses={
            200: openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "status": openapi.Schema(type=openapi.TYPE_STRING),
                    "user_id": openapi.Schema(type=openapi.TYPE_INTEGER),
                    "role": openapi.Schema(type=openapi.TYPE_STRING),
                },
            ),
            400: "Некорректные входные данные",
            403: "Неверный секрет или недоступна привязка",
        },
    )
    def post(self, request):
        telegram_id = request.data.get("telegram_id")
        admin_secret = request.data.get("admin_secret")
        telegram_username = request.data.get("telegram_username")

        if telegram_id is None:
            return Response({"error": "telegram_id_required"}, status=400)

        try:
            telegram_id = int(telegram_id)
        except (TypeError, ValueError):
            return Response({"error": "telegram_id_invalid"}, status=400)

        if telegram_id <= 0:
            return Response({"error": "telegram_id_invalid"}, status=400)

        if not isinstance(admin_secret, str) or not admin_secret.strip():
            return Response({"error": "admin_secret_required"}, status=400)

        admin_user = TelegramAuthService.bind_admin_by_secret(
            telegram_id=telegram_id,
            provided_secret=admin_secret,
        )
        if not admin_user:
            return Response({"error": "admin_bind_denied"}, status=403)

        if isinstance(telegram_username, str):
            clean_username = telegram_username.strip().lstrip("@")
            if clean_username and clean_username != (admin_user.telegram_username or ""):
                admin_user.telegram_username = clean_username
                admin_user.save(update_fields=["telegram_username"])

        return Response({
            "status": "ok",
            "user_id": admin_user.id,
            "role": admin_user.role,
        })



class UserProfileView(APIView):
    @swagger_auto_schema(
        tags=["👤 Drivers"],
        operation_summary="Профиль пользователя по Telegram ID",
        operation_description="""
Возвращает:
• имя  
• телефон  
• язык  
• статус  
• vehicle_id (если активен)  
        """,
    )
    def get(self, request, telegram_id):
        try:
            user = User.objects.get(telegram_id=telegram_id)
        except User.DoesNotExist:
            return Response({"error": "not_found"}, 404)

        data = UserSerializer(user).data
        active_shift_exists = DriverShift.objects.filter(
            driver=user,
            ended_at__isnull=True
        ).exists()

        data["shift_active"] = active_shift_exists

        if user.status == "active":
            req = VehicleRequest.objects.filter(user=user, status="approved").last()
            data["vehicle_id"] = req.vehicle_id if req else None

            assign = VehicleAssignment.objects.filter(driver=user, unassigned_at__isnull=True).select_related(
                "vehicle").first()
            if assign and getattr(assign.vehicle, "imei", None):
                data["vehicle_imei"] = assign.vehicle.imei
            else:
                data["vehicle_imei"] = None
        else:
            data["vehicle_imei"] = None

        return Response(data)



class ApproveVehicleRequestView(APIView):
    @swagger_auto_schema(
    tags=["Requests"],
    request_body=ApproveRequestSerializer,
        responses={200: "Approved"},
        operation_summary="Подтвердить заявку водителя (ADMIN)",
    )
    def post(self, request):
        serializer = ApproveRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        request_id = serializer.validated_data["request_id"]

        req = get_object_or_404(
            VehicleRequest,
            id=request_id,
            status="pending"
        )

        user = req.user
        vehicle_id = req.vehicle_id

        req.status = "approved"
        req.save()

        user.status = "active"
        user.save()

        VehicleAssignment.objects.filter(
            driver=user,
            unassigned_at__isnull=True
        ).update(unassigned_at=timezone.now())

        vehicle = get_object_or_404(Vehicle, veh_id=vehicle_id)
        VehicleAssignment.objects.create(
            driver=user,
            vehicle=vehicle,
        )

        return Response({
            "status": "approved",
            "request_id": req.id,
            "user_id": user.id,
            "vehicle_id": vehicle_id,
        })



class VehicleRequestsListView(APIView):
    @swagger_auto_schema(
        tags=["Requests"],
        operation_summary="Все заявки на технику",
        operation_description="""
Показывает полный список заявок водителей:
• кто отправил  
• какая техника выбрана  
• статус заявки  
        """,
    )
    def get(self, request):
        qs = VehicleRequest.objects.select_related("user").all()

        result = [{
            "request_id": r.id,
            "user_id": r.user.id,
            "full_name": f"{r.user.first_name} {r.user.surname}",
            "phone": r.user.phone,
            "vehicle_id": r.vehicle_id,
            "status": r.status,
            "created_at": r.created_at,
        } for r in qs]

        return Response(result)



class DriverViewSet(viewsets.ViewSet):
    @swagger_auto_schema(tags=["👤 Drivers"], operation_summary="Список активных водителей")
    def list(self, request):
        drivers = CustomUser.objects.filter(role="driver", status="active")
        serializer = DriverCardSerializer(drivers, many=True, context={"request": request})
        return Response({"drivers": serializer.data})

    @swagger_auto_schema(tags=["👤 Drivers"], operation_summary="Детально о водителе")
    def retrieve(self, request, pk=None):
        driver = get_object_or_404(CustomUser, pk=pk, role="driver")
        serializer = DriverDetailSerializer(driver, context={"request": request})
        return Response(serializer.data)


class PendingRequestsView(APIView):
    @swagger_auto_schema(tags=["Requests"])
    def get(self, request):
        qs = VehicleRequest.objects.filter(status="pending").select_related("user")
        serializer = PendingRequestCardSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data)


class PendingRequestDetailView(APIView):
    @swagger_auto_schema(tags=["Requests"])
    def get(self, request, request_id):
        req = get_object_or_404(
            VehicleRequest,
            id=request_id,
            status="pending"
        )
        serializer = PendingRequestDetailSerializer(req, context={"request": request})
        return Response(serializer.data)


class RejectVehicleRequestView(APIView):

    @swagger_auto_schema(
        tags=["Requests"],
        request_body=RejectVehicleRequestSerializer,
        responses={200: "Rejected"}
    )
    def post(self, request):
        serializer = RejectVehicleRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        request_id = serializer.validated_data["request_id"]

        req = get_object_or_404(
            VehicleRequest,
            id=request_id,
            status="pending"
        )

        req.status = "rejected"
        req.save()

        user = req.user
        user.status = "registration"
        user.save()

        return Response({"status": "rejected"})


class StartShiftView(APIView):
    """
    Начало смены водителя.

    Бот отправляет: {telegram_id}
    Backend:
    1. Находит водителя
    2. Находит его текущую машину (VehicleAssignment)
    3. Создаёт DriverShift с привязкой к машине

    Это позволяет потом связать Trip (из Incomtek) с этой сменой.
    """

    def post(self, request):
        telegram_id = request.data.get("telegram_id")
        if telegram_id is None:
            return Response({"error": "telegram_id_required"}, status=400)

        try:
            telegram_id = int(telegram_id)
        except (TypeError, ValueError):
            return Response({"error": "telegram_id_invalid"}, status=400)

        user = User.objects.filter(telegram_id=telegram_id).first()
        if not user:
            return Response({"error": "user_not_found"}, status=404)
        if user.role != "driver":
            return Response({"error": "user_not_driver"}, status=403)
        if user.status != "active":
            return Response({"error": "user_not_active"}, status=403)

        try:
            with transaction.atomic():
                User.objects.select_for_update().filter(pk=user.pk).exists()

                if DriverShift.objects.filter(driver=user, status="active", ended_at__isnull=True).exists():
                    return Response(
                        {"error": "shift_already_started"},
                        status=400
                    )

                assignment = VehicleAssignment.objects.filter(
                    driver=user,
                    unassigned_at__isnull=True
                ).select_related("vehicle").first()

                vehicle = assignment.vehicle if assignment else None

                shift = DriverShift.objects.create(
                    driver=user,
                    vehicle=vehicle,
                    started_at=timezone.now(),
                    status="active"
                )
        except IntegrityError:
            return Response({"error": "shift_already_started"}, status=400)
        except Exception:
            logger.exception("StartShiftView failed", extra={"telegram_id": telegram_id})
            return Response({"error": "internal_error"}, status=500)

        return Response({
            "status": "started",
            "started_at": shift.started_at,
            "vehicle_id": vehicle.veh_id if vehicle else None,
            "vehicle_name": vehicle.name if vehicle else None
        })



class EndShiftView(APIView):
    """
    Закрытие смены водителя.

    Бот отправляет: {telegram_id, trips_count, note?}
    Backend:
    1. Закрывает смену
    2. Сохраняет заявленное количество рейсов (trips_count)
    3. Запускает синхронизацию Trip из Incomtek для этой машины
    4. После синхронизации можно сравнить заявленное vs фактическое

    Важно: используем veh_id (не pilot_id, которого нет в модели)
    """

    def post(self, request):
        telegram_id = request.data.get("telegram_id")
        trips_count = request.data.get("trips_count")
        note = _normalize_note(request.data.get("note"))

        if telegram_id is None:
            return Response({"error": "telegram_id_required"}, status=400)
        try:
            telegram_id = int(telegram_id)
        except (TypeError, ValueError):
            return Response({"error": "telegram_id_invalid"}, status=400)

        if trips_count is None:
            return Response({"error": "trips_count_required"}, status=400)
        try:
            trips_count = int(trips_count)
        except (TypeError, ValueError):
            return Response({"error": "trips_count_invalid"}, status=400)
        if trips_count < 0:
            return Response({"error": "trips_count_invalid"}, status=400)

        user = User.objects.filter(telegram_id=telegram_id).first()
        if not user:
            return Response({"error": "user_not_found"}, status=404)
        if user.role != "driver":
            return Response({"error": "user_not_driver"}, status=403)
        if user.status != "active":
            return Response({"error": "user_not_active"}, status=403)

        shift = DriverShift.objects.filter(driver=user, status="active", ended_at__isnull=True).first()

        if not shift:
            return Response({"error": "shift_not_started"}, status=400)

        end_at = timezone.now()
        shift.trips_count = trips_count
        shift.trips_note = note
        shift.ended_at = end_at
        shift.status = "completed"
        shift.distance_km = 0.0

        if shift.vehicle and shift.engine_hours_at_shift_start is not None:
            shift.engine_hours = round(
                (shift.vehicle.total_engine_hours or 0.0) - shift.engine_hours_at_shift_start, 1
            )

        try:
            shift.save(update_fields=["trips_count", "trips_note", "ended_at", "status", "distance_km", "engine_hours"])
        except Exception:
            logger.exception("EndShiftView failed", extra={"telegram_id": telegram_id})
            return Response({"error": "internal_error"}, status=500)

        vehicle = shift.vehicle
        if not vehicle:
            assignment = (
                VehicleAssignment.objects
                .filter(driver=user)
                .filter(assigned_at__lte=shift.started_at)
                .order_by("-assigned_at")
                .select_related("vehicle")
                .first()
            )
            vehicle = assignment.vehicle if assignment else None

        if vehicle and vehicle.veh_id:
            sync_single_vehicle_task.delay(vehicle.veh_id)

        note_suffix = f"\nПримечание: {shift.trips_note}" if shift.trips_note else ""
        send_notification(
            type_code="info",
            title="Окончание смены",
            message=f"{user.first_name} сдал смену. Рейсов заявлено: {trips_count}{note_suffix}",
            vehicle=vehicle
        )

        return Response({
            "status": "ended",
            "ended_at": shift.ended_at.strftime("%d-%m-%Y %H:%M"),
            "movement_started_at": (shift.movement_started_at or shift.started_at).strftime("%d-%m-%Y %H:%M") if (shift.movement_started_at or shift.started_at) else None,
            "stats": {
                "claimed_trips": trips_count,  
                "trips_note": shift.trips_note,
                "shift_distance_km": shift.distance_km,
                "late_minutes": shift.late_minutes_effective,
                "late_label": shift.late_label,
                "worked_norm_minutes": shift.worked_norm_minutes_effective,
                "worked_norm_label": shift.worked_norm_label,
                "message": "Данные GPS синхронизируются. Фактические рейсы появятся через несколько минут."
            },
            "vehicle": {
                "id": vehicle.veh_id if vehicle else None,
                "name": vehicle.name if vehicle else None
            }
        })

class DeleteDriverView(APIView):
    """
    Удаление водителя админом с отправкой уведомления через бота.
    """

    @swagger_auto_schema(
        tags=["👤 Drivers"],
        operation_summary="Удалить водителя (ADMIN)",
        operation_description="""
Удаляет водителя из системы.

Что делает:
1. Закрывает все активные назначения техники
2. Закрывает все активные смены
3. Отклоняет все pending заявки
4. Удаляет пользователя
5. Отправляет уведомление через бота
        """,
        responses={
            200: "Водитель удалён",
            404: "Водитель не найден",
        }
    )
    def delete(self, request, driver_id):
        driver = get_object_or_404(CustomUser, pk=driver_id, role="driver")

        telegram_id = driver.telegram_id
        driver_name = f"{driver.first_name} {driver.surname}".strip()

        try:
            with transaction.atomic():
                VehicleAssignment.objects.filter(
                    driver=driver,
                    unassigned_at__isnull=True
                ).update(unassigned_at=timezone.now())

                DriverShift.objects.filter(
                    driver=driver,
                    status="active",
                    ended_at__isnull=True
                ).update(
                    status="completed",
                    ended_at=timezone.now()
                )

                VehicleRequest.objects.filter(
                    user=driver,
                    status="pending"
                ).update(status="rejected")

                FuelReport.objects.filter(driver=driver).update(driver=None)
                _null_driver_in_legacy_fuel_reports(driver.id)

                driver.delete()
        except IntegrityError:
            return Response(
                {"error": "Нельзя удалить водителя: есть связанные записи (например, отчёты о заправках)."},
                status=409,
            )

        return Response({
            "status": "deleted",
            "driver_id": driver_id,
            "driver_name": driver_name,
            "telegram_id": telegram_id,
        })

class FuelReportView(APIView):
    """
    Сохранение отчёта о заправке от водителя.

    Логика согласования с Incomtek:
    1. Водитель отправляет фото чека и литры
    2. Backend ищет FuelEvent (от Incomtek) за последние 30 минут для этой машины
    3. Если найден — создаём FuelConfirmation, связывая данные водителя с датчиком
    4. Если не найден — создаём FuelReport как ручной ввод

    Это позволяет:
    - Сравнивать liters_from_driver vs liters_from_sensor
    - Выявлять расхождения
    - Не терять данные ни от водителя, ни от датчика
    """
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @swagger_auto_schema(
        tags=["⛽ Fuel Operations"],
        operation_summary="Сохранить отчёт о заправке",
        operation_description="Водитель отправляет данные о заправке через бота",
    )
    def post(self, request):
        from fuels.models import FuelEvent, FuelConfirmation, FuelReportPhoto, FuelConfirmationPhoto
        from datetime import timedelta

        telegram_id = request.data.get("telegram_id")
        liters = request.data.get("liters")

        photos = request.FILES.getlist("photos")
        photo = request.FILES.get("photo")
        if photo and not photos:
            photos = [photo]

        if hasattr(request.data, "getlist"):
            photo_file_ids = [v for v in request.data.getlist("photo_file_ids") if v]
        else:
            raw_photo_file_ids = request.data.get("photo_file_ids")
            photo_file_ids = raw_photo_file_ids if isinstance(raw_photo_file_ids, list) else []

        single_photo_file_id = request.data.get("photo_file_id")
        if single_photo_file_id and not photo_file_ids:
            photo_file_ids = [single_photo_file_id]

        primary_photo = photos[0] if photos else None
        primary_photo_file_id = photo_file_ids[0] if photo_file_ids else single_photo_file_id

        if not telegram_id or liters is None:
            return Response({"error": "telegram_id and liters required"}, status=400)

        try:
            liters = float(liters)
            if liters <= 0 or liters > 500:
                return Response({"error": "invalid_liters"}, status=400)
        except (ValueError, TypeError):
            return Response({"error": "invalid_liters"}, status=400)

        driver = get_object_or_404(User, telegram_id=telegram_id, role="driver")

        assignment = VehicleAssignment.objects.filter(
            driver=driver,
            unassigned_at__isnull=True
        ).select_related("vehicle").first()

        vehicle = assignment.vehicle if assignment else None

        matched_fuel_event = None
        if vehicle:
            time_window = timezone.now() - timedelta(minutes=30)
            matched_fuel_event = (
                FuelEvent.objects
                .filter(
                    vehicle=vehicle,
                    event_type=1,  
                    created_at__gte=time_window
                )
                .exclude(confirmation__isnull=False)
                .order_by('-created_at')
                .first()
            )

        if matched_fuel_event:
            confirmation, created = FuelConfirmation.objects.get_or_create(
                fuel_event=matched_fuel_event,
                defaults={
                    'driver': driver,
                    'liters_from_driver': liters,
                    'photo': primary_photo,
                    'photo_file_id': primary_photo_file_id,
                }
            )

            if photos:
                FuelConfirmationPhoto.objects.bulk_create(
                    [
                        FuelConfirmationPhoto(
                            confirmation=confirmation,
                            photo=item_photo,
                            photo_file_id=photo_file_ids[idx] if idx < len(photo_file_ids) else None,
                        )
                        for idx, item_photo in enumerate(photos)
                    ]
                )

            # Вычисляем расхождение
            sensor_liters = matched_fuel_event.liters_from_ink or 0
            delta = abs(liters - sensor_liters)
            delta_percent = (delta / sensor_liters * 100) if sensor_liters > 0 else 0

            send_notification(
                type_code="fuel_refill",
                title="Заправка подтверждена",
                message=f"{driver.first_name} подтвердил заправку. Датчик: {sensor_liters}л, Водитель: {liters}л",
                vehicle=vehicle
            )

            return Response({
                "status": "confirmed",
                "fuel_event_id": matched_fuel_event.id,
                "confirmation_id": confirmation.id,
                "liters_from_driver": liters,
                "liters_from_sensor": sensor_liters,
                "delta": round(delta, 2),
                "delta_percent": round(delta_percent, 1),
                "zone_name": matched_fuel_event.zone_name,
                "photos_count": len(photos),
            })

        else:
            # FuelEvent не найден — создаём ручной FuelReport
            report = FuelReport.objects.create(
                driver=driver,
                vehicle=vehicle,
                liters=liters,
                liters_from_driver=liters,
                photo=primary_photo,
                photo_file_id=primary_photo_file_id,
                source=FuelReport.Source.MANUAL,
                confirmed=True
            )

            if photos:
                FuelReportPhoto.objects.bulk_create(
                    [
                        FuelReportPhoto(
                            report=report,
                            photo=item_photo,
                            photo_file_id=photo_file_ids[idx] if idx < len(photo_file_ids) else None,
                        )
                        for idx, item_photo in enumerate(photos)
                    ]
                )

            send_notification(
                type_code="fuel_refill",
                title="Заправка (ручной ввод)",
                message=f"{driver.first_name} заправил {liters} л. (без данных датчика)",
                vehicle=vehicle
            )

            return Response({
                "status": "manual",
                "report_id": report.id,
                "liters": liters,
                "photos_count": len(photos),
                "message": "Данные датчика не найдены. Сохранено как ручной ввод."
            })


class DriverFuelReportCreateView(APIView):
    """
    Приём заправки от водителя (Telegram)
    """

    def post(self, request):
        telegram_id = request.data.get("telegram_id")
        if not telegram_id:
            return Response(
                {"error": "telegram_id_required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        driver = get_object_or_404(CustomUser, telegram_id=telegram_id)

        assignment = VehicleAssignment.objects.filter(
            driver=driver,
            unassigned_at__isnull=True
        ).select_related("vehicle").first()

        if not assignment:
            return Response(
                {"error": "driver_has_no_vehicle"},
                status=status.HTTP_400_BAD_REQUEST
            )

        payload = request.data.copy()

        photos = request.FILES.getlist("photos")
        if photos and hasattr(payload, "setlist"):
            payload.setlist("photos", photos)

        photo_file_ids = request.data.getlist("photo_file_ids") if hasattr(request.data, "getlist") else []
        if photo_file_ids and hasattr(payload, "setlist"):
            payload.setlist("photo_file_ids", photo_file_ids)

        serializer = DriverFuelReportSerializer(
            data=payload,
            context={
                "driver": driver,
                "vehicle": assignment.vehicle,
                "request": request,
            }
        )

        serializer.is_valid(raise_exception=True)
        fuel_report = serializer.save()



        return Response(
            DriverFuelReportSerializer(fuel_report).data,
            status=status.HTTP_201_CREATED
        )


class ShiftDetailsView(APIView):
    """
    GET /api/users/shifts/<int:shift_id>/

    Получение информации о смене.
    """
    permission_classes = []
    authentication_classes = []

    def get(self, request, shift_id):
        try:
            shift = DriverShift.objects.select_related('driver', 'vehicle').get(id=shift_id)
        except DriverShift.DoesNotExist:
            return Response({"error": "Смена не найдена"}, status=404)

        return Response({
            "id": shift.id,
            "driver": {
                "id": shift.driver.id,
                "name": shift.driver.first_name,
                "surname": shift.driver.surname,
                "telegram_id": shift.driver.telegram_id,
            },
            "vehicle": {
                "id": shift.vehicle.id if shift.vehicle else None,
                "name": shift.vehicle.name if shift.vehicle else None,
            } if shift.vehicle else None,
            "started_at": shift.started_at.strftime("%d-%m-%Y %H:%M") if shift.started_at else None,
            "ended_at": shift.ended_at.strftime("%d-%m-%Y %H:%M") if shift.ended_at else None,
            "ignition_on_at": shift.ignition_on_at.strftime("%d-%m-%Y %H:%M") if shift.ignition_on_at else None,
            "movement_started_at": (shift.movement_started_at or shift.started_at).strftime("%d-%m-%Y %H:%M") if (shift.movement_started_at or shift.started_at) else None,
            "auto_created": shift.auto_created,
            "status": shift.status,
            "trips_count": shift.trips_count,
            "trips_note": shift.trips_note,
            "distance_km": shift.distance_km,
            "late_minutes": shift.late_minutes_effective,
            "late_label": shift.late_label,
            "worked_norm_minutes": shift.worked_norm_minutes_effective,
            "worked_norm_label": shift.worked_norm_label,
            "verified_trips_count": shift.verified_trips_count,
            "verified_distance_km": shift.verified_distance_km,
            "trips_delta": shift.trips_delta,
        }, status=200)
