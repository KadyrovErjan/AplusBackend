from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "vehicle", "type", "is_read", "created_at")
    list_filter = ("type", "is_read", "created_at")
    search_fields = ("title", "message", "user__email", "vehicle__plate_number")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)
    fieldsets = (
        ("Уведомление", {"fields": ("type", "title", "message", "is_read", "created_at")}),
        ("Связи", {"fields": ("user", "vehicle")}),
    )
