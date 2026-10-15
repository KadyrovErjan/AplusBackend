import aiohttp
import asyncio
import json
import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

BASE_URL = settings.PILOT_API_URL
LOGIN = settings.PILOT_LOGIN
PASSWORD = settings.PILOT_PASSWORD

_session = None
_semaphore = None


def get_semaphore():
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(5)
    return _semaphore


async def get_session():
    global _session
    if _session is None or _session.closed:
        timeout = aiohttp.ClientTimeout(total=20)
        auth = aiohttp.BasicAuth(LOGIN, PASSWORD)
        _session = aiohttp.ClientSession(auth=auth, timeout=timeout)
    return _session


async def pilot_request(cmd: str, node: int = 14, params: dict | None = None) -> dict:
    """Асинхронный запрос для Polling Worker"""
    params = params or {}
    query = {"cmd": cmd, "node": node, **params}

    sem = get_semaphore()

    async with sem:
        session = await get_session()
        try:
            async with session.get(BASE_URL, params=query) as resp:
                text = await resp.text()
                if text.startswith("\ufeff"):
                    text = text.replace("\ufeff", "", 1)

                if resp.status != 200:
                    logger.error(f"PILOT HTTP Error: {resp.status}")
                    raise RuntimeError(f"PILOT HTTP {resp.status}")

                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON from PILOT: {text[:100]}")
                    raise RuntimeError("Invalid JSON from PILOT")

        except asyncio.TimeoutError:
            raise RuntimeError("PILOT timeout")
        except aiohttp.ClientError as e:
            raise RuntimeError(f"PILOT network error: {e}")


def pilot_request_sync(cmd: str, node: int = 14, params: dict | None = None) -> dict:
    """Синхронный запрос для старых management commands"""
    params = params or {}
    query = {"cmd": cmd, "node": node, **params}

    try:
        resp = requests.get(
            BASE_URL,
            params=query,
            auth=(LOGIN, PASSWORD),
            timeout=20,
        )
        resp.raise_for_status()

        text = resp.text
        if text.startswith("\ufeff"):
            text = text.replace("\ufeff", "", 1)

        try:
            return resp.json()
        except Exception:
            return {}

    except Exception as e:
        logger.error(f"PILOT SYNC ERROR: {e}")
        return {}


class PilotClient:
    """
    Класс-клиент для работы со сложными отчетами Incomtek.
    Использует глобальные настройки из settings.
    """

    def __init__(self):
        self.base_url = BASE_URL
        self.login = LOGIN
        self.password = PASSWORD

    def _make_request(self, payload: dict):
        """
        Универсальный метод запроса (синхронный).
        """
        try:
            response = requests.post(
                self.base_url,
                data=payload,
                auth=(self.login, self.password),
                timeout=45
            )
            response.raise_for_status()

            text = response.text
            if text.startswith("\ufeff"):
                text = text.replace("\ufeff", "", 1)

            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                logger.error(f"Pilot API returned non-JSON: {text[:200]}")
                return {"success": False, "msg": "Invalid JSON response"}

            if not isinstance(data, dict):
                logger.error(f"Pilot API returned unexpected JSON type: {type(data).__name__}")
                return {"success": False, "msg": "Unexpected JSON format"}

            if data.get('code', 0) != 0 and not data.get('success'):
                msg = data.get('msg', 'Unknown error')
                logger.error(f"Pilot API Logical Error: {msg}")
                return {"success": False, "msg": msg}

            if 'success' not in data and data.get('code') == 0:
                data['success'] = True

            return data

        except requests.RequestException as e:
            logger.error(f"Pilot API Connection Error: {e}")
            return {"success": False, "msg": str(e)}

    def _make_get_request(self, params: dict):
        """
        GET-запрос для API методов, где контракт явно query-based (например cmd=fuel).
        """
        try:
            response = requests.get(
                self.base_url,
                params=params,
                auth=(self.login, self.password),
                timeout=45,
            )
            response.raise_for_status()

            text = response.text
            if text.startswith("\ufeff"):
                text = text.replace("\ufeff", "", 1)

            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                logger.error(f"Pilot API returned non-JSON: {text[:200]}")
                return {"success": False, "msg": "Invalid JSON response"}

            if not isinstance(data, dict):
                logger.error(f"Pilot API returned unexpected JSON type: {type(data).__name__}")
                return {"success": False, "msg": "Unexpected JSON format"}

            if data.get("code", 0) != 0 and not data.get("success"):
                msg = data.get("msg", "Unknown error")
                logger.error(f"Pilot API Logical Error: {msg}")
                return {"success": False, "msg": msg}

            if "success" not in data and data.get("code") == 0:
                data["success"] = True

            return data

        except requests.RequestException as e:
            logger.error(f"Pilot API Connection Error: {e}")
            return {"success": False, "msg": str(e)}

    def get_report(self, report_type: int, veh_id: str, start: int, stop: int, explode: int = 1, group: int = 2):
        """
        Получение отчета.
        :param report_type: 4 (Mileage), 60 (Trips between zones)
        :param veh_id: Строка ID через запятую "123,456"
        """
        payload = {
            "cmd": "get_report",
            "report_type": report_type,
            "start": start,
            "stop": stop,
            "veh_id": veh_id,
            "explode": explode,
            "group": group,
            "format": "json",
            "tz": "Asia/Bishkek",
            "node": 1
        }
        return self._make_request(payload)

    def get_route_report(self, veh_id: str, start: int, stop: int, zone_from_id: int, zone_to_id: int):
        """
        Получение отчета 'Trips with geozones' (report_type=173).
        Возвращает рейсы строго между двумя указанными зонами.
        """
        payload = {
            "cmd": "get_report",
            "report_type": 173,
            "start": start,
            "stop": stop,
            "veh_id": veh_id,
            "zones_id": f"{zone_from_id},{zone_to_id}",
            "explode": 1,
            "group": 2,
            "format": "json",
            "tz": "Asia/Bishkek",
            "node": 1
        }
        return self._make_request(payload)

    def get_mileage_report(self, veh_id: str, start: int, stop: int):
        """Получить Mileage Report (report_type=4) для точного пробега."""
        return self.get_report(report_type=4, veh_id=veh_id, start=start, stop=stop)

    def get_refuels_report(self, veh_id: str, start: int, stop: int):
        """Получить отчет 'Refuels' (report_type=38). Только подтвержденные заправки."""
        return self.get_report(report_type=38, veh_id=veh_id, start=start, stop=stop)

    def get_fuel_report(self, imei: str, start: int, stop: int, addr: int = 1, node: int = 1):
        """
        Получить топливный отчет cmd=fuel.
        Возвращает cards / fillings / fuel (дневные остатки).
        """
        params = {
            "cmd": "fuel",
            "imei": imei,
            "start": start,
            "stop": stop,
            "addr": addr,
            "node": node,
        }
        return self._make_get_request(params)

    def get_sensors_report(self, imei: str, start: int, stop: int, node: int = 1):
        """
        Получить периоды работы датчиков cmd=sensors.
        Используется для точного времени включения зажигания по ts.
        """
        params = {
            "cmd": "sensors",
            "imei": imei,
            "start": start,
            "stop": stop,
            "node": node,
        }
        return self._make_get_request(params)

    def get_vehicles_list(self):
        return self._make_request({"cmd": "list", "node": 1})
