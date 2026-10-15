import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("maintenance", "0001_initial"),
        ("vehicles", "0009_move_issue_models_to_maintenance"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="issuereport",
                    name="driver",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="issue_reports",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Водитель",
                    ),
                ),
                migrations.AlterField(
                    model_name="issuereport",
                    name="vehicle",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="issue_reports",
                        to="vehicles.vehicle",
                        verbose_name="Транспорт",
                    ),
                ),
            ],
        ),
    ]
