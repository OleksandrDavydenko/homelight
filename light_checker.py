import requests
import time
import logging
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from config import SHELLY_AUTH_KEY, SHELLY_BASE_URL, TARGET_MAC, TIMEZONE

logger = logging.getLogger(__name__)

# Константи
MIN_VOLTAGE = 100  # Мінімальна напруга для вважання що світло є
MAX_RETRY_ATTEMPTS = 5
INITIAL_RETRY_DELAY = 2  # сек
MAX_RETRY_DELAY = 30  # сек
REQUEST_TIMEOUT = 15  # сек
OUTAGE_START_FILE = "/tmp/homelight_outage_start.json"

# Глобальний стан для зворотної сумісності
_outage_start_time = None
_current_state = {}  # Для get_current_state/set_current_state


def get_outage_start_time() -> int | None:
    """Отримати час початку відключення"""
    global _outage_start_time
    if _outage_start_time is None:
        _outage_start_time = load_outage_start_time()
    return _outage_start_time


def set_outage_start_time(timestamp: int | None) -> None:
    """Встановити час початку відключення"""
    global _outage_start_time
    _outage_start_time = timestamp
    save_outage_start_time(timestamp)
    if timestamp:
        logger.info(f"Час початку відключення встановлено: {timestamp}")
    else:
        logger.info("Час початку відключення скинуто")


