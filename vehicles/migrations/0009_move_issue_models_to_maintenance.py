from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("vehicles", "0008_alter_vehicle_status"),
        ("maintenance", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="IssueReportPhoto"),
                migrations.DeleteModel(name="IssueReportProblem"),
                migrations.DeleteModel(name="IssueReport"),
            ],
        ),
    ]
