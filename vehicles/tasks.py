from celery import shared_task
from django.core.management import call_command

@shared_task
def run_sync_vehicles():
    """
    Периодическая задача для синхронизации транспорта.
    Запускает команду управления: python manage.py sync_vehicles
    """
    call_command("sync_vehicles")
    return "Vehicles synced successfully"