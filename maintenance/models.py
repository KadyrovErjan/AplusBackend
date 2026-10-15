from django.conf import settings
from django.db import models
from django.db.models import Q


class IssueReport(models.Model):
    STATUS_CHOICES = [
        ("pending", "В ожидании"),
        ("in_progress", "В работе"),
        ("resolved", "Решено"),
    ]

    driver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="issue_reports",
        verbose_name="Водитель",
    )
    vehicle = models.ForeignKey(
        "vehicles.Vehicle",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issue_reports",
        verbose_name="Транспорт",
    )
    description = models.TextField(verbose_name="Описание проблемы")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        verbose_name="Статус",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата решения")
    last_problem_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Дата последней проблемы",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Отчёт о поломке"
        verbose_name_plural = "Отчёты о поломках"
        db_table = "users_issuereport"
        indexes = [
            models.Index(
                fields=["driver", "vehicle", "status"],
                name="users_issue_driver__abf8b6_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["driver", "vehicle"],
                condition=Q(status__in=["pending", "in_progress"], vehicle__isnull=False),
                name="uniq_active_issue_per_driver_vehicle",
            ),
        ]

    def __str__(self):
        return f"{self.driver} | {self.get_status_display()}"


class IssueReportProblem(models.Model):
    report = models.ForeignKey(
        IssueReport,
        on_delete=models.CASCADE,
        related_name="problems",
        verbose_name="Ремонт",
    )
    description = models.TextField(verbose_name="Описание проблемы")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Проблема по ремонту"
        verbose_name_plural = "Проблемы по ремонту"
        db_table = "users_issuereportproblem"
        indexes = [
            models.Index(
                fields=["report", "created_at"],
                name="users_issue_report__80f6f3_idx",
            ),
        ]


class IssueReportPhoto(models.Model):
    report = models.ForeignKey(
        IssueReport,
        on_delete=models.CASCADE,
        related_name="photos",
        verbose_name="Отчёт",
    )
    problem = models.ForeignKey(
        "maintenance.IssueReportProblem",
        on_delete=models.CASCADE,
        related_name="photos",
        null=True,
        blank=True,
        verbose_name="Проблема",
    )
    photo = models.ImageField(upload_to="issue_reports/", verbose_name="Фото")
    photo_file_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        verbose_name="ID файла",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата загрузки")

    class Meta:
        verbose_name = "Фото поломки"
        verbose_name_plural = "Фото поломок"
        db_table = "users_issuereportphoto"
