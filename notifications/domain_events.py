from dataclasses import dataclass
from typing import Iterable
from vehicles.models import Vehicle

LOW_FUEL_THRESHOLD = 20
OIL_THRESHOLD = 500


@dataclass
class SystemEvent:
    type: str
    vehicle: Vehicle


def collect_system_events() -> list[SystemEvent]:
    """
    ЕДИНАЯ точка сбора системных событий.
    Используется:
    - Celery tasks
    """

    events: list[SystemEvent] = []

    low_fuel_vehicles = Vehicle.objects.filter(
        fuel__lt=LOW_FUEL_THRESHOLD
    )
    for v in low_fuel_vehicles:
        events.append(
            SystemEvent(type="low_fuel", vehicle=v)
        )

    oil_service_vehicles = Vehicle.objects.filter(
        to_remaining__lte=OIL_THRESHOLD
    )
    for v in oil_service_vehicles:
        events.append(
            SystemEvent(type="oil_service", vehicle=v)
        )

    return events
