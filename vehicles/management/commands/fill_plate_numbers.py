
from django.core.management.base import BaseCommand
from vehicles.models import Vehicle
from vehicles.serializers import FrontendVehicleSerializer
import re


class Command(BaseCommand):
    help = "Заполняет plate_number: номер если есть, иначе полное название"

    def is_valid_plate(self, plate):
        """
        Проверяет что это реальный номер, а не мусор типа "3" или "330"
        Валидный номер: 01KG622AQ, 08KG669CH и т.д.
        """
        if not plate:
            return False
        
        if len(plate) >= 7 and "KG" in plate.upper():
            return True
        
        return False

    def handle(self, *args, **kwargs):
        updated = 0
        
        for v in Vehicle.objects.all():
            plate = FrontendVehicleSerializer._find_plate(v.name)
            
            if plate and self.is_valid_plate(plate):
                new_value = plate
            else:
                new_value = v.name
            
            if v.plate_number != new_value:
                v.plate_number = new_value
                v.save(update_fields=["plate_number"])
                updated += 1
                self.stdout.write(f"  {v.veh_id}: {new_value}")

        self.stdout.write(self.style.SUCCESS(f"\n✅ Обновлено: {updated}"))
