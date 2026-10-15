from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("vehicles", "0007_issuereport_problem_history_and_dedup"),
    ]

    operations = [
        migrations.AlterField(
            model_name="vehicle",
            name="status",
            field=models.CharField(
                choices=[
                    ("active", "Активен"),
                    ("idle", "Техника в простое"),
                    ("repair", "Техника на ремонте"),
                    ("low_fuel", "Низкий уровень топлива"),
                    ("rented", "Техника в аренде"),
                ],
                default="active",
                max_length=20,
                verbose_name="Статус",
            ),
        ),
    ]
