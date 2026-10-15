from django.db import migrations, models
from django.db.models import Count, F, Q


def collapse_duplicate_active_shifts(apps, schema_editor):
    DriverShift = apps.get_model("vehicles", "DriverShift")

    active_shifts = DriverShift.objects.filter(status="active", ended_at__isnull=True)

    duplicate_driver_ids = (
        active_shifts
        .values("driver_id")
        .annotate(cnt=Count("id"))
        .filter(cnt__gt=1)
        .values_list("driver_id", flat=True)
    )
    for driver_id in duplicate_driver_ids:
        same_driver_qs = DriverShift.objects.filter(
            driver_id=driver_id,
            status="active",
            ended_at__isnull=True,
        ).order_by("-started_at", "-id")
        keep_shift = same_driver_qs.first()
        if keep_shift:
            same_driver_qs.exclude(id=keep_shift.id).update(
                status="completed",
                ended_at=F("started_at"),
            )

    duplicate_vehicle_ids = (
        DriverShift.objects
        .filter(status="active", ended_at__isnull=True, vehicle_id__isnull=False)
        .values("vehicle_id")
        .annotate(cnt=Count("id"))
        .filter(cnt__gt=1)
        .values_list("vehicle_id", flat=True)
    )
    for vehicle_id in duplicate_vehicle_ids:
        same_vehicle_qs = DriverShift.objects.filter(
            vehicle_id=vehicle_id,
            status="active",
            ended_at__isnull=True,
        ).order_by("-started_at", "-id")
        keep_shift = same_vehicle_qs.first()
        if keep_shift:
            same_vehicle_qs.exclude(id=keep_shift.id).update(
                status="completed",
                ended_at=F("started_at"),
            )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("vehicles", "0005_drivershift_trips_note"),
    ]

    operations = [
        migrations.RunPython(collapse_duplicate_active_shifts, noop_reverse),
        migrations.AddConstraint(
            model_name="drivershift",
            constraint=models.UniqueConstraint(
                fields=("driver",),
                condition=Q(ended_at__isnull=True, status="active"),
                name="uniq_active_shift_per_driver",
            ),
        ),
        migrations.AddConstraint(
            model_name="drivershift",
            constraint=models.UniqueConstraint(
                fields=("vehicle",),
                condition=Q(ended_at__isnull=True, status="active", vehicle__isnull=False),
                name="uniq_active_shift_per_vehicle",
            ),
        ),
    ]
