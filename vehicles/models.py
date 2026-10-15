from django.db import models
from django.db.models import Q
from django.conf import settings
from datetime import timedelta

from django.utils import timezone

class Vehicle(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Активен"
        IDLE = "idle", "Техника в простое"
        REPAIR = "repair", "Техника на ремонте"
        LOW_FUEL = "low_fuel", "Низкий уровень топлива"
        RENTED = "rented", "Техника в аренде"

    veh_id = models.IntegerField(unique=True, verbose_name="ID транспорта")
    imei = models.CharField(max_length=64, unique=True, verbose_name="IMEI")
    name = models.CharField(max_length=128, null=True, blank=True, verbose_name="Название")
    type = models.CharField(max_length=64, null=True, blank=True, verbose_name="Тип транспорта")

    plate_number = models.CharField(max_length=500, null=True, blank=True, verbose_name="Госномер")
    vin = models.CharField(max_length=64, null=True, blank=True, verbose_name="VIN")
    mileage = models.IntegerField(null=True, blank=True, verbose_name="Пробег (км)")
    to_remaining = models.IntegerField(default=0, verbose_name="Остаток до ТО")
    location_name = models.CharField(max_length=255, null=True, blank=True, verbose_name="Местоположение")

    photo_front = models.CharField(max_length=255, null=True, blank=True, verbose_name="Фото спереди")
    photo_back = models.CharField(max_length=255, null=True, blank=True, verbose_name="Фото сзади")

    lat = models.FloatField(null=True, blank=True, verbose_name="Широта")
    lon = models.FloatField(null=True, blank=True, verbose_name="Долгота")
    speed = models.FloatField(null=True, blank=True, verbose_name="Скорость")
    fuel = models.FloatField(null=True, blank=True, verbose_name="Топливо")
    address = models.CharField(max_length=255, null=True, blank=True, verbose_name="Адрес")

    ignition = models.BooleanField(default=False, verbose_name="Зажигание")
    last_ignition_change_at = models.DateTimeField(null=True, blank=True, help_text="Время последнего изменения статуса зажигания", verbose_name="Последнее изменение зажигания")

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        verbose_name="Статус",
    )

    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    purchase_date = models.DateField(null=True, blank=True, verbose_name="Дата покупки")

    fuel_level = models.FloatField(null=True, blank=True, verbose_name="Уровень топлива")
    total_engine_hours = models.FloatField(null=True, blank=True, verbose_name="Общие моточасы")

    service_interval_hours = models.IntegerField(default=250, help_text="Интервал ТО в моточасах", verbose_name="Интервал ТО (м/ч)")
    last_service_hours = models.FloatField(default=0.0, help_text="Моточасы при последнем ТО", verbose_name="Моточасы последнего ТО")

    driver = models.ForeignKey("users.CustomUser", on_delete=models.SET_NULL, null=True, blank=True, related_name="current_vehicles", verbose_name="Текущий водитель")

    class Meta:
        verbose_name = "Транспорт"
        verbose_name_plural = "Транспорт"

    def __str__(self):
        return self.name or self.plate_number or str(self.veh_id)

    def save(self, *args, **kwargs):
        previous_status = None
        if self.pk:
            previous_status = (
                Vehicle.objects
                .filter(pk=self.pk)
                .values_list("status", flat=True)
                .first()
            )

        super().save(*args, **kwargs)

        if previous_status == self.status:
            return

        timestamp = timezone.now()
        if self.status == self.Status.RENTED:
            VehicleRentHistory.objects.filter(
                vehicle=self,
                finished_at__isnull=True,
            ).update(finished_at=timestamp)
            VehicleRentHistory.objects.create(
                vehicle=self,
                started_at=timestamp,
            )
            return

        if previous_status == self.Status.RENTED:
            VehicleRentHistory.objects.filter(
                vehicle=self,
                finished_at__isnull=True,
            ).update(finished_at=timestamp)


