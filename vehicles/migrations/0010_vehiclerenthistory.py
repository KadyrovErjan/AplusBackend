import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("vehicles", "0009_move_issue_models_to_maintenance"),
    ]

    operations = [
        migrations.CreateModel(
            name="VehicleRentHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("started_at", models.DateTimeField(verbose_name="Начало аренды")),
                ("finished_at", models.DateTimeField(blank=True, null=True, verbose_name="Окончание аренды")),
                (
                    "vehicle",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rent_history",
                        to="vehicles.vehicle",
                        verbose_name="История аренды",
                    ),
                ),
            ],
            options={
                "verbose_name": "История аренды транспорта",
                "verbose_name_plural": "История аренды транспорта",
                "ordering": ["-started_at"],
            },
        ),
    ]
