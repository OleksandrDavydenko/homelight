import requests
import time
import logging
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from config import SHELLY_AUTH_KEY, SHELLY_BASE_URL, TARGET_MAC, TIMEZONE

logger = logging.getLogger(__name__)

# Константи для перевірки напруги
MIN_VOLTAGE = 100  # В
LOW_VOLTAGE = 200  # В (низька напруга)
HIGH_VOLTAGE = 240  # В (висока напруга - небезпечна для приладів)
MAX_RETRY_ATTEMPTS = 5
INITIAL_RETRY_DELAY = 2  # сек
MAX_RETRY_DELAY = 30  # сек
REQUEST_TIMEOUT = 15  # сек
STATE_FILE = "/tmp/homelight_state.json"
OUTAGE_START_FILE = "/tmp/homelight_outage_start.json"
VOLTAGE_STATE_FILE = "/tmp/homelight_voltage_state.json"

# Глобальний стан
_current_state = None
_outage_start_time = None
_previous_voltage_state = None

def get_current_state() -> dict | None:
    global _current_state
    return _current_state

def set_current_state(state: dict) -> None:
    global _current_state
    _current_state = state
    logger.debug(f"Стан оновлено в пам'яті: {state}")

def get_outage_start_time() -> int | None:
    global _outage_start_time
    if _outage_start_time is None:
        _outage_start_time = load_outage_start_time()
    return _outage_start_time

def set_outage_start_time(timestamp: int | None) -> None:
    global _outage_start_time
    _outage_start_time = timestamp
    save_outage_start_time(timestamp)
    if timestamp:
        logger.info(f"Час початку відключення встановлено: {timestamp}")
    else:
        logger.info("Час початку відключення скинуто")

def get_previous_voltage_state() -> str | None:
    global _previous_voltage_state
    if _previous_voltage_state is None:
        _previous_voltage_state = load_voltage_state()
    return _previous_voltage_state

def set_previous_voltage_state(state: str) -> None:
    global _previous_voltage_state
    _previous_voltage_state = state
    save_voltage_state(state)
    logger.debug(f"Стан напруги збережено: {state}")