class VehicleRentHistory(models.Model):
    vehicle = models.ForeignKey(
        "Vehicle",
        on_delete=models.CASCADE,
        related_name="rent_history",
        verbose_name="История аренды",
    )
    started_at = models.DateTimeField(verbose_name="Начало аренды")
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Окончание аренды",
    )

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "История аренды транспорта"
        verbose_name_plural = "История аренды транспорта"

    def __str__(self):
        return f"{self.vehicle} | {self.started_at:%d-%m-%Y %H:%M}"


class Track(models.Model):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, verbose_name="Транспорт")
    track_id = models.BigIntegerField(verbose_name="ID трека")
    start_ts = models.BigIntegerField(verbose_name="Начало (timestamp)")
    end_ts = models.BigIntegerField(verbose_name="Окончание (timestamp)")
    raw_track = models.TextField(verbose_name="Сырые данные трека")

    class Meta:
        verbose_name = "Трек"
        verbose_name_plural = "Треки"

    def __str__(self):
        return f"Трек {self.track_id} для {self.vehicle}"


class FuelLevelHistory(models.Model):
    vehicle = models.ForeignKey("Vehicle", on_delete=models.CASCADE, verbose_name="Транспорт")
    ts = models.BigIntegerField(verbose_name="Время (timestamp)")
    fuel = models.FloatField(verbose_name="Уровень топлива")

    class Meta:
        ordering = ["-ts"]
        indexes = [models.Index(fields=["vehicle", "ts"])]
        verbose_name = "История уровня топлива"
        verbose_name_plural = "История уровней топлива"


class TrackPoint(models.Model):
    vehicle = models.ForeignKey("Vehicle", on_delete=models.CASCADE, verbose_name="Транспорт")
    ts = models.BigIntegerField(verbose_name="Время (timestamp)")
    lat = models.FloatField(verbose_name="Широта")
    lon = models.FloatField(verbose_name="Долгота")
    speed = models.FloatField(null=True, blank=True, verbose_name="Скорость")

    class Meta:
        ordering = ["-ts"]
        indexes = [models.Index(fields=["vehicle", "ts"])]
        verbose_name = "Точка трека"
        verbose_name_plural = "Точки трека"


class Event(models.Model):
    vehicle = models.ForeignKey("Vehicle", on_delete=models.CASCADE, verbose_name="Транспорт")
    ts = models.BigIntegerField(verbose_name="Время (timestamp)")
    event_type = models.CharField(max_length=64, verbose_name="Тип события")
    value = models.CharField(max_length=256, null=True, blank=True, verbose_name="Значение")

    class Meta:
        ordering = ["-ts"]
        indexes = [models.Index(fields=["vehicle", "ts"])]
        verbose_name = "Событие"
        verbose_name_plural = "События"


class Geozone(models.Model):
    zone_id = models.IntegerField(unique=True, verbose_name="ID геозоны")
    name = models.CharField(max_length=255, verbose_name="Название")
    zonetype = models.IntegerField(verbose_name="Тип зоны")
    color = models.CharField(max_length=16, null=True, blank=True, verbose_name="Цвет")
    geometry = models.JSONField(null=True, blank=True, verbose_name="Геометрия")

    class Meta:
        verbose_name = "Геозона"
        verbose_name_plural = "Геозоны"

    def __str__(self):
        return self.name


class VehicleStatusHistory(models.Model):
    vehicle = models.ForeignKey("Vehicle", on_delete=models.CASCADE, verbose_name="Транспорт")
    ts = models.BigIntegerField(verbose_name="Время (timestamp)")
    lat = models.FloatField(verbose_name="Широта")
    lon = models.FloatField(verbose_name="Долгота")
    speed = models.FloatField(null=True, blank=True, verbose_name="Скорость")
    ignition = models.BooleanField(default=False, verbose_name="Зажигание")
    fuel = models.FloatField(null=True, blank=True, verbose_name="Топливо")

    class Meta:
        ordering = ["-ts"]
        indexes = [
            models.Index(fields=["ignition"]),
            models.Index(fields=["vehicle", "ts"]),
        ]
        verbose_name = "История состояния транспорта"
        verbose_name_plural = "История состояний транспорта"


