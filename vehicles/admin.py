from django.contrib import admin

from .models import Vehicle, VehicleAssignment, VehicleRentHistory


class VehicleAssignmentInline(admin.TabularInline):
    model = VehicleAssignment
    extra = 0
    autocomplete_fields = ("driver",)
    readonly_fields = ("assigned_at",)
    fields = ("driver", "assigned_at", "unassigned_at")
    ordering = ("-assigned_at",)
    show_change_link = True


class VehicleRentHistoryInline(admin.TabularInline):
    model = VehicleRentHistory
    extra = 0
    fields = ("started_at", "finished_at")
    readonly_fields = ("started_at", "finished_at")
    ordering = ("-started_at",)
    can_delete = False


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = (
        "veh_id",
        "plate_number",
        "name",
        "status",
        "type",
        "ignition",
        "last_ignition_change_at",
        "mileage",
    )
    list_filter = ("status", "type", "ignition", "updated_at")
    search_fields = ("=veh_id", "plate_number", "imei", "name", "vin")
    ordering = ("plate_number", "name", "veh_id")
    readonly_fields = ("updated_at", "last_ignition_change_at")
    autocomplete_fields = ("driver",)
    inlines = [VehicleAssignmentInline, VehicleRentHistoryInline]
    fieldsets = (
        ("Основное", {"fields": ("veh_id", "imei", "name", "type", "plate_number", "vin", "status")}),
        ("Телеметрия", {"fields": ("lat", "lon", "speed", "fuel", "fuel_level", "ignition", "last_ignition_change_at", "updated_at")}),
        ("Эксплуатация", {"fields": ("mileage", "total_engine_hours", "service_interval_hours", "last_service_hours", "to_remaining", "purchase_date", "location_name", "address")}),
        ("Фото", {"fields": ("photo_front", "photo_back")}),
        ("Совместимость", {"fields": ("driver",), "classes": ("collapse",)}),
    )


@admin.register(VehicleAssignment)
class VehicleAssignmentAdmin(admin.ModelAdmin):
    list_display = ("vehicle", "driver", "assigned_at", "unassigned_at")
    list_filter = ("vehicle", "assigned_at", "unassigned_at")
    search_fields = (
        "vehicle__plate_number",
        "vehicle__name",
        "vehicle__imei",
        "driver__email",
        "driver__first_name",
        "driver__surname",
    )
    ordering = ("-assigned_at",)
    autocomplete_fields = ("vehicle", "driver")
    readonly_fields = ("assigned_at",)
    fieldsets = (
        ("Назначение", {"fields": ("vehicle", "driver")}),
        ("Период", {"fields": ("assigned_at", "unassigned_at")}),
    )


@admin.register(VehicleRentHistory)
class VehicleRentHistoryAdmin(admin.ModelAdmin):
    list_display = ("vehicle", "started_at", "finished_at")
    list_filter = ("started_at", "finished_at")
    search_fields = ("vehicle__plate_number", "vehicle__name", "vehicle__imei")
    ordering = ("-started_at",)
    autocomplete_fields = ("vehicle",)
    readonly_fields = ("started_at", "finished_at")
