import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("vehicles", "0008_alter_vehicle_status"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="IssueReport",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("description", models.TextField(verbose_name="Описание проблемы")),
                        (
                            "status",
                            models.CharField(
                                choices=[("pending", "В ожидании"), ("in_progress", "В работе"), ("resolved", "Решено")],
                                default="pending",
                                max_length=20,
                                verbose_name="Статус",
                            ),
                        ),
                        ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")),
                        ("resolved_at", models.DateTimeField(blank=True, null=True, verbose_name="Дата решения")),
                        ("last_problem_at", models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="Дата последней проблемы")),
                        (
                            "driver",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="+",
                                to=settings.AUTH_USER_MODEL,
                                verbose_name="Водитель",
                            ),
                        ),
                        (
                            "vehicle",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="+",
                                to="vehicles.vehicle",
                                verbose_name="Транспорт",
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Отчёт о поломке",
                        "verbose_name_plural": "Отчёты о поломках",
                        "db_table": "users_issuereport",
                        "ordering": ["-created_at"],
                        "indexes": [
                            models.Index(
                                fields=["driver", "vehicle", "status"],
                                name="users_issue_driver__abf8b6_idx",
                            ),
                        ],
                        "constraints": [
                            models.UniqueConstraint(
                                condition=Q(status__in=["pending", "in_progress"], vehicle__isnull=False),
                                fields=("driver", "vehicle"),
                                name="uniq_active_issue_per_driver_vehicle",
                            ),
                        ],
                    },
                ),
                migrations.CreateModel(
                    name="IssueReportProblem",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("description", models.TextField(verbose_name="Описание проблемы")),
                        ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")),
                        (
                            "report",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="problems",
                                to="maintenance.issuereport",
                                verbose_name="Ремонт",
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Проблема по ремонту",
                        "verbose_name_plural": "Проблемы по ремонту",
                        "db_table": "users_issuereportproblem",
                        "ordering": ["created_at"],
                        "indexes": [
                            models.Index(
                                fields=["report", "created_at"],
                                name="users_issue_report__80f6f3_idx",
                            ),
                        ],
                    },
                ),
                migrations.CreateModel(
                    name="IssueReportPhoto",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("photo", models.ImageField(upload_to="issue_reports/", verbose_name="Фото")),
                        ("photo_file_id", models.CharField(blank=True, max_length=255, null=True, verbose_name="ID файла")),
                        ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Дата загрузки")),
                        (
                            "problem",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="photos",
                                to="maintenance.issuereportproblem",
                                verbose_name="Проблема",
                            ),
                        ),
                        (
                            "report",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="photos",
                                to="maintenance.issuereport",
                                verbose_name="Отчёт",
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Фото поломки",
                        "verbose_name_plural": "Фото поломок",
                        "db_table": "users_issuereportphoto",
                    },
                ),
            ],
        ),
    ]
