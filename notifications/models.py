from django.db import models
from django.conf import settings


class Notification(models.Model):
    TYPE_CHOICES = [
        ("service", "Поломка"),
        ("low_fuel", "Мало топлива"),
        ("fuel_refill", "Заправка"),
        ("driver_request", "Регистрация водителя"),
        ("warning", "Предупреждение (слив / геозона)"),
        ("info", "Информация (смена)"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True, blank=True,
        verbose_name="Пользователь"
    )

    vehicle = models.ForeignKey(
        "vehicles.Vehicle",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name="Транспорт"
    )

    type = models.CharField(max_length=32, choices=TYPE_CHOICES, verbose_name="Тип уведомления")
    title = models.CharField(max_length=255, verbose_name="Заголовок")
    message = models.TextField(verbose_name="Сообщение")

    is_read = models.BooleanField(default=False, verbose_name="Прочитано")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Уведомление"
        verbose_name_plural = "Уведомления"

    def __str__(self):
        return f"[{self.get_type_display()}] {self.title}"
