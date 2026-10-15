from django.db import models
from django.conf import settings

class FuelReport(models.Model):
    class Source(models.TextChoices):
        SENSOR = "sensor", "Датчик"
        MANUAL = "manual", "Вручную"

    driver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="fuel_reports", verbose_name="Водитель")
    vehicle = models.ForeignKey("vehicles.Vehicle", on_delete=models.SET_NULL, null=True, blank=True, related_name="fuel_reports", verbose_name="Транспорт")

    liters = models.FloatField(verbose_name="Объём топлива (л)")
    photo = models.ImageField(upload_to="fuel_reports/", null=True, blank=True, verbose_name="Фото")
    photo_file_id = models.CharField(max_length=500, null=True, blank=True, verbose_name="ID файла фото")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    zone_id = models.BigIntegerField(null=True, blank=True, verbose_name="ID зоны")
    zone_name = models.CharField(max_length=500, null=True, blank=True, verbose_name="Название зоны")

    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL, verbose_name="Источник")
    confirmed = models.BooleanField(default=True, verbose_name="Подтверждено")
    liters_from_driver = models.FloatField(null=True, blank=True, verbose_name="Литры от водителя")
    liters_from_ink = models.FloatField(null=True, blank=True, verbose_name="Литры от датчика")

    lat = models.FloatField(null=True, blank=True, verbose_name="Широта")
    lon = models.FloatField(null=True, blank=True, verbose_name="Долгота")
    address = models.CharField(max_length=500, null=True, blank=True, verbose_name="Адрес")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Отчёт о заправке"
        verbose_name_plural = "Отчёты о заправках"

    def __str__(self):
        return f"{self.vehicle} | {self.liters} л"


class FuelReportPhoto(models.Model):
    report = models.ForeignKey(
        "fuels.FuelReport",
        on_delete=models.CASCADE,
        related_name="photos",
        verbose_name="Отчет о заправке",
    )
    photo = models.ImageField(upload_to="fuel_reports/", verbose_name="Фото")
    photo_file_id = models.CharField(max_length=500, null=True, blank=True, verbose_name="ID файла фото")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        ordering = ["id"]
        verbose_name = "Фото отчета о заправке"
        verbose_name_plural = "Фото отчетов о заправке"

    def __str__(self):
        return f"Фото FuelReport #{self.report_id}"


class FuelZone(models.Model):
    external_id = models.IntegerField(unique=True, null=True, blank=True, verbose_name="Внешний ID")
    name = models.CharField(max_length=255, verbose_name="Название зоны")

    ZONE_TYPE_CHOICES = (
        (1, "Полигон"),
        (2, "Круг"),
    )
    zone_type = models.IntegerField(choices=ZONE_TYPE_CHOICES, default=1, verbose_name="Тип зоны")

    center_lat = models.FloatField(null=True, blank=True, verbose_name="Широта центра")
    center_lon = models.FloatField(null=True, blank=True, verbose_name="Долгота центра")
    radius_m = models.FloatField(null=True, blank=True, verbose_name="Радиус (м)")

    polygon_coords = models.JSONField(null=True, blank=True, verbose_name="Координаты полигона")

    class Meta:
        verbose_name = "Объекты"
        verbose_name_plural = "Объекты"

    def __str__(self):
        return self.name


class FuelEventType(models.IntegerChoices):
    FUELING = 1, "Заправка"
    DRAIN = 2, "Слив"


class FuelEvent(models.Model):
    vehicle = models.ForeignKey("vehicles.Vehicle", on_delete=models.CASCADE, related_name="fuel_events", verbose_name="Транспорт")
    liters_from_ink = models.FloatField(null=True, blank=True, verbose_name="Объём топлива (л)")
    lat = models.FloatField(null=True, blank=True, verbose_name="Широта")
    lon = models.FloatField(null=True, blank=True, verbose_name="Долгота")
    address = models.TextField(null=True, blank=True, verbose_name="Адрес")

    zone = models.ForeignKey("fuels.FuelZone", on_delete=models.SET_NULL, null=True, blank=True, related_name="events", verbose_name="Топливная зона")

    event_type = models.PositiveSmallIntegerField(choices=FuelEventType.choices, default=FuelEventType.FUELING, verbose_name="Тип события")

    incomtek_zone_id = models.CharField(max_length=100, null=True, blank=True, verbose_name="ID зоны Incomtek")
    zone_name = models.CharField(max_length=255, null=True, blank=True, verbose_name="Название зоны")
    created_at = models.DateTimeField(verbose_name="Дата события")

    class Meta:
        verbose_name = "Топливное событие"
        verbose_name_plural = "Топливные события"

    def __str__(self):
        return f"{self.get_event_type_display()} · {self.liters_from_ink or 0} л"


class FuelConfirmation(models.Model):
    fuel_event = models.OneToOneField(FuelEvent, on_delete=models.CASCADE, related_name="confirmation", verbose_name="Топливное событие")
    driver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="Водитель")

    liters_from_driver = models.FloatField(verbose_name="Литры от водителя")
    photo = models.ImageField(upload_to="fuel_reports/", null=True, blank=True, verbose_name="Фото")
    photo_file_id = models.CharField(max_length=500, null=True, blank=True, verbose_name="ID файла фото")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата подтверждения")

    class Meta:
        verbose_name = "Подтверждение заправки"
        verbose_name_plural = "Подтверждения заправок"

    def __str__(self):
        return f"Подтверждение #{self.id}"


class FuelConfirmationPhoto(models.Model):
    confirmation = models.ForeignKey(
        "fuels.FuelConfirmation",
        on_delete=models.CASCADE,
        related_name="photos",
        verbose_name="Подтверждение заправки",
    )
    photo = models.ImageField(upload_to="fuel_reports/", verbose_name="Фото")
    photo_file_id = models.CharField(max_length=500, null=True, blank=True, verbose_name="ID файла фото")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        ordering = ["id"]
        verbose_name = "Фото подтверждения заправки"
        verbose_name_plural = "Фото подтверждений заправки"

    def __str__(self):
        return f"Фото FuelConfirmation #{self.confirmation_id}"