def load_outage_start_time() -> int | None:
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
    try:
        data = {"outage_start": timestamp}
        with open(OUTAGE_START_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.debug(f"Час початку відключення збережено: {timestamp}")
    except Exception as e:
        logger.exception(f"Помилка при збереженні часу відключення: {e}")

def load_voltage_state() -> str | None:
    try:
        if os.path.exists(VOLTAGE_STATE_FILE):
            with open(VOLTAGE_STATE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                state = data.get("voltage_state")
                if state:
                    logger.debug(f"Стан напруги завантажено: {state}")
                    return state
    except Exception as e:
        logger.exception(f"Помилка при завантаженні стану напруги: {e}")
    return None

def save_voltage_state(state: str) -> None:
    try:
        data = {"voltage_state": state}
        with open(VOLTAGE_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.debug(f"Стан напруги збережено: {state}")
    except Exception as e:
        logger.exception(f"Помилка при збереженні стану напруги: {e}")


class LightChecker:
    def __init__(self):
        self.auth_key = SHELLY_AUTH_KEY
        self.base_url = SHELLY_BASE_URL
        self.target_mac = TARGET_MAC

    def fetch_all_status(self, max_retries=MAX_RETRY_ATTEMPTS):
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
                    
                    if r.status_code == 401:
                        j = r.json()
                        error_msg = j.get("error", "Unauthorized")
                        raise RuntimeError(f"Помилка авторизації (401): {error_msg}")
                    
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
                    raise RuntimeError(f"Не вдалося отримати дані після {max_retries} спроб")

        raise RuntimeError("Помилка: постійний 429 (rate limit). Спробуйте через 1-2 хвилини.")

    def pick_device(self, data: dict):
        devices_status = data.get("devices_status", {})
        
        if not devices_status:
            raise RuntimeError("devices_status порожній")

        if self.target_mac:
            for mac in [self.target_mac.lower(), self.target_mac.upper(), self.target_mac]:
                if mac in devices_status:
                    logger.info(f"Знайдено цільовий пристрій: {mac}")
                    return mac, devices_status[mac]

            logger.warning(f"Не знайдено пристрій з MAC: {self.target_mac}")

        first_mac = next(iter(devices_status.keys()))
        logger.info(f"Використовується пристрій: {first_mac}")
        return first_mac, devices_status[first_mac]

    def _extract_switch_data(self, device_data: dict) -> tuple:
        voltage = None
        power = None
        current_amp = None
        frequency = None
        switch_state = None

        for key, value in device_data.items():
            if key.startswith('switch') and isinstance(value, dict):
                logger.debug(f"Знайдено {key}")
                voltage = value.get('voltage')
                power = value.get('apower')
                current_amp = value.get('current')
                frequency = value.get('freq')
                switch_state = value.get('output')
                break

        return voltage, power, current_amp, frequency, switch_state

    def _determine_has_light(self, voltage, power, current_amp) -> bool | None:
        if voltage is None:
            logger.warning("Не вдалося отримати напругу")
            return None

        if voltage > MIN_VOLTAGE:
            logger.info(f"Напруга більше {MIN_VOLTAGE}В ({voltage} V)")
            return True
        else:
            logger.info(f"Напруга менше {MIN_VOLTAGE}В ({voltage} V)")
            return False

    def _determine_voltage_status(self, voltage) -> str:
        if voltage is None:
            return "unknown"

        if voltage < LOW_VOLTAGE:
            logger.warning(f"Низька напруга: {voltage}В (< {LOW_VOLTAGE}В)")
            return "low"
        elif voltage > HIGH_VOLTAGE:
            logger.warning(f"Висока напруга: {voltage}В (> {HIGH_VOLTAGE}В)")
            return "high"
        else:
            logger.info(f"Нормальна напруга: {voltage}В")
            return "normal"

    def analyze_device_data(self, mac: str, device_data: dict):
        logger.info(f"Аналіз даних для пристрою {mac}...")

        current_time = int(time.time())
        sys_info = device_data.get('sys', {})
        online = sys_info.get('mac') is not None

        if not online:
            logger.warning(f"Пристрій {mac} не знайдено або OFFLINE")
            return {
                "has_light": False,
                "online": False,
                "reason": "device_offline",
                "voltage_status": "unknown",
                "voltage": None,
                "last_update_time": current_time,
                "mac": mac
            }

        voltage, power, current_amp, frequency, switch_state = self._extract_switch_data(device_data)
        has_light = self._determine_has_light(voltage, power, current_amp)
        voltage_status = self._determine_voltage_status(voltage)

        logger.info(f"Підсумок: світло {'Є' if has_light else 'Немає' if has_light is False else 'Невідомо'}, напруга: {voltage_status}")

        return {
            "has_light": has_light,
            "online": True,
            "voltage": voltage,
            "voltage_status": voltage_status,
            "power": power,
            "current": current_amp,
            "frequency": frequency,
            "switch_state": switch_state,
            "last_update_time": sys_info.get('last_sync_ts', current_time),
            "mac": mac
        }

    def get_real_device_status(self):
        logger.info("Початок перевірки")

        try:
            data = self.fetch_all_status()
            
            if "devices_status" not in data:
                logger.error(f"В даних відсутній ключ 'devices_status'. Отримані ключі: {list(data.keys())}")
                
                if not data.get("isok", True):
                    error_msg = data.get("error", "Unknown API error")
                    return {
                        "has_light": False,
                        "online": False,
                        "reason": "api_error",
                        "voltage_status": "unknown",
                        "voltage": None,
                        "last_update_time": int(time.time())
                    }
                
                return {
                    "has_light": False,
                    "online": False,
                    "reason": "no_devices",
                    "voltage_status": "unknown",
                    "voltage": None,
                    "last_update_time": int(time.time())
                }
            
            mac, device_data = self.pick_device(data)
            logger.info(f"Обрано пристрій: {mac}")
            return self.analyze_device_data(mac, device_data)

        except RuntimeError as e:
            error_msg = str(e)
            logger.error(f"Помилка: {error_msg}")
            
            return {
                "has_light": False,
                "online": False,
                "reason": "error",
                "voltage_status": "unknown",
                "voltage": None,
                "last_update_time": int(time.time())
            }
                
        except Exception as e:
            logger.exception(f"Виняток: {type(e).__name__}: {str(e)}")
            return {
                "has_light": False,
                "online": False,
                "reason": "error",
                "voltage_status": "unknown",
                "voltage": None,
                "last_update_time": int(time.time())
            }

    def get_last_update_time(self, timestamp: int) -> str:
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

        if dt_local.date() == now_dt.date():
            time_str = dt_local.strftime("сьогодні о %H:%M")
        elif dt_local.date() == (now_dt - timedelta(days=1)).date():
            time_str = dt_local.strftime("вчора о %H:%M")
        else:
            time_str = dt_local.strftime("%d.%m о %H:%M")

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

    def check_voltage_change(self, voltage_status: str, voltage_value: float) -> str | None:
        """Перевіряє зміну стану напруги та повертає повідомлення"""
        previous_state = get_previous_voltage_state()
        current_state = voltage_status
        
        # Якщо стан змінився
        if previous_state and previous_state != current_state:
            # Оновлюємо стан напруги
            set_previous_voltage_state(current_state)
            
            # Формуємо повідомлення про зміну напруги
            if current_state == "low":
                return f"⚠️ УВАГА! Низька напруга: {voltage_value:.1f} В\n(норма: 200-240 В)"
            elif current_state == "high":
                return f"⚠️ УВАГА! Висока напруга: {voltage_value:.1f} В\n(норма: 200-240 В)"
            elif current_state == "normal":
                return f"✅ Напруга в нормі: {voltage_value:.1f} В"
            elif current_state == "unknown":
                return "❓ Стан напруги невідомий"
        
        # Якщо це перша перевірка, просто зберігаємо стан
        elif previous_state is None:
            set_previous_voltage_state(current_state)
            
        return None

    def check_light_status(self) -> str:
        """Основна функція для перевірки статусу світла (для кнопки check)"""
        logger.info("Початок перевірки світла")

        status = self.get_real_device_status()
        last_update_time = status.get("last_update_time", 0)
        time_info = self.get_last_update_time(last_update_time)

        voltage = status.get("voltage")
        frequency = status.get("frequency")
        voltage_status = status.get("voltage_status", "unknown")
        has_light = status.get("has_light")
        online = status.get("online")

        try:
            tz = ZoneInfo(TIMEZONE)
            check_time = datetime.now(tz).strftime("%d.%m.%Y %H:%M:%S")
        except Exception:
            check_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

        header = f"📊 РЕЗУЛЬТАТ ПЕРЕВІРКИ: {check_time}\n\n"

        current_time = int(time.time())
        outage_start = get_outage_start_time()
        
        if not online or has_light is False:
            if outage_start is None:
                set_outage_start_time(current_time)
                outage_duration_info = ""
            else:
                outage_duration = current_time - outage_start
                duration_str = self.format_duration(outage_duration)
                outage_duration_info = f"⏱️ Час відключення: {duration_str}\n"
            
            if time_info:
                result = f"{header}❌ СВІТЛА НЕМАЄ\n\n{outage_duration_info}{time_info}"
            else:
                result = f"{header}❌ СВІТЛА НЕМАЄ\n\n{outage_duration_info}".strip()
                
        elif has_light is True:
            voltage_display = f"{voltage:.1f} В" if voltage is not None else "–"
            frequency_display = f"{frequency:.1f} Гц" if frequency is not None else "–"
            
            details = f"🔌 Напруга: {voltage_display}\n〰️  Частота: {frequency_display}"
            
            if time_info:
                details += f"\n{time_info}"
            
            outage_duration_info = ""
            if outage_start:
                outage_duration = current_time - outage_start
                if outage_duration > 60:
                    duration_str = self.format_duration(outage_duration)
                    outage_duration_info = f"\n\n💡 Світла не було: {duration_str}"
                
                set_outage_start_time(None)
            
            result = f"{header}✅ СВІТЛО Є\n\n{details}{outage_duration_info}"
            
            if voltage_status == "low" and voltage is not None:
                result += f"\n\n⚠️ УВАГА! Низька напруга ({voltage:.0f} В)"
            elif voltage_status == "high" and voltage is not None:
                result += f"\n\n⚠️ УВАГА! Висока напруга ({voltage:.0f} В)"
                
        else:
            result = f"{header}❓ СТАН НЕВІДОМИЙ"

        return result

    def check_status_with_alerts(self) -> tuple:
        """
        Перевіряє статус та повертає кортеж:
        (основне_повідомлення, повідомлення_про_напругу, повідомлення_про_світло)
        
        Примітка: основне повідомлення використовується тільки для кнопки check,
        для автоматичних перевірок відправляємо тільки алерти
        """
        logger.info("Перевірка статусу з алертами")

        status = self.get_real_device_status()
        
        voltage = status.get("voltage")
        voltage_status = status.get("voltage_status", "unknown")
        has_light = status.get("has_light")
        online = status.get("online")
        
        current_time = int(time.time())
        outage_start = get_outage_start_time()
        
        # Змінні для повідомлень
        voltage_alert = None
        light_alert = None
        
        # 1. Перевіряємо зміну напруги (відправляємо алерт ТІЛЬКИ при зміні стану)
        voltage_alert = self.check_voltage_change(voltage_status, voltage)
        
        # 2. Перевіряємо зміну стану світла
        if not online or has_light is False:
            # Світла немає
            if outage_start is None:
                # Перший раз, коли світло пропало
                set_outage_start_time(current_time)
                light_alert = "🔴 СВІТЛО ПРОПАЛО!"
                
                # Якщо пристрій offline, додаємо тривалість
                if not online:
                    duration = 1  # Починаємо відлік
                    duration_str = self.format_duration(duration)
                    light_alert += f"\n\n⏱️ Час відключення: {duration_str}"
            
            else:
                # Світло вже відсутнє - оновлюємо повідомлення про тривалість
                outage_duration = current_time - outage_start
                if outage_duration % 300 == 0:  # Кожні 5 хвилин
                    duration_str = self.format_duration(outage_duration)
                    light_alert = f"🔴 СВІТЛА НЕМАЄ\n\n⏱️ Час відключення: {duration_str}"
                    
        elif has_light is True:
            # Світло є
            if outage_start is not None:
                # Світло повернулося після відключення
                outage_duration = current_time - outage_start
                duration_str = self.format_duration(outage_duration)
                
                # Додаємо інформацію про напругу, якщо вона є
                voltage_info = ""
                if voltage is not None:
                    voltage_info = f"\n🔌 Напруга: {voltage:.1f} В"
                
                light_alert = f"🟢 СВІТЛО ПОВЕРНУЛОСЬ!\n\n⏱️ Час відключення: {duration_str}{voltage_info}"
                
                set_outage_start_time(None)
        
        # 3. Отримуємо основне повідомлення (тільки для кнопки check)
        main_message = self.check_light_status()
        
        return main_message, voltage_alert, light_alert

# Приклад використання:
# 
# Для автоматичної перевірки (кожні 1-2 хвилини):
# light_checker = LightChecker()
# _, voltage_alert, light_alert = light_checker.check_status_with_alerts()
# 
# if voltage_alert:
#     # Відправляємо ТІЛЬКИ це повідомлення про зміну напруги
#     await bot.send_message(chat_id, voltage_alert)
# 
# if light_alert:
#     # Відправляємо ТІЛЬКИ це повідомлення про зміну стану світла
#     await bot.send_message(chat_id, light_alert)
# 
# # Для кнопки "check" (користувач запитує):
# check_result = light_checker.check_light_status()
# await bot.send_message(chat_id, check_result)