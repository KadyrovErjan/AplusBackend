from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("vehicles", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="drivershift",
            name="late_minutes",
            field=models.PositiveIntegerField(
                default=0,
                help_text="На сколько минут фактический старт движения позже 09:30",
                verbose_name="Опоздание (мин)",
            ),
        ),
        migrations.AddField(
            model_name="drivershift",
            name="movement_started_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Время, когда техника реально начала движение (по GPS порогу старта)",
                null=True,
                verbose_name="Фактическое начало движения",
            ),
        ),
        migrations.AddField(
            model_name="drivershift",
            name="worked_norm_minutes",
            field=models.PositiveIntegerField(
                default=480,
                help_text="Норма 8 часов (480 мин) минус опоздание",
                verbose_name="Учтено рабочих минут",
            ),
        ),
    ]
