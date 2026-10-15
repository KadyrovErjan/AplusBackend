from django.contrib import admin

from .models import FuelZone


@admin.register(FuelZone)
class FuelZoneAdmin(admin.ModelAdmin):
    list_display = ("name", "external_id", "zone_type", "radius_m")
    list_filter = ("zone_type",)
    search_fields = ("name", "=external_id")
    ordering = ("name",)
    fieldsets = (
        ("Основное", {"fields": ("name", "external_id", "zone_type")}),
        ("Границы", {"fields": ("center_lat", "center_lon", "radius_m", "polygon_coords")}),
    )