class VehicleAssignment(models.Model):
    vehicle = models.ForeignKey("Vehicle", on_delete=models.CASCADE, verbose_name="Транспорт")
    driver = models.ForeignKey("users.CustomUser", on_delete=models.SET_NULL, null=True, blank=True, related_name="vehicles", verbose_name="Водитель")
    assigned_at = models.DateTimeField(auto_now_add=True, verbose_name="Назначен")
    unassigned_at = models.DateTimeField(null=True, blank=True, verbose_name="Снят")

    class Meta:
        indexes = [models.Index(fields=["vehicle", "driver"])]
        verbose_name = "Назначение транспорта"
        verbose_name_plural = "Назначения транспорта"


class Repair(models.Model):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, verbose_name="Транспорт")
    date = models.DateField(verbose_name="Дата ремонта")
    title = models.CharField(max_length=255, verbose_name="Название")
    description = models.TextField(blank=True, verbose_name="Описание")
    parts = models.JSONField(blank=True, null=True, verbose_name="Запчасти")
    cost_parts = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Стоимость запчастей")
    cost_work = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Стоимость работ")
    status = models.CharField(max_length=32, default="done", verbose_name="Статус")

    class Meta:
        verbose_name = "Ремонт"
        verbose_name_plural = "Ремонты"


