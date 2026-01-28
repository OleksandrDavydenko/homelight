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
LOW_VOLTAGE = 195  # В (низька напруга)
HIGH_VOLTAGE = 245  # В (висока напруга - небезпечна для приладів)
MAX_RETRY_ATTEMPTS = 5
INITIAL_RETRY_DELAY = 2  # сек
MAX_RETRY_DELAY = 30  # сек
REQUEST_TIMEOUT = 15  # сек
STATE_FILE = "/tmp/homelight_state.json"
OUTAGE_START_FILE = "/tmp/homelight_outage_start.json"
VOLTAGE_ANOMALY_FILE = "/tmp/homelight_voltage_anomaly.json"

# Глобальний стан для зберігання протягом роботи dyno
_current_state = None
_outage_start_time = None
_voltage_anomaly_start = None

def get_current_state() -> dict | None:
    """Отримати поточний стан зі змінної"""
    global _current_state
    return _current_state

def set_current_state(state: dict) -> None:
    """Зберегти поточний стан у змінну"""
    global _current_state
    _current_state = state
    logger.debug(f"Стан оновлено в пам'яті: {state}")

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

def get_voltage_anomaly_start() -> dict | None:
    """Отримати час початку аномалії напруги"""
    global _voltage_anomaly_start
    if _voltage_anomaly_start is None:
        _voltage_anomaly_start = load_voltage_anomaly_start()
    return _voltage_anomaly_start

def set_voltage_anomaly_start(data: dict | None) -> None:
    """Встановити час початку аномалії напруги"""
    global _voltage_anomaly_start
    _voltage_anomaly_start = data
    save_voltage_anomaly_start(data)
    if data:
        logger.info(f"Час початку аномалії напруги встановлено: {data}")
    else:
        logger.info("Час початку аномалії напруги скинуто")

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

