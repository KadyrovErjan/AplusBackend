from django.contrib import admin
from django.contrib.admin.sites import NotRegistered
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.utils.html import format_html

from fuels.models import FuelEvent, FuelReport
from vehicles.models import DriverShift, VehicleRequest

from .models import CustomUser, ShiftKPISettings


def render_admin_image_preview(file_field):
    if not file_field:
        return "—"
    try:
        url = file_field.url
    except (AttributeError, ValueError):
        return "—"
    return format_html(
        '<a href="{0}" target="_blank"><img src="{0}" style="height:60px; border-radius:6px;" /></a>',
        url,
    )


try:
    admin.site.unregister(Group)
except NotRegistered:
    pass


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = ("first_name", "surname", "username", "role", "phone", "status", "telegram_id", "is_active")
    list_filter = ("role", "status", "is_active", "is_staff")
    search_fields = ("email", "username", "phone", "first_name", "surname", "last_name", "=telegram_id")
    ordering = ("first_name", "surname", "username")
    fieldsets = (
        *UserAdmin.fieldsets,
        ("Профиль", {"fields": ("surname", "role", "phone", "telegram_id", "status", "birthdate")}),
        ("Документы", {"fields": ("passport_id", "iin", "address", "passport_front", "passport_back", "driver_license_front", "driver_license_back")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "username", "password1", "password2"),
            },
        ),
        (
            "Профиль",
            {
                "fields": ("first_name", "surname", "role", "phone", "telegram_id", "status", "birthdate"),
            },
        ),
    )


@admin.register(DriverShift)
class DriverShiftAdmin(admin.ModelAdmin):
    list_display = (
        "driver",
        "vehicle",
        "status",
        "started_at",
        "ended_at",
        "auto_created",
        "late_minutes",
        "trips_count",
        "trips_note_short",
        "verified_trips_count",
        "engine_hours",
    )
    list_filter = ("status", "auto_created", "started_at", "vehicle")
    search_fields = (
        "driver__email",
        "driver__first_name",
        "driver__surname",
        "vehicle__plate_number",
        "vehicle__name",
    )
    ordering = ("-started_at",)
    readonly_fields = (
        "started_at",
        "ignition_on_at",
        "movement_started_at",
        "distance_at_shift_start",
        "engine_hours_at_shift_start",
        "verified_trips_count",
        "trips_delta",
        "idle_since",
        "idle_notified_at",
    )
    fieldsets = (
        ("Основное", {"fields": ("driver", "vehicle", "status", "auto_created")}),
        ("Время", {"fields": ("started_at", "ended_at", "ignition_on_at", "movement_started_at")}),
        ("Показатели", {"fields": ("late_minutes", "worked_norm_minutes", "distance_km", "engine_hours")}),
        ("Рейсы", {"fields": ("trips_count", "trips_note", "verified_trips_count", "trips_delta")}),
        ("Маршрут и зоны", {"fields": ("route_from", "route_to", "zones_data", "distance_at_shift_start", "engine_hours_at_shift_start"), "classes": ("collapse",)}),
        ("Мониторинг простоя", {"fields": ("idle_since", "idle_notified_at"), "classes": ("collapse",)}),
    )

    def trips_note_short(self, obj):
        note = (obj.trips_note or "").strip()
        if not note:
            return "—"
        if len(note) <= 40:
            return note
        return f"{note[:37]}..."

    trips_note_short.short_description = "Примечание"


@admin.register(FuelReport)
class FuelReportAdmin(admin.ModelAdmin):
    list_display = ("vehicle", "driver", "liters", "source", "confirmed", "photo_preview", "created_at")
    list_filter = ("source", "confirmed", "vehicle", "created_at")
    search_fields = (
        "vehicle__plate_number",
        "vehicle__name",
        "driver__email",
        "driver__first_name",
        "driver__surname",
        "zone_name",
    )
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "photo_preview")
    fieldsets = (
        ("Основное", {"fields": ("vehicle", "driver", "source", "confirmed", "created_at")}),
        ("Заправка", {"fields": ("liters", "liters_from_driver", "liters_from_ink")}),
        ("Фото", {"fields": ("photo_preview", "photo", "photo_file_id")}),
        ("Местоположение", {"fields": ("zone_id", "zone_name", "lat", "lon", "address"), "classes": ("collapse",)}),
    )

    def photo_preview(self, obj):
        if not obj:
            return "—"
        photo = obj.photos.first()
        if photo and photo.photo:
            return render_admin_image_preview(photo.photo)
        return render_admin_image_preview(obj.photo)

    photo_preview.short_description = "Фото"


@admin.register(FuelEvent)
class FuelEventAdmin(admin.ModelAdmin):
    list_display = ("vehicle", "liters_from_ink", "event_type", "created_at", "zone_name")
    list_filter = ("event_type", "created_at", "zone")
    search_fields = ("vehicle__plate_number", "vehicle__name", "zone_name", "address")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)
    fieldsets = (
        ("Основное", {"fields": ("vehicle", "event_type", "liters_from_ink", "created_at")}),
        ("Местоположение", {"fields": ("zone", "zone_name", "incomtek_zone_id", "lat", "lon", "address")}),
    )


@admin.register(VehicleRequest)
class VehicleRequestAdmin(admin.ModelAdmin):
    list_display = ("user", "vehicle_id", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("user__email", "user__first_name", "user__surname", "vehicle_id")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)
    fieldsets = (
        ("Заявка", {"fields": ("user", "vehicle_id", "status", "created_at")}),
    )


@admin.register(ShiftKPISettings)
class ShiftKPISettingsAdmin(admin.ModelAdmin):
    list_display = ("enable_auto_shift_creation", "max_shift_duration_hours", "idle_timeout_minutes", "updated_at", "updated_by")
    readonly_fields = ("updated_at",)
    fieldsets = (
        (
            "Автоматическое управление сменами",
            {
                "fields": (
                    "enable_auto_shift_creation",
                    "auto_close_idle_hours",
                    "auto_close_idle_ignition_on_hours",
                    "max_shift_duration_hours",
                    "gps_check_interval_minutes",
                ),
                "description": "Параметры автосоздания и автозакрытия смен.",
            },
        ),
        (
            "Критерии старта смены",
            {
                "fields": (
                    "min_movement_km",
                    "min_engine_hours_growth",
                ),
                "description": "Минимальные значения для автоматического начала смены.",
            },
        ),
        (
            "Мониторинг простоя",
            {
                "fields": (
                    "idle_timeout_minutes",
                    "lunch_break_start_hour",
                    "lunch_break_end_hour",
                ),
                "description": "Настройки уведомлений о простое техники.",
            },
        ),
        (
            "Служебная информация",
            {
                "fields": (
                    "updated_at",
                    "updated_by",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def has_add_permission(self, request):
        return not ShiftKPISettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

    def changelist_view(self, request, extra_context=None):
        settings_obj = ShiftKPISettings.objects.first()
        if settings_obj is None:
            settings_obj = ShiftKPISettings.objects.create()

        from django.http import HttpResponseRedirect
        from django.urls import reverse

        return HttpResponseRedirect(
            reverse("admin:users_shiftkpisettings_change", args=[settings_obj.pk])
        )

    class Media:
        css = {
            "all": ("admin/css/shift_kpi_settings.css",)
        }
