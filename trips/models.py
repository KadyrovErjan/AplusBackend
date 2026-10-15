import hashlib
from django.db import models
from django.utils.translation import gettext_lazy as _


class Trip(models.Model):
    class Meta:
        verbose_name = _("Рейс")
        verbose_name_plural = _("Рейсы")
        indexes = [
            models.Index(fields=['vehicle', 'start_at']),
            models.Index(fields=['uid'], name='trip_uid_idx'),
        ]
        ordering = ['-start_at']

    uid = models.CharField(
        max_length=32,
        unique=True,
        editable=False,
        help_text="MD5 Hash(vehicle_id + timestamp_start) для защиты от дублей"
    )

    vehicle = models.ForeignKey(
        'vehicles.Vehicle',
        on_delete=models.CASCADE,
        related_name='trips'
    )

    start_zone_name = models.CharField(_("Зона отправления"), max_length=255)
    end_zone_name = models.CharField(_("Зона назначения"), max_length=255)

    start_at = models.DateTimeField(_("Начало движения"))
    arrival_at = models.DateTimeField(_("Прибытие в зону назначения"))
    end_at = models.DateTimeField(_("Окончание (выезд с разгрузки)"), null=True, blank=True)

    trip_duration_seconds = models.IntegerField(_("Время в пути (сек)"), default=0)
    dwell_duration_seconds = models.IntegerField(_("Время простоя в конце (сек)"), default=0)
    distance_km = models.DecimalField(_("Пробег (км)"), max_digits=10, decimal_places=3, default=0)

    driver_name = models.CharField(max_length=255, blank=True, null=True)

    def save(self, *args, **kwargs):
        if not self.uid:
            self.uid = self.generate_uid()
        super().save(*args, **kwargs)

    def generate_uid(self):
        raw_str = f"{self.vehicle_id}_{self.start_at.timestamp()}"
        return hashlib.md5(raw_str.encode('utf-8')).hexdigest()

    def __str__(self):
        return f"{self.vehicle} | {self.start_zone_name} -> {self.end_zone_name} ({self.start_at})"