def load_outage_start_time() -> int | None:
    """Завантажити час початку відключення з файлу"""
    try:
        if os.path.exists(OUTAGE_START_FILE):
            with open(OUTAGE_START_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                timestamp = data.get("outage_start")
                if timestamp:
                    logger.debug(f"Час початку відключення завантажено: {timestamp}")
                    return timestamp
    except Exception as e:
        logger.exception(f"Помилка при завантаженні часу відключення: {e}")
    return None


def save_outage_start_time(timestamp: int | None) -> None:
    """Зберегти час початку відключення у файл"""
    try:
        data = {"outage_start": timestamp}
        with open(OUTAGE_START_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.debug(f"Час початку відключення збережено: {timestamp}")
    except Exception as e:
        logger.exception(f"Помилка при збереженні часу відключення: {e}")


# Функції для зворотної сумісності з broadcaster.py
def get_current_state() -> dict:
    """Отримати поточний стан (для зворотної сумісності)"""
    global _current_state
    return _current_state


def set_current_state(state: dict) -> None:
    """Зберегти поточний стан (для зворотної сумісності)"""
    global _current_state
    _current_state = state


class LightChecker:
    def __init__(self):
        self.auth_key = SHELLY_AUTH_KEY
        self.base_url = SHELLY_BASE_URL
        self.target_mac = TARGET_MAC

    def fetch_all_status(self, max_retries=MAX_RETRY_ATTEMPTS):
        """Отримання статусу всіх пристроїв з Shelly Cloud"""
        url = f"{self.base_url}/device/all_status"
        payload = {"auth_key": self.auth_key}

        delay = INITIAL_RETRY_DELAY
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Спроба {attempt}/{max_retries} отримати дані з Shelly API...")
                r = requests.post(url, data=payload, timeout=REQUEST_TIMEOUT)

                if r.status_code == 429:
                    logger.warning(f"429 Too many requests. Чекаю {delay}s...")
                    time.sleep(delay)
                    delay = min(delay * 2, MAX_RETRY_DELAY)
                    continue

                if r.status_code != 200:
                    logger.error(f"HTTP помилка: {r.status_code}, тіло: {r.text[:300]}")
                    r.raise_for_status()

                j = r.json()
                if not j.get("isok"):
                    error_msg = j.get("error", "Unknown API error")
                    raise RuntimeError(f"Shelly API помилка: {error_msg}")

                logger.info("Дані з Shelly API успішно отримано")
                return j["data"]

            except requests.exceptions.RequestException as e:
                logger.error(f"Помилка запиту: {e}")
                if attempt < max_retries:
                    logger.info(f"Повторна спроба через {delay} сек...")
                    time.sleep(delay)
                    delay = min(delay * 2, MAX_RETRY_DELAY)
                else:
                    raise RuntimeError(f"Не вдалося отримати дані")

        raise RuntimeError("Не вдалося отримати дані")

    def pick_device(self, data: dict):
        """Вибір цільового пристрою з отриманих даних"""
        devices_status = data.get("devices_status", {})
        
        if not devices_status:
            raise RuntimeError("Немає пристроїв")

        # Якщо вказано конкретний MAC
        if self.target_mac:
            for mac in [self.target_mac.lower(), self.target_mac.upper(), self.target_mac]:
                if mac in devices_status:
                    logger.info(f"Знайдено пристрій: {mac}")
                    return mac, devices_status[mac]

            logger.warning(f"Не знайдено пристрій з MAC: {self.target_mac}")
            raise RuntimeError("Пристрій не знайдено")

        # Беремо перший пристрій
        first_mac = next(iter(devices_status.keys()))
        logger.info(f"Використовується пристрій: {first_mac}")
        return first_mac, devices_status[first_mac]

    def _extract_switch_data(self, device_data: dict) -> tuple:
        """Витяг даних про напругу з пристрою"""
        voltage = None
        frequency = None

        for key, value in device_data.items():
            if key.startswith('switch') and isinstance(value, dict):
                voltage = value.get('voltage')
                frequency = value.get('freq')
                break

        return voltage, frequency

    def _determine_has_light(self, voltage) -> bool:
        """Визначення наявності світла на основі напруги"""
        if voltage is None:
            logger.warning("Не вдалося отримати напругу")
            return False

        if voltage > MIN_VOLTAGE:
            logger.info(f"Напруга {voltage} В - світло Є")
            return True
        else:
            logger.info(f"Напруга {voltage} В - світла НЕМАЄ")
            return False

    def get_device_status(self):
        """Отримання статусу пристрою"""
        logger.info("Перевірка статусу")

        try:
            # Отримуємо дані
            data = self.fetch_all_status()
            
            # Перевіряємо чи є пристрої
            if "devices_status" not in data:
                logger.error("Немає даних про пристрої")
                return {
                    "has_light": False,
                    "voltage": None,
                    "last_update_time": int(time.time())
                }
            
            # Вибираємо пристрій
            mac, device_data = self.pick_device(data)
            
            # Аналізуємо дані
            current_time = int(time.time())
            voltage, frequency = self._extract_switch_data(device_data)
            has_light = self._determine_has_light(voltage)
            
            return {
                "has_light": has_light,
                "voltage": voltage,
                "frequency": frequency,
                "last_update_time": current_time
            }

        except Exception as e:
            logger.error(f"Помилка: {e}")
            # Якщо будь-яка помилка - світла немає
            return {
                "has_light": False,
                "voltage": None,
                "last_update_time": int(time.time())
            }

    # Метод для зворотної сумісності з broadcaster.py
    def get_real_device_status(self):
        """Альтернативна назва для get_device_status (для зворотної сумісності)"""
        return self.get_device_status()

    def get_last_update_time(self, timestamp: int) -> str:
        """Отримує час останнього оновлення"""
        if not timestamp or timestamp == 0:
            return ""

        try:
            tz = ZoneInfo(TIMEZONE)
            dt = datetime.fromtimestamp(timestamp)
            dt_local = dt.astimezone(tz)
            now_dt = datetime.now(tz)
        except Exception:
            dt_local = datetime.fromtimestamp(timestamp)
            now_dt = datetime.now()

        diff = now_dt - dt_local
        diff_seconds = int(diff.total_seconds())

        # Форматуємо дату
        if dt_local.date() == now_dt.date():
            time_str = dt_local.strftime("сьогодні о %H:%M")
        elif dt_local.date() == (now_dt - timedelta(days=1)).date():
            time_str = dt_local.strftime("вчора о %H:%M")
        else:
            time_str = dt_local.strftime("%d.%m о %H:%M")

        # Додаємо скільки часу тому
        if diff_seconds < 60:
            ago = "щойно"
        elif diff_seconds < 3600:
            minutes = diff_seconds // 60
            ago = f"{minutes} хв тому"
        elif diff_seconds < 86400:
            hours = diff_seconds // 3600
            minutes = (diff_seconds % 3600) // 60
            ago = f"{hours} год {minutes} хв тому"
        else:
            days = diff_seconds // 86400
            ago = f"{days} дн тому"

        return f"🕒 {time_str} ({ago})"

    def format_duration(self, seconds: int) -> str:
        """Форматує тривалість у зрозумілий формат"""
        if seconds < 60:
            return f"{seconds} секунд"
        elif seconds < 3600:
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes} хв {secs} сек"
        elif seconds < 86400:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours} год {minutes} хв"
        else:
            days = seconds // 86400
            hours = (seconds % 86400) // 3600
            return f"{days} дн {hours} год"

    def check_light_status(self) -> str:
        """Основна функція для перевірки статусу світла (для кнопки check)"""
        logger.info("Перевірка світла")

        status = self.get_device_status()
        last_update_time = status.get("last_update_time", 0)
        time_info = self.get_last_update_time(last_update_time)

        # Отримуємо дані
        voltage = status.get("voltage")
        frequency = status.get("frequency")
        has_light = status.get("has_light")

        # Отримуємо поточний час
        current_time = int(time.time())
        
        # Отримуємо час початку відключення
        outage_start = get_outage_start_time()
        
        # Перевіряємо чи є світло
        if has_light:
            # Світло Є
            
            # Формуємо деталі
            voltage_display = f"{voltage:.1f} В" if voltage is not None else "–"
            frequency_display = f"{frequency:.1f} Гц" if frequency is not None else "–"
            
            details = f"🔌 Напруга: {voltage_display}\n〰️  Частота: {frequency_display}"
            
            # Додаємо інформацію про час
            if time_info:
                details += f"\n{time_info}"
            
            # Перевіряємо чи було відключення
            outage_duration_info = ""
            if outage_start:
                outage_duration = current_time - outage_start
                if outage_duration > 60:
                    duration_str = self.format_duration(outage_duration)
                    outage_duration_info = f"\n\n💡 Світла не було: {duration_str}"
                
                # Скидаємо час початку відключення
                set_outage_start_time(None)
            
            result = f"✅ СВІТЛО Є\n\n{details}{outage_duration_info}"
                
        else:
            # Світла НЕМАЄ
            current_time = int(time.time())
            
            if outage_start is None:
                # Перше повідомлення про відключення
                set_outage_start_time(current_time)
                outage_duration_info = ""
            else:
                # Відключення триває
                outage_duration = current_time - outage_start
                duration_str = self.format_duration(outage_duration)
                outage_duration_info = f"⏱️ Час відключення: {duration_str}\n"
            
            # Формуємо повідомлення
            if time_info:
                result = f"❌ СВІТЛА НЕМАЄ\n\n{outage_duration_info}{time_info}"
            else:
                result = f"❌ СВІТЛА НЕМАЄ\n\n{outage_duration_info}".strip()

        return result

    def check_for_alerts(self) -> str | None:
        """
        Перевіряє статус світла та повертає повідомлення тільки при зміні стану.
        Повертає None якщо стан не змінився.
        """
        logger.info("Перевірка для алертів")

        status = self.get_device_status()
        
        has_light = status.get("has_light")
        
        current_time = int(time.time())
        outage_start = get_outage_start_time()
        
        # Перевіряємо зміну стану
        if has_light:
            # Світло Є
            if outage_start is not None:
                # Світло повернулося після відключення
                outage_duration = current_time - outage_start
                duration_str = self.format_duration(outage_duration)
                
                # Скидаємо час початку відключення
                set_outage_start_time(None)
                
                return f"🟢 СВІТЛО ПОВЕРНУЛОСЬ!\n\n⏱️ Час відключення: {duration_str}"
        
        else:
            # Світла НЕМАЄ
            if outage_start is None:
                # Перший раз, коли світло пропало
                set_outage_start_time(current_time)
                return "🔴 СВІТЛО ПРОПАЛО!"
        
        # Якщо стан не змінився
        return None