def load_voltage_anomaly_start() -> dict | None:
    """Завантажити час початку аномалії напруги з файлу"""
    try:
        if os.path.exists(VOLTAGE_ANOMALY_FILE):
            with open(VOLTAGE_ANOMALY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logger.debug(f"Дані аномалії напруги завантажено: {data}")
                return data
    except Exception as e:
        logger.exception(f"Помилка при завантаженні часу аномалії напруги: {e}")
    return None

def save_voltage_anomaly_start(data: dict | None) -> None:
    """Зберегти час початку аномалії напруги у файл"""
    try:
        if data is None:
            data = {"anomaly_start": None, "anomaly_type": None}
        
        with open(VOLTAGE_ANOMALY_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.debug(f"Дані аномалії напруги збережено: {data}")
    except Exception as e:
        logger.exception(f"Помилка при збереженні часу аномалії напруги: {e}")


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
        """Вибір цільового пристрою з отриманих даних"""
        devices_status = data.get("devices_status", {})
        
        if not devices_status:
            raise RuntimeError("devices_status порожній")

        # Якщо вказано конкретний MAC
        if self.target_mac:
            for mac in [self.target_mac.lower(), self.target_mac.upper(), self.target_mac]:
                if mac in devices_status:
                    logger.info(f"Знайдено цільовий пристрій: {mac}")
                    return mac, devices_status[mac]

            logger.warning(f"Не знайдено пристрій з MAC: {self.target_mac}")

        # Беремо перший пристрій
        first_mac = next(iter(devices_status.keys()))
        logger.info(f"Використовується пристрій: {first_mac}")
        return first_mac, devices_status[first_mac]

    def _extract_switch_data(self, device_data: dict) -> tuple:
        """Витяг даних про напругу, потужність та інше зі switch пристрою"""
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

    def _determine_has_light(self, voltage, power, current_amp) -> bool:
        """Визначення наявності світла на основі параметрів"""
        if voltage is None:
            logger.warning("Не вдалося отримати напругу - вважаємо що світла немає")
            return False  # Якщо немає даних про напругу - світла немає

        if voltage > MIN_VOLTAGE:
            logger.info(f"Напруга більше {MIN_VOLTAGE}В ({voltage} V)")
            return True
        else:
            logger.info(f"Напруга менше {MIN_VOLTAGE}В ({voltage} V)")
            return False

    def _determine_voltage_status(self, voltage) -> str:
        """Визначення статусу напруги: normal, low, high"""
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
        """Аналіз даних пристрою та визначення статусу світла"""
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
                "last_update_time": current_time,
                "mac": mac
            }

        # Витягуємо дані про switch
        voltage, power, current_amp, frequency, switch_state = self._extract_switch_data(device_data)
        has_light = self._determine_has_light(voltage, power, current_amp)
        voltage_status = self._determine_voltage_status(voltage)

        logger.info(f"Підсумок: світло {'Є' if has_light else 'Немає'}, напруга: {voltage_status}")

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
        """Отримання реального статусу пристрою з Shelly Cloud"""
        logger.info("Початок перевірки")

        try:
            # Отримуємо всі дані
            data = self.fetch_all_status()
            
            # Перевіряємо, чи є devices_status у даних
            if "devices_status" not in data:
                logger.error(f"В даних відсутній ключ 'devices_status'. Отримані ключі: {list(data.keys())}")
                
                if not data.get("isok", True):
                    error_msg = data.get("error", "Unknown API error")
                    return {
                        "has_light": False,
                        "online": False,
                        "reason": "api_error",
                        "voltage_status": "unknown",
                        "last_update_time": int(time.time())
                    }
                
                # Якщо немає пристроїв
                return {
                    "has_light": False,
                    "online": False,
                    "reason": "no_devices",
                    "voltage_status": "unknown",
                    "last_update_time": int(time.time())
                }
            
            # Вибираємо пристрій
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
                "last_update_time": int(time.time())
            }
                
        except Exception as e:
            logger.exception(f"Виняток: {type(e).__name__}: {str(e)}")
            return {
                "has_light": False,
                "online": False,
                "reason": "error",
                "voltage_status": "unknown",
                "last_update_time": int(time.time())
            }

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

    def _get_formatted_time(self) -> str:
        """Отримує поточний час у форматованому вигляді"""
        try:
            tz = ZoneInfo(TIMEZONE)
            check_time = datetime.now(tz).strftime("%d.%m.%Y %H:%M:%S")
        except Exception:
            check_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        return check_time

    def check_light_status(self, is_routine_check: bool = False, is_manual_check: bool = False) -> str:
        """Основна функція для перевірки статусу світла
        
        Args:
            is_routine_check: True - рутинна перевірка (планове повідомлення)
            is_manual_check: True - ручна перевірка користувачем (/check)
            Якщо обидва False - автоматичний алерт про зміну стану
            
        Returns:
            Повідомлення для відправки. Для ручних перевірок НІКОЛИ не порожній рядок.
        """
        try:
            logger.info(f"Початок перевірки світла (тип: {'рутинна' if is_routine_check else 'ручна' if is_manual_check else 'алерт'})")

            status = self.get_real_device_status()
            
            # Якщо статус None - повертаємо помилку
            if status is None:
                return "❌ ПОМИЛКА: Не вдалося отримати статус"

            last_update_time = status.get("last_update_time", 0)
            time_info = self.get_last_update_time(last_update_time)

            # Отримуємо дані
            voltage = status.get("voltage")
            frequency = status.get("frequency")
            voltage_status = status.get("voltage_status", "unknown")
            has_light = status.get("has_light")
            online = status.get("online")

            # Отримуємо поточний час
            current_time = int(time.time())
            formatted_time = self._get_formatted_time()
            
            # Отримуємо інформацію про аномалію напруги
            voltage_anomaly_data = get_voltage_anomaly_start()
            voltage_anomaly_start = voltage_anomaly_data.get("anomaly_start") if voltage_anomaly_data else None
            previous_anomaly_type = voltage_anomaly_data.get("anomaly_type") if voltage_anomaly_data else None
            
            # Визначаємо, чи є аномалія напруги зараз
            current_anomaly_type = None
            if voltage_status == "low":
                current_anomaly_type = "low"
            elif voltage_status == "high":
                current_anomaly_type = "high"
            
            # Головна логіка за станом світла
            if not online or has_light is False:
                # СВІТЛА НЕМАЄ
                return self._handle_no_light_state(current_time, formatted_time, voltage, 
                                                  is_routine_check, is_manual_check)
            elif has_light is True:
                # СВІТЛО Є
                return self._handle_light_on_state(current_time, formatted_time, voltage, frequency,
                                                  voltage_status, time_info, voltage_anomaly_start,
                                                  previous_anomaly_type, current_anomaly_type,
                                                  is_routine_check, is_manual_check)
            else:
                # Невідомий стан (має бути неможливо)
                logger.error(f"Несподіване значення has_light: {has_light}")
                if is_manual_check or is_routine_check:
                    return f"📊 РЕЗУЛЬТАТ ПЕРЕВІРКИ: {formatted_time}\n\n❓ НЕВІДОМИЙ СТАН СИСТЕМИ"
                else:
                    return "❓ НЕВІДОМИЙ СТАН СИСТЕМИ"
                
        except Exception as e:
            logger.error(f"Помилка в check_light_status: {e}")
            return f"❌ ПОМИЛКА ПРИ ПЕРЕВІРЦІ: {str(e)}"

    def _handle_no_light_state(self, current_time: int, formatted_time: str, voltage: float,
                              is_routine_check: bool, is_manual_check: bool) -> str:
        """Обробка стану коли світла немає"""
        outage_start = get_outage_start_time()
        
        if outage_start is None:
            # Перше виявлення відключення
            set_outage_start_time(current_time)
            # Скидаємо аномалію напруги
            set_voltage_anomaly_start(None)
        
        # Розраховуємо тривалість відключення
        outage_duration = ""
        if outage_start:
            duration_seconds = current_time - outage_start
            outage_duration = f"\n⏱ Час відключення: {self.format_duration(duration_seconds)}"
        
        # Формуємо повідомлення
        voltage_info = f"\n🔌 Напруга: {voltage:.1f} В" if voltage is not None else ""
        
        # Для всіх типів перевірок показуємо однакове повідомлення
        message = f"❌ СВІТЛА НЕМАЄ{voltage_info}{outage_duration}"
        
        # Додаємо заголовок для ручних/рутинних перевірок
        if is_manual_check or is_routine_check:
            return f"📊 РЕЗУЛЬТАТ ПЕРЕВІРКИ: {formatted_time}\n\n{message}"
        else:
            # Автоматичний алерт - тільки при першому виявленні
            if outage_start == current_time:  # Тільки що виявили
                return message
            else:
                return ""  # Не відправляємо повторні алерти

    def _handle_light_on_state(self, current_time: int, formatted_time: str, voltage: float,
                              frequency: float, voltage_status: str, time_info: str,
                              voltage_anomaly_start: int, previous_anomaly_type: str,
                              current_anomaly_type: str, is_routine_check: bool,
                              is_manual_check: bool) -> str:
        """Обробка стану коли світло є"""
        voltage_display = f"{voltage:.1f} В" if voltage is not None else "–"
        frequency_display = f"{frequency:.1f} Гц" if frequency is not None else "–"
        
        # Інформація про час
        time_details = f"\n{time_info}" if time_info else ""
        
        # Перевірка на відновлення після відключення
        outage_start = get_outage_start_time()
        outage_recovery_info = ""
        if outage_start:
            outage_duration = current_time - outage_start
            if outage_duration > 60:
                duration_str = self.format_duration(outage_duration)
                outage_recovery_info = f"\n💡 Світла не було: {duration_str}"
            set_outage_start_time(None)
        
        # ===== РУЧНА ПЕРЕВІРКА (/check) =====
        if is_manual_check:
            details = f"🔌 Напруга: {voltage_display}\n〰️ Частота: {frequency_display}{time_details}"
            
            # Визначаємо статус напруги
            if voltage_status == "low":
                status_text = f"✅ СВІТЛО Є\n⚠️ Напруга низька ({voltage:.1f} В)"
            elif voltage_status == "high":
                status_text = f"✅ СВІТЛО Є\n⚠️ Напруга висока ({voltage:.1f} В)"
            else:
                status_text = "✅ СВІТЛО Є"
            
            return f"📊 РЕЗУЛЬТАТ ПЕРЕВІРКИ: {formatted_time}\n\n{status_text}\n\n{details}{outage_recovery_info}"
        
        # ===== РУТИННА ПЕРЕВІРКА (автоматична планова) =====
        elif is_routine_check:
            # Рутинні перевірки тільки коли все в нормі
            if voltage_status == "normal":
                details = f"🔌 Напруга: {voltage_display} (в нормі)\n〰️ Частота: {frequency_display}{time_details}"
                return f"📈 Стан мережі | {formatted_time}\n──────────────\n{details}\n✅ Світло є. Параметри стабільні.{outage_recovery_info}"
            else:
                # Якщо є проблеми - не відправляємо рутинне повідомлення
                return ""
        
        # ===== АВТОМАТИЧНИЙ АЛЕРТ (зміна стану) =====
        else:
            # Обробка аномалії напруги
            voltage_alert = self._process_voltage_anomaly(current_time, voltage, current_anomaly_type,
                                                         voltage_anomaly_start, previous_anomaly_type)
            
            # Формування результату
            if voltage_alert:
                result = voltage_alert
                if outage_recovery_info:
                    result += outage_recovery_info
                return result
            elif outage_recovery_info:
                # Відновлення після відключення
                details = f"🔌 Напруга: {voltage_display}\n〰️ Частота: {frequency_display}{time_details}"
                return f"✅ СВІТЛО ВІДНОВЛЕНО\n──────────────\n{details}{outage_recovery_info}"
            else:
                # Немає змін статусу - нічого не відправляємо
                return ""

    def _process_voltage_anomaly(self, current_time: int, voltage: float, 
                                current_anomaly_type: str, voltage_anomaly_start: int,
                                previous_anomaly_type: str) -> str:
        """Обробка аномалії напруги для автоматичних алертів"""
        result = ""
        
        if current_anomaly_type:
            if voltage_anomaly_start is None:
                # Перше виявлення аномалії
                set_voltage_anomaly_start({
                    "anomaly_start": current_time,
                    "anomaly_type": current_anomaly_type
                })
                
                if current_anomaly_type == "low":
                    result = f"⚠️ УВАГА! НИЗЬКА НАПРУГА\n──────────────\n🔌 {voltage:.1f} В (< {LOW_VOLTAGE} В)"
                else:
                    result = f"⚠️ УВАГА! ВИСОКА НАПРУГА\n──────────────\n🔌 {voltage:.1f} В (> {HIGH_VOLTAGE} В)"
                    
            elif previous_anomaly_type != current_anomaly_type:
                # Зміна типу аномалії
                anomaly_duration = current_time - voltage_anomaly_start
                duration_str = self.format_duration(anomaly_duration)
                previous_type = "низька" if previous_anomaly_type == "low" else "висока"
                
                result = f"✅ НАПРУГА В НОРМІ\n──────────────\n🔌 {voltage:.1f} В\n⏱ Тривала аномалія: {duration_str}\n🔹 Тип: {previous_type}\n\n"
                
                if current_anomaly_type == "low":
                    result += f"⚠️ УВАГА! НИЗЬКА НАПРУГА\n──────────────\n🔌 {voltage:.1f} В (< {LOW_VOLTAGE} В)"
                else:
                    result += f"⚠️ УВАГА! ВИСОКА НАПРУГА\n──────────────\n🔌 {voltage:.1f} В (> {HIGH_VOLTAGE} В)"
                
                set_voltage_anomaly_start({
                    "anomaly_start": current_time,
                    "anomaly_type": current_anomaly_type
                })
            
        elif voltage_anomaly_start and not current_anomaly_type:
            # Аномалія закінчилася
            anomaly_duration = current_time - voltage_anomaly_start
            duration_str = self.format_duration(anomaly_duration)
            previous_type = "низька" if previous_anomaly_type == "low" else "висока"
            result = f"✅ НАПРУГА В НОРМІ\n──────────────\n🔌 {voltage:.1f} В\n⏱ Тривала аномалія: {duration_str}\n🔹 Тип: {previous_type}"
            set_voltage_anomaly_start(None)
            
        return result

    def check_light(self, is_routine: bool = False, is_manual: bool = False) -> str:
        """Метод для зовнішнього виклику з вказівкою типу перевірки"""
        return self.check_light_status(is_routine_check=is_routine, is_manual_check=is_manual)