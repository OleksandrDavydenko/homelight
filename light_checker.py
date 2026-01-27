import requests
import time
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from config import SHELLY_AUTH_KEY, SHELLY_BASE_URL, TARGET_MAC, TIMEZONE

class LightChecker:
    def __init__(self):
        self.auth_key = SHELLY_AUTH_KEY
        self.base_url = SHELLY_BASE_URL
        self.target_mac = TARGET_MAC
        
    def fetch_all_status(self, max_retries=5):
        """Отримання статусу всіх пристроїв з Shelly Cloud"""
        url = f"{self.base_url}/device/all_status"
        payload = {"auth_key": self.auth_key}

        delay = 2
        for attempt in range(1, max_retries + 1):
            try:
                print(f"📡 [Shelly API] Спроба {attempt}/{max_retries} отримати дані...")
                r = requests.post(url, data=payload, timeout=15)

                if r.status_code == 429:
                    print(f"⚠️ [Shelly API] 429 Too many requests. Чекаю {delay}s...")
                    time.sleep(delay)
                    delay = min(delay * 2, 30)
                    continue

                if r.status_code != 200:
                    print(f"❌ [Shelly API] HTTP помилка: {r.status_code}")
                    print(f"❌ [Shelly API] Тіло відповіді: {r.text[:300]}")
                    r.raise_for_status()

                j = r.json()
                if not j.get("isok"):
                    raise RuntimeError(f"❌ [Shelly API] API помилка: {j}")

                print(f"✅ [Shelly API] Дані успішно отримано")
                return j["data"]["devices_status"]

            except requests.exceptions.RequestException as e:
                print(f"❌ [Shelly API] Помилка запиту: {e}")
                if attempt < max_retries:
                    print(f"🔄 [Shelly API] Повторна спроба через {delay} сек...")
                    time.sleep(delay)
                    delay = min(delay * 2, 30)
                else:
                    raise RuntimeError(f"Не вдалося отримати дані після {max_retries} спроб")

        raise RuntimeError("Не вдалося отримати дані: постійний 429 (rate limit). Спробуйте через 1-2 хвилини.")

    def pick_device(self, devices_status: dict):
        """Вибір цільового пристрою"""
        if not devices_status:
            raise RuntimeError("❌ [Shelly API] devices_status порожній")

        # Якщо вказано конкретний MAC
        if self.target_mac:
            mac_lower = self.target_mac.lower()
            mac_upper = self.target_mac.upper()
            
            # Шукаємо в різних форматах
            for mac in [mac_lower, mac_upper, self.target_mac]:
                if mac in devices_status:
                    print(f"✅ [Shelly API] Знайдено цільовий пристрій: {mac}")
                    return mac, devices_status[mac]
            
            print(f"⚠️ [Shelly API] Не знайдено пристрій з MAC: {self.target_mac}")
            print(f"⚠️ [Shelly API] Доступні пристрої: {list(devices_status.keys())}")
        
        # Беремо перший пристрій
        first_mac = next(iter(devices_status.keys()))
        print(f"⚠️ [Shelly API] Використовується перший пристрій: {first_mac}")
        return first_mac, devices_status[first_mac]

    def analyze_device_data(self, mac: str, device_data: dict):
        """Аналіз даних пристрою та визначення статусу світла"""
        print(f"📊 [Shelly API] Аналіз даних для пристрою {mac}...")
        
        current_time = int(time.time())
        
        # Отримуємо системну інформацію
        sys_info = device_data.get('sys', {})
        online = sys_info.get('mac') is not None  # Якщо є MAC, пристрій існує
        
        if not online:
            print(f"⚠️ [Shelly API] Пристрій {mac} не знайдено або OFFLINE")
            return {
                "has_light": False,
                "online": False,
                "reason": "device_offline",
                "device_name": f"Shelly Device {mac[-6:]}",
                "last_update_time": current_time,
                "mac": mac
            }
        
        # Перевіряємо чи пристрій онлайн в хмарі
        cloud_connected = device_data.get('cloud', {}).get('connected', False)
        if not cloud_connected:
            print(f"⚠️ [Shelly API] Пристрій {mac} не підключений до хмари")
            return {
                "has_light": None,
                "online": True,  # Пристрій існує, але не в хмарі
                "reason": "cloud_disconnected",
                "device_name": f"Shelly Device {mac[-6:]}",
                "last_update_time": sys_info.get('last_sync_ts', current_time),
                "mac": mac
            }
        
        # Шукаємо switch пристрій для перевірки напруги
        voltage = None
        power = None
        current_amp = None
        frequency = None
        switch_state = None
        
        # Шукаємо всі ключі, що починаються з 'switch'
        for key, value in device_data.items():
            if key.startswith('switch'):
                print(f"🔌 [Shelly API] Знайдено {key}")
                
                if isinstance(value, dict):
                    voltage = value.get('voltage')
                    power = value.get('apower')
                    current_amp = value.get('current')
                    frequency = value.get('freq')
                    switch_state = value.get('output')
                    
                    print(f"📊 [Shelly API] Напруга: {voltage} V")
                    print(f"📊 [Shelly API] Потужність: {power} W")
                    print(f"📊 [Shelly API] Струм: {current_amp} A")
                    print(f"📊 [Shelly API] Частота: {frequency} Hz")
                    print(f"📊 [Shelly API] Стан виходу: {'ON' if switch_state else 'OFF'}")
                    break
        
        # Визначаємо, чи є світло
        has_light = False
        
        if voltage is not None:
            if voltage > 100:  # Якщо напруга більше 100В
                print(f"✅ [Shelly API] Напруга більше 100В ({voltage} V)")
                if power is not None and power > 0:
                    # Є напруга і споживання - точно є світло
                    has_light = True
                    print(f"✅ [Shelly API] Є споживання: {power} W - світло Є")
                elif current_amp is not None and current_amp > 0:
                    # Є напруга і струм - точно є світло
                    has_light = True
                    print(f"✅ [Shelly API] Є струм: {current_amp} A - світло Є")
                else:
                    # Є напруга, але немає споживання
                    print(f"⚠️ [Shelly API] Напруга є, але немає споживання")
                    has_light = True  # Напруга є - припускаємо що світло є
            else:
                # Напруги немає або вона дуже низька
                print(f"❌ [Shelly API] Напруга менше 100В ({voltage} V) - світла НЕМАЄ")
                has_light = False
        else:
            # Не вдалося отримати напругу
            print(f"⚠️ [Shelly API] Не вдалося отримати напругу")
            has_light = None
        
        # Отримуємо назву пристрою
        device_name = f"Shelly {device_data.get('code', 'Device')} ({mac[-6:]})"
        
        print(f"📊 [Shelly API] Підсумок: світло {'Є' if has_light else 'Немає' if has_light is False else 'Невідомо'}")
        
        return {
            "has_light": has_light,
            "online": True,
            "voltage": voltage,
            "power": power,
            "current": current_amp,
            "frequency": frequency,
            "switch_state": switch_state,
            "device_name": device_name,
            "ip_address": device_data.get('wifi', {}).get('sta_ip'),
            "timestamp": current_time,
            "last_update_time": sys_info.get('last_sync_ts', current_time),
            "mac": mac
        }
    
    def get_real_device_status(self):
        """Отримання реального статусу пристрою з Shelly Cloud"""
        print(f"🔧 [Shelly API] Початок перевірки")
        print(f"🔧 [Shelly API] Base URL: {self.base_url}")
        print(f"🔧 [Shelly API] Target MAC: {self.target_mac if self.target_mac else 'автоматичний вибір'}")
        
        try:
            # Отримуємо дані з Shelly Cloud
            devices_status = self.fetch_all_status()
            
            # Обираємо пристрій
            mac, device_data = self.pick_device(devices_status)
            
            print(f"✅ [Shelly API] Обрано пристрій: {mac}")
            
            # Аналізуємо дані
            return self.analyze_device_data(mac, device_data)
            
        except Exception as e:
            print(f"💥 [Shelly API] Виняток: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            
            return {
                "has_light": None, 
                "online": None, 
                "reason": f"connection_error: {str(e)}",
                "last_update_time": int(time.time())
            }
    
    def format_time_ago(self, timestamp):
        """Форматує час у зрозумілий формат"""
        if not timestamp or timestamp == 0:
            return "немає даних"
        
        current_time = int(time.time())
        diff_seconds = current_time - timestamp
        
        if diff_seconds < 60:
            return "щойно"
        elif diff_seconds < 3600:
            minutes = diff_seconds // 60
            return f"{minutes} хв тому"
        elif diff_seconds < 86400:
            hours = diff_seconds // 3600
            minutes = (diff_seconds % 3600) // 60
            return f"{hours} год {minutes} хв тому"
        else:
            days = diff_seconds // 86400
            hours = (diff_seconds % 86400) // 3600
            return f"{days} дн {hours} год тому"
    
    def format_duration(self, minutes):
        """Форматує тривалість у зрозумілий формат"""
        if minutes < 60:
            return f"{minutes} хв"
        elif minutes < 1440:
            hours = minutes // 60
            mins = minutes % 60
            return f"{hours} год {mins} хв"
        else:
            days = minutes // 1440
            hours = (minutes % 1440) // 60
            return f"{days} дн {hours} год"
    
    def get_last_update_time(self, timestamp):
        """Отримує час останнього оновлення"""
        if not timestamp or timestamp == 0:
            return "Час оновлення: немає даних"

        # Конвертуємо timestamp у datetime з урахуванням обраної часової зони
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

        return f"🕒 Оновлено: {time_str} ({ago})"
    
    def check_light_status(self):
        """Основна функція для перевірки статусу світла"""
        print(f"\n" + "="*60)
        print(f"🔌 [BOT] Початок перевірки світла через Shelly API")
        print(f"="*60)
        
        status = self.get_real_device_status()
        
        print(f"\n📋 [BOT] Результат перевірки:")
        for key, value in status.items():
            if key not in ['error_details']:  # Пропускаємо великі об'єкти
                print(f"📋 [BOT] {key}: {value}")
        
        # Отримуємо інформацію про час
        last_update_time = status.get("last_update_time", 0)
        time_info = self.get_last_update_time(last_update_time)
        
        # Формуємо зрозуміле повідомлення для користувача
        if status.get("online") is False:
            offline_since = status.get("last_update_time", 0)
            if offline_since:
                offline_minutes = (int(time.time()) - offline_since) // 60
                offline_duration = self.format_duration(offline_minutes)
            else:
                offline_duration = "невідомо"
            
            result = (
                f"🔴 СТАН: НЕМАЄ СВІТЛА\n\n"
                f"⏱️ Час відключення: {offline_duration}\n"
                f"{time_info}\n\n"
                f"💡 Пристрій OFFLINE"
            )
            
        elif status.get("has_light") is True:
            voltage = status.get("voltage", 0)
            power = status.get("power", 0)
            current = status.get("current", 0)
            frequency = status.get("frequency", 0)
            
            # Формуємо детальну інформацію
            details = []
            if voltage:
                details.append(f"🔌 Напруга: {voltage} В")
            if power:
                details.append(f"💡 Потужність: {power} Вт")
            if current:
                details.append(f"⚡ Струм: {current} А")
            if frequency:
                details.append(f"〰️  Частота: {frequency} Гц")
            
            details_str = "\n".join(details)
            
            result = (
                f"✅ СТАН: СВІТЛО Є\n\n"
                f"{details_str}\n"
                f"{time_info}\n\n"
                f"💡 Електропостачання працює стабільно"
            )
            
        elif status.get("has_light") is False:
            voltage = status.get("voltage", 0)
            
            result = (
                f"❌ СТАН: СВІТЛА НЕМАЄ\n\n"
                f"🔌 Напруга в мережі: {voltage} В\n"
                f"{time_info}\n\n"
                f"💡 Відсутнє електропостачання"
            )
            
        elif status.get("reason") == "cloud_disconnected":
            result = (
                f"⚠️ СТАН: НЕМАЄ ПІДКЛЮЧЕННЯ ДО ХМАРИ\n\n"
                f"{time_info}\n\n"
                f"💡 Пристрій працює, але не підключений до Shelly Cloud"
            )
            
        elif "connection_error" in str(status.get("reason", "")):
            reason = status.get("reason", "невідома помилка")
            
            result = (
                f"❌ ПОМИЛКА ПІДКЛЮЧЕННЯ\n\n"
                f"ℹ️ {reason}\n\n"
                f"💡 Перевірте з'єднання з Shelly Cloud"
            )
            
        else:
            reason = status.get("reason", "невідома причина")
            result = (
                f"❌ ПОМИЛКА\n\n"
                f"ℹ️ {reason}\n\n"
                f"💡 Спробуйте пізніше"
            )
        
        # Додаємо заголовок з поточним часом у відповідній часовій зоні
        try:
            tz = ZoneInfo(TIMEZONE)
            current_time = datetime.now(tz).strftime("%d.%m.%Y %H:%M:%S")
        except Exception:
            current_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        
        # Додаємо інформацію про пристрій
        device_name = status.get("device_name", "Пристрій")
        mac = status.get("mac", "немає")
        
        
        final_result = f"📊 ПЕРЕВІРКА: {current_time}\n{result}"
        
        print(f"\n📤 [BOT] Відправляємо користувачу:")
        print(final_result)
        print(f"="*60 + "\n")
        
        return final_result