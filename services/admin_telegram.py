import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def _vehicle_info(vehicle) -> str:
    if not vehicle:
        return "Не указана"

    name = None
    number = None

    try:
        from vehicles.serializers import FrontendVehicleSerializer

        frontend = FrontendVehicleSerializer.from_vehicle_obj(vehicle)
        name = frontend.get("name")
        number = frontend.get("number")
    except Exception:
        name = vehicle.name
        number = vehicle.plate_number

    parts = []
    for value in (name, number):
        item = " ".join(str(value or "").split()).strip()
        if item and item not in parts:
            parts.append(item)

    return " ".join(parts) if parts else "Не указана"


def get_admin_telegram_ids() -> list:
    """Возвращает telegram_id всех активных администраторов."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return list(
        User.objects.filter(role='admin', telegram_id__isnull=False)
        .values_list('telegram_id', flat=True)
    )


def send_to_admins(text: str) -> int:
    """
    Отправляет текстовое сообщение всем администраторам через Telegram бот.
    Возвращает количество успешных отправок.
    """
    bot_api_url = getattr(settings, 'BOT_API_URL', None)
    if not bot_api_url:
        logger.warning("BOT_API_URL не настроен — уведомление администратору пропущено")
        return 0

    telegram_ids = get_admin_telegram_ids()
    if not telegram_ids:
        logger.warning("Нет администраторов с telegram_id — уведомление пропущено")
        return 0

    url = f"{bot_api_url.rstrip('/')}/internal/notify-admin"
    success_count = 0

    for telegram_id in telegram_ids:
        try:
            resp = requests.post(
                url,
                json={"telegram_id": telegram_id, "text": text},
                timeout=5,
            )
            resp.raise_for_status()
            success_count += 1
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления администратору {telegram_id}: {e}")

    return success_count


def notify_ignition_on(vehicle, driver, ignition_on_at):
    """Уведомление: водитель включил зажигание."""
    from django.utils import timezone
    local_time = timezone.localtime(ignition_on_at)
    vehicle_info = _vehicle_info(vehicle)
    text = (
        f"🔑 Зажигание включено\n"
        f"🚛 Техника: {vehicle_info}\n"
        f"👤 Водитель: {driver.first_name} {getattr(driver, 'surname', '') or ''}\n"
        f"🕒 {local_time.strftime('%d-%m-%Y %H:%M')}"
    )
    send_to_admins(text)


def notify_shift_started(shift):
    """Уведомление: смена началась."""
    from django.utils import timezone
    local_time = timezone.localtime(shift.started_at)
    vehicle = shift.vehicle
    driver = shift.driver
    vehicle_info = _vehicle_info(vehicle)
    text = (
        f"🟢 Смена началась\n"
        f"🚛 Техника: {vehicle_info}\n"
        f"👤 Водитель: {driver.first_name} {getattr(driver, 'surname', '') or ''}\n"
        f"🕒 {local_time.strftime('%d-%m-%Y %H:%M')}"
    )
    send_to_admins(text)


def notify_shift_ended(shift):
    """Уведомление: смена завершена."""
    from django.utils import timezone
    end_time = shift.ended_at or timezone.now()
    local_time = timezone.localtime(end_time)
    vehicle = shift.vehicle
    driver = shift.driver
    vehicle_info = _vehicle_info(vehicle)
    text = (
        f"🔴 Смена завершена\n"
        f"🚛 Техника: {vehicle_info}\n"
        f"👤 Водитель: {driver.first_name} {getattr(driver, 'surname', '') or ''}\n"
        f"🕒 {local_time.strftime('%d-%m-%Y %H:%M')}"
    )
    send_to_admins(text)


def notify_idle(shift, idle_minutes: int):
    """Уведомление: техника стоит более N минут."""
    vehicle = shift.vehicle
    driver = shift.driver
    vehicle_info = _vehicle_info(vehicle)
    text = (
        f"⚠️ Техника стоит более {idle_minutes} минут\n"
        f"🚛 {vehicle_info}\n"
        f"👤 {driver.first_name} {getattr(driver, 'surname', '') or ''}\n"
        f"⏱ {idle_minutes} минут"
    )
    send_to_admins(text)