class VehicleRequest(models.Model):
    STATUS = [
        ("pending", "В ожидании"),
        ("approved", "Одобрено"),
        ("rejected", "Отклонено"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="Пользователь")
    vehicle_id = models.CharField(max_length=50, verbose_name="ID транспорта")
    status = models.CharField(max_length=20, choices=STATUS, default="pending", verbose_name="Статус")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        verbose_name = "Запрос на транспорт"
        verbose_name_plural = "Запросы на транспорт"
        db_table = "users_vehiclerequest"
    def __str__(self):
        return f"{self.user} — {self.vehicle_id} ({self.get_status_display()})"


class DriverShift(models.Model):
    """
    Смена водителя.

    Поток данных:
    1. Смена создаётся автоматически по GPS (зажигание + движение)
    2. Во время смены Incomtek фиксирует рейсы (Trip)
    3. Водитель вводит trips_count при закрытии смены через бот
    4. Backend связывает Trip с этой сменой по vehicle + времени
    5. На сайте видно сравнение: заявлено vs фактически (GPS)
    """
    STATUS_CHOICES = [
        ("active", "Активна"),
        ("completed", "Завершена"),
    ]

    driver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shifts",
        verbose_name="Водитель"
    )

    vehicle = models.ForeignKey(
        "vehicles.Vehicle",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shifts",
        verbose_name="Транспорт"
    )

    started_at = models.DateTimeField(verbose_name="Начало смены")
    ended_at = models.DateTimeField(null=True, blank=True, verbose_name="Окончание смены")

    ignition_on_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Время включения зажигания",
        help_text="Реальное время, когда водитель включил зажигание (по GPS)"
    )

    movement_started_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Фактическое начало движения",
        help_text="Время, когда техника реально начала движение (по GPS порогу старта)"
    )

    idle_since = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Простой с",
        help_text="Время начала текущего простоя (для мониторинга)"
    )

    idle_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Уведомление о простое отправлено",
        help_text="Когда последний раз отправлено уведомление о простое (антиспам)"
    )

    distance_at_shift_start = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Пробег на начало смены (км)",
        help_text="Используется для проверки минимального движения"
    )

    engine_hours_at_shift_start = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Моточасы на начало смены",
        help_text="Используется для проверки роста моточасов"
    )

    auto_created = models.BooleanField(
        default=False,
        verbose_name="Автосоздана",
        help_text="Была ли смена создана автоматически системой (True) или вручную водителем (False)"
    )

    trips_count = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Заявлено рейсов",
        help_text="Количество рейсов, введённое водителем при закрытии смены"
    )

    trips_note = models.TextField(
        null=True,
        blank=True,
        verbose_name="Примечание по рейсам",
        help_text="Короткое примечание водителя к рейсам за смену"
    )

    late_minutes = models.PositiveIntegerField(
        default=0,
        verbose_name="Опоздание (мин)",
        help_text="На сколько минут фактический старт движения позже 09:30"
    )

    worked_norm_minutes = models.PositiveIntegerField(
        default=480,
        verbose_name="Учтено рабочих минут",
        help_text="Норма 8 часов (480 мин) минус опоздание"
    )

    distance_km = models.FloatField(null=True, blank=True, verbose_name="Пройденное расстояние (км)")
    route_from = models.CharField(max_length=255, null=True, blank=True, verbose_name="Маршрут от")
    route_to = models.CharField(max_length=255, null=True, blank=True, verbose_name="Маршрут до")
    zones_data = models.JSONField(null=True, blank=True, verbose_name="Данные зон")
    engine_hours = models.FloatField(default=0.0, verbose_name="Моточасы")

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active", verbose_name="Статус смены")

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "Смена водителя"
        verbose_name_plural = "Смены водителей"
        db_table = "users_drivershift"
        indexes = [
            models.Index(fields=['vehicle', 'started_at']),
            models.Index(fields=['driver', 'status']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["driver"],
                condition=Q(status="active", ended_at__isnull=True),
                name="uniq_active_shift_per_driver",
            ),
            models.UniqueConstraint(
                fields=["vehicle"],
                condition=Q(status="active", ended_at__isnull=True, vehicle__isnull=False),
                name="uniq_active_shift_per_vehicle",
            ),
        ]

    def get_verified_trips(self):
        """
        Получить фактические рейсы (GPS) за период смены.
        Это Trip записи из Incomtek, привязанные по vehicle и времени.
        """
        from trips.models import Trip
        from django.utils import timezone

        if not self.vehicle:
            return Trip.objects.none()

        end_time = self.ended_at or timezone.now()
        return Trip.objects.filter(
            vehicle=self.vehicle,
            start_at__gte=self.started_at,
            start_at__lte=end_time
        )

    @property
    def verified_trips_count(self):
        """Количество рейсов по GPS"""
        return self.get_verified_trips().count()

    @property
    def verified_distance_km(self):
        """
        Подтвержденная дистанция смены.
        Источник: рассчитанная дистанция самой смены (DriverShift.distance_km).
        """
        return float(self.distance_km or 0.0)

    @property
    def trips_delta(self):
        """
        Расхождение между заявленным и фактическим.
        Положительное = водитель заявил больше, чем GPS видит.
        """
        claimed = self.trips_count or 0
        return claimed - self.verified_trips_count

    def _compute_work_metrics(self):
        """
        Вычисляет опоздание и учтенные рабочие минуты от фактического старта.
        """
        from django.utils import timezone

        norm_minutes = int(getattr(settings, "WORKDAY_NORM_MINUTES", 480))
        start_hour = int(getattr(settings, "WORKDAY_START_HOUR_LOCAL", 9))
        warmup_minutes = int(getattr(settings, "WORKDAY_WARMUP_MINUTES", 30))

        norm_minutes = max(0, norm_minutes)
        start_hour = max(0, min(23, start_hour))
        warmup_minutes = max(0, warmup_minutes)

        start_point = self.movement_started_at or self.started_at
        if not start_point:
            return 0, norm_minutes

        local_start = timezone.localtime(start_point)
        deadline = local_start.replace(hour=start_hour, minute=0, second=0, microsecond=0) + timedelta(minutes=warmup_minutes)

        late = max(0, int((local_start - deadline).total_seconds() // 60))
        worked = max(0, norm_minutes - late)
        return late, worked

    @property
    def late_minutes_effective(self):
        late, _ = self._compute_work_metrics()
        return late

    @property
    def worked_norm_minutes_effective(self):
        _, worked = self._compute_work_metrics()
        return worked

    @staticmethod
    def _format_minutes_label(value):
        value = int(value or 0)
        hours = value // 60
        minutes = value % 60
        return f"{hours}ч {minutes}м"

    @property
    def late_label(self):
        return self._format_minutes_label(self.late_minutes_effective)

    @property
    def worked_norm_label(self):
        return self._format_minutes_label(self.worked_norm_minutes_effective)


from maintenance.models import IssueReport, IssueReportPhoto, IssueReportProblem  # noqa: E402
