from django.db import migrations, models
from django.db.models import Count, F, Q


def backfill_issue_problem_history(apps, schema_editor):
    IssueReport = apps.get_model("vehicles", "IssueReport")
    IssueReportPhoto = apps.get_model("vehicles", "IssueReportPhoto")
    IssueReportProblem = apps.get_model("vehicles", "IssueReportProblem")

    for report in IssueReport.objects.all().iterator():
        problem = IssueReportProblem.objects.create(
            report_id=report.id,
            description=report.description or "",
        )
        IssueReportProblem.objects.filter(id=problem.id).update(created_at=report.created_at)
        IssueReportPhoto.objects.filter(report_id=report.id).update(problem_id=problem.id)
        IssueReport.objects.filter(id=report.id).update(last_problem_at=report.created_at)


def collapse_duplicate_active_issue_reports(apps, schema_editor):
    IssueReport = apps.get_model("vehicles", "IssueReport")
    active_qs = IssueReport.objects.filter(
        status__in=["pending", "in_progress"],
        vehicle_id__isnull=False,
    )

    duplicate_pairs = (
        active_qs
        .values("driver_id", "vehicle_id")
        .annotate(cnt=Count("id"))
        .filter(cnt__gt=1)
    )

    for pair in duplicate_pairs:
        reports = active_qs.filter(
            driver_id=pair["driver_id"],
            vehicle_id=pair["vehicle_id"],
        ).order_by("created_at", "id")
        keep = reports.first()
        if keep:
            reports.exclude(id=keep.id).update(
                status="resolved",
                resolved_at=F("created_at"),
            )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("vehicles", "0006_drivershift_active_unique_constraints"),
    ]

    operations = [
        migrations.AddField(
            model_name="issuereport",
            name="last_problem_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="Дата последней проблемы"),
        ),
        migrations.CreateModel(
            name="IssueReportProblem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("description", models.TextField(verbose_name="Описание проблемы")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")),
                (
                    "report",
                    models.ForeignKey(on_delete=models.CASCADE, related_name="problems", to="vehicles.issuereport", verbose_name="Ремонт"),
                ),
            ],
            options={
                "verbose_name": "Проблема по ремонту",
                "verbose_name_plural": "Проблемы по ремонту",
                "db_table": "users_issuereportproblem",
                "ordering": ["created_at"],
                "indexes": [models.Index(fields=["report", "created_at"], name="users_issue_report__80f6f3_idx")],
            },
        ),
        migrations.AddField(
            model_name="issuereportphoto",
            name="problem",
            field=models.ForeignKey(blank=True, null=True, on_delete=models.CASCADE, related_name="photos", to="vehicles.issuereportproblem", verbose_name="Проблема"),
        ),
        migrations.RunPython(backfill_issue_problem_history, noop_reverse, atomic=False),
        migrations.RunPython(collapse_duplicate_active_issue_reports, noop_reverse, atomic=False),
        migrations.AddIndex(
            model_name="issuereport",
            index=models.Index(fields=["driver", "vehicle", "status"], name="users_issue_driver__abf8b6_idx"),
        ),
        migrations.AddConstraint(
            model_name="issuereport",
            constraint=models.UniqueConstraint(
                condition=Q(status__in=["pending", "in_progress"], vehicle__isnull=False),
                fields=("driver", "vehicle"),
                name="uniq_active_issue_per_driver_vehicle",
            ),
        ),
    ]
