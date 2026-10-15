from django.contrib import admin
from django.utils.html import format_html

from .models import IssueReport, IssueReportPhoto, IssueReportProblem


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


class IssuePhotoInline(admin.TabularInline):
    model = IssueReportPhoto
    extra = 0
    fields = ("problem", "photo", "photo_file_id", "created_at", "photo_preview")
    readonly_fields = ("created_at", "photo_preview")
    ordering = ("created_at",)

    def photo_preview(self, obj):
        if not obj:
            return "—"
        return render_admin_image_preview(obj.photo)

    photo_preview.short_description = "Превью"


class IssueProblemInline(admin.TabularInline):
    model = IssueReportProblem
    extra = 0
    fields = ("description", "created_at")
    readonly_fields = ("created_at",)
    ordering = ("created_at",)


@admin.register(IssueReport)
class IssueReportAdmin(admin.ModelAdmin):
    list_display = ("vehicle", "driver", "status", "created_at", "photo_preview", "short_desc")
    list_filter = ("status", "vehicle", "created_at")
    search_fields = (
        "vehicle__plate_number",
        "vehicle__name",
        "driver__email",
        "driver__first_name",
        "driver__surname",
        "description",
    )
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "resolved_at", "last_problem_at", "photo_preview")
    fieldsets = (
        ("Основное", {"fields": ("vehicle", "driver", "status", "created_at", "resolved_at", "last_problem_at")}),
        ("Описание", {"fields": ("description",)}),
        ("Фото", {"fields": ("photo_preview",)}),
    )
    inlines = [IssueProblemInline, IssuePhotoInline]

    def short_desc(self, obj):
        text = (obj.description or "").strip()
        if not text:
            return "—"
        if len(text) <= 50:
            return text
        return f"{text[:47]}..."

    short_desc.short_description = "Описание"

    def photo_preview(self, obj):
        if not obj:
            return "—"
        first_problem = obj.problems.order_by("created_at").first()
        photo = first_problem.photos.first() if first_problem else None
        if not photo:
            photo = obj.photos.first()
        return render_admin_image_preview(photo.photo if photo else None)

    photo_preview.short_description = "Фото"
