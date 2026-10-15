from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


class ShiftKPISettings(models.Model):
    """
    Singleton модель для настроек системы учёта смен.

    Используется для хранения всех настраиваемых параметров системы:
    - Параметры автоматического определения начала/окончания смены
    - Настройки мониторинга простоя
    - Рабочие часы и обеденный перерыв
    """

    auto_close_idle_hours = models.FloatField(
        default=2.0,
        validators=[MinValueValidator(0.5), MaxValueValidator(24.0)],
        verbose_name="Автозакрытие смены (часы)",
        help_text="Через сколько часов простоя (без движения и зажигания OFF) закрывать смену автоматически"
    )

    auto_close_idle_ignition_on_hours = models.FloatField(
        default=4.0,
        validators=[MinValueValidator(1.0), MaxValueValidator(24.0)],
        verbose_name="Автозакрытие при простое с зажиганием ON (часы)",
        help_text="Через сколько часов простоя без движения при включённом зажигании (после 18:00) закрывать смену. ended_at = время последнего движения."
    )

    max_shift_duration_hours = models.FloatField(
        default=16.0,
        validators=[MinValueValidator(8.0), MaxValueValidator(24.0)],
        verbose_name="Максимальная длительность смены (часы)",
        help_text="Максимальная длительность смены. После превышения смена закрывается автоматически (защита от забытой смены)"
    )


    min_movement_km = models.FloatField(
        default=0.5,
        validators=[MinValueValidator(0.1), MaxValueValidator(10.0)],
        verbose_name="Минимальное движение (км)",
        help_text="Минимальное расстояние, которое должна проехать техника для начала смены (защита от 'сон в кабине')"
    )

    min_engine_hours_growth = models.FloatField(
        default=0.1,
        validators=[MinValueValidator(0.01), MaxValueValidator(1.0)],
        verbose_name="Минимальный рост моточасов",
        help_text="Минимальное увеличение моточасов для подтверждения работы техники"
    )

    gps_check_interval_minutes = models.IntegerField(
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(30)],
        verbose_name="Интервал проверки GPS (мин)",
        help_text="Как часто система проверяет GPS-данные техники для автосоздания смен"
    )

    enable_auto_shift_creation = models.BooleanField(
        default=True,
        verbose_name="Включить автосоздание смен",
        help_text="Если отключить, система не будет создавать смены автоматически"
    )

    # --- Настройки мониторинга простоя ---

    idle_timeout_minutes = models.IntegerField(
        default=30,
        validators=[MinValueValidator(5), MaxValueValidator(120)],
        verbose_name="Порог простоя (мин)",
        help_text="Через сколько минут простоя отправлять уведомление администратору"
    )

    lunch_break_start_hour = models.IntegerField(
        default=12,
        validators=[MinValueValidator(0), MaxValueValidator(23)],
        verbose_name="Начало обеда (час)",
        help_text="Час начала обеденного перерыва (GMT+6). Простой в обед не учитывается."
    )

    lunch_break_end_hour = models.IntegerField(
        default=13,
        validators=[MinValueValidator(0), MaxValueValidator(23)],
        verbose_name="Конец обеда (час)",
        help_text="Час окончания обеденного перерыва (GMT+6)"
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Последнее обновление"
    )

    updated_by = models.ForeignKey(
        'users.CustomUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Изменил",
        help_text="Администратор, который последний раз менял настройки"
    )

    class Meta:
        verbose_name = "Настройки системы смен"
        verbose_name_plural = "Настройки системы смен"
        db_table = "shift_kpi_settings"

    def __str__(self):
        return f"Настройки системы смен (обновлено: {self.updated_at.strftime('%d-%m-%Y %H:%M')})"

    def save(self, *args, **kwargs):
        """
        Singleton pattern: всегда используем только одну запись с id=1
        """
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_settings(cls):
        """
        Получить единственный экземпляр настроек.
        Если его нет - создать с дефолтными значениями.
        """
        obj, created = cls.objects.get_or_create(pk=1)
        return obj

    def delete(self, *args, **kwargs):
        """
        Запретить удаление настроек (Singleton)
        """
        pass
