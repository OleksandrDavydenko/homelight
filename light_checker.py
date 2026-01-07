import tinytuya
import time
import json
from datetime import datetime
from config import ACCESS_ID, ACCESS_SECRET, DEVICE_ID, TUYA_REGION

class LightChecker:
    def __init__(self):
        self.access_id = ACCESS_ID
        self.access_secret = ACCESS_SECRET
        self.device_id = DEVICE_ID
        self.region = TUYA_REGION
        
    def get_real_device_status(self):
        """Отримання реального статусу пристрою з перевіркою онлайн статусу"""
        print(f"🔧 [Tuya API] Початок перевірки для пристрою: {self.device_id}")
        print(f"🔧 [Tuya API] Регіон: {self.region}")
        print(f"🔧 [Tuya API] Access ID: {self.access_id[:10]}...")
        
        try:
            # Підключаємося до європейського регіону
            print(f"🔧 [Tuya API] Створення підключення до Tuya Cloud...")
            cloud = tinytuya.Cloud(
                apiRegion=self.region,
                apiKey=self.access_id,
                apiSecret=self.access_secret,
                apiDeviceID=self.device_id
            )
            
            # 1. Отримуємо інформацію про пристрій
            print(f"📡 [Tuya API] Виклик cloud.getdevices('{self.device_id}')...")
            devices_info = cloud.getdevices(self.device_id)
            
            print(f"📡 [Tuya API] Відповідь getdevices():")
            print(f"📡 [Tuya API] Тип відповіді: {type(devices_info)}")
            
            # Логуємо відповідь (обмежуємо довжину для безпеки)
            if devices_info is not None:
                response_str = str(devices_info)
                if len(response_str) > 500:
                    response_str = response_str[:500] + "..."
                print(f"📡 [Tuya API] Вміст відповіді: {response_str}")
            
            if devices_info and devices_info.get("success"):
                devices_list = devices_info.get("result", [])
                print(f"✅ [Tuya API] Успішна відповідь, знайдено {len(devices_list)} пристроїв")
                
                # Шукаємо нашу розетку
                our_device = None
                for idx, device in enumerate(devices_list):
                    device_id = device.get("id", "немає")
                    device_name = device.get("name", "без назви")
                    print(f"🔍 [Tuya API] Пристрій #{idx+1}: {device_name} (ID: {device_id})")
                    
                    if device.get("id") == self.device_id:
                        our_device = device
                        print(f"🎯 [Tuya API] Знайдено цільовий пристрій!")
                        break
                
                if our_device:
                    device_name = our_device.get("name", "Пристрій")
                    print(f"📱 [Tuya API] Назва пристрою: {device_name}")
                    print(f"📍 [Tuya API] IP адреса: {our_device.get('ip')}")
                    print(f"📊 [Tuya API] Категорія: {our_device.get('category')}")
                    
                    # КЛЮЧОВЕ: Перевіряємо онлайн статус
                    online_status = our_device.get("online", False)
                    update_time = our_device.get("update_time", 0)
                    current_time = int(time.time())
                    
                    print(f"🌐 [Tuya API] Статус підключення: {'🟢 ONLINE' if online_status else '🔴 OFFLINE'}")
                    
                    if update_time > 0:
                        last_seen = datetime.fromtimestamp(update_time).strftime("%Y-%m-%d %H:%M:%S")
                        seconds_ago = current_time - update_time
                        print(f"🕒 [Tuya API] Час останнього оновлення: {last_seen}")
                        print(f"⏱️ [Tuya API] Секунд тому: {seconds_ago}")
                    
                    if not online_status:
                        offline_minutes = (current_time - update_time) // 60
                        print(f"⚠️ [Tuya API] Пристрій OFFLINE вже {offline_minutes} хвилин!")
                        
                        return {
                            "has_light": False,
                            "online": False,
                            "reason": "device_offline",
                            "offline_since": update_time,
                            "offline_minutes": offline_minutes,
                            "device_name": device_name,
                            "ip_address": our_device.get("ip")
                        }
                    else:
                        # Якщо пристрій онлайн, перевіряємо реальні показники
                        print(f"🔄 [Tuya API] Отримуємо поточний статус...")
                        status_data = cloud.getstatus(self.device_id)
                        
                        print(f"📡 [Tuya API] Відповідь getstatus():")
                        if status_data is not None:
                            status_str = str(status_data)
                            if len(status_str) > 500:
                                status_str = status_str[:500] + "..."
                            print(f"📡 [Tuya API] Вміст: {status_str}")
                        
                        if status_data and status_data.get("success"):
                            print(f"✅ [Tuya API] Успішно отримано детальний статус")
                            return self.analyze_current_status(status_data, our_device)
                        else:
                            print(f"❌ [Tuya API] Не вдалося отримати поточний статус")
                            return {
                                "has_light": None, 
                                "online": True, 
                                "reason": "status_unavailable",
                                "device_name": device_name
                            }
                else:
                    print(f"❌ [Tuya API] Пристрій {self.device_id} не знайдено в списку")
                    return {
                        "has_light": None, 
                        "online": None, 
                        "reason": "device_not_found"
                    }
            else:
                # Детально логуємо помилку
                print(f"❌ [Tuya API] Помилка API!")
                
                if devices_info is None:
                    error_msg = "Відповідь None"
                elif isinstance(devices_info, dict):
                    if "success" in devices_info:
                        print(f"📊 [Tuya API] success: {devices_info.get('success')}")
                    
                    if "msg" in devices_info:
                        error_msg = devices_info.get("msg", "Невідома помилка")
                        print(f"📊 [Tuya API] msg: {error_msg}")
                    
                    if "code" in devices_info:
                        error_code = devices_info.get("code", "немає")
                        print(f"📊 [Tuya API] code: {error_code}")
                    
                    if "Err" in devices_info:
                        error_err = devices_info.get("Err", "немає")
                        print(f"📊 [Tuya API] Err: {error_err}")
                    
                    if "Error" in devices_info:
                        error_error = devices_info.get("Error", "немає")
                        print(f"📊 [Tuya API] Error: {error_error}")
                    
                    if "Payload" in devices_info:
                        payload = devices_info.get("Payload", "немає")
                        print(f"📊 [Tuya API] Payload: {payload}")
                else:
                    error_msg = f"Невідомий тип відповіді: {type(devices_info)}"
                
                return {
                    "has_light": None, 
                    "online": None, 
                    "reason": "api_error",
                    "error_details": devices_info
                }
                
        except Exception as e:
            print(f"💥 [Tuya API] Виняток: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            
            return {
                "has_light": None, 
                "online": None, 
                "reason": f"connection_error: {str(e)}"
            }
    
    def analyze_current_status(self, status_data, device_info):
        """Аналіз поточного статусу пристрою"""
        print(f"📊 [Tuya API] Аналіз поточного статусу...")
        
        result = status_data.get("result", [])
        print(f"📊 [Tuya API] Знайдено {len(result)} параметрів")
        
        # Знаходимо ключові параметри
        voltage = None
        power = None
        current = None
        switch_state = None
        
        for idx, item in enumerate(result):
            code = item.get("code")
            value = item.get("value")
            print(f"📊 [Tuya API] Параметр #{idx+1}: code={code}, value={value}")
            
            if code == "cur_voltage":
                voltage = value / 10  # Конвертуємо в вольти
                print(f"🔌 [Tuya API] Напруга: {voltage:.1f} В")
            elif code == "cur_power":
                power = value / 10  # Конвертуємо в вати
                print(f"💡 [Tuya API] Потужність: {power:.1f} Вт")
            elif code == "cur_current":
                current = value / 1000  # Конвертуємо в ампери
                print(f"📊 [Tuya API] Струм: {current:.3f} А")
            elif code == "switch_1":
                switch_state = value
                print(f"⚡ [Tuya API] Стан розетки: {'ВКЛЮЧЕНО' if value else 'ВИМКНЕНО'}")
        
        # Визначаємо, чи є світло
        has_light = False
        
        if voltage is not None:
            if voltage > 100:  # Якщо напруга більше 100В
                print(f"✅ [Tuya API] Напруга більше 100В ({voltage:.1f} В)")
                if power is not None and power > 0:
                    # Є напруга і споживання - точно є світло
                    has_light = True
                    print(f"✅ [Tuya API] Є споживання: {power:.1f} Вт - світло Є")
                elif current is not None and current > 0:
                    # Є напруга і струм - точно є світло
                    has_light = True
                    print(f"✅ [Tuya API] Є струм: {current:.3f} А - світло Є")
                else:
                    # Є напруга, але немає споживання
                    print(f"⚠️ [Tuya API] Напруга є, але немає споживання")
                    has_light = True  # Напруга є - припускаємо що світло є
            else:
                # Напруги немає або вона дуже низька
                print(f"❌ [Tuya API] Напруга менше 100В ({voltage:.1f} В) - світла НЕМАЄ")
                has_light = False
        else:
            # Не вдалося отримати напругу
            print(f"⚠️ [Tuya API] Не вдалося отримати напругу")
            has_light = None
        
        print(f"📊 [Tuya API] Підсумок: світло {'Є' if has_light else 'Немає' if has_light is False else 'Невідомо'}")
        
        return {
            "has_light": has_light,
            "online": device_info.get("online", False),
            "voltage": voltage,
            "power": power,
            "current": current,
            "switch_state": switch_state,
            "device_name": device_info.get("name"),
            "ip_address": device_info.get("ip"),
            "timestamp": int(time.time())
        }
    
    def check_light_status(self):
        """Основна функція для перевірки статусу світла"""
        print(f"\n" + "="*60)
        print(f"🔌 [BOT] Початок перевірки світла")
        print(f"="*60)
        
        status = self.get_real_device_status()
        
        print(f"\n📋 [BOT] Результат перевірки:")
        print(f"📋 [BOT] Статус: {status}")
        
        # Формуємо зрозуміле повідомлення для користувача
        if status.get("online") is False:
            device_name = status.get("device_name", "Пристрій")
            offline_minutes = status.get("offline_minutes", 0)
            
            if offline_minutes > 60:
                hours = offline_minutes // 60
                minutes = offline_minutes % 60
                time_str = f"{hours} год {minutes} хв"
            else:
                time_str = f"{offline_minutes} хв"
            
            result = (
                f"🔴 ПРИСТРІЙ OFFLINE\n\n"
                f"📱 Пристрій: {device_name}\n"
                f"⏱️ Офлайн вже: {time_str}\n\n"
                f"💡 Пристрій не підключений до інтернету"
            )
            
        elif status.get("has_light") is True:
            device_name = status.get("device_name", "Пристрій")
            voltage = status.get("voltage", 0)
            power = status.get("power", 0)
            
            result = f"✅ СВІТЛО Є!\n\n📱 Пристрій: {device_name}\n🔌 Напруга: {voltage:.1f} В"
            
            if power:
                result += f"\n💡 Потужність: {power:.1f} Вт"
            
        elif status.get("has_light") is False:
            device_name = status.get("device_name", "Пристрій")
            voltage = status.get("voltage", 0)
            
            result = (
                f"❌ СВІТЛА НЕМАЄ!\n\n"
                f"📱 Пристрій: {device_name}\n"
                f"🔌 Напруга: {voltage:.1f} В\n\n"
                f"💡 Напруги немає або вона занадто низька"
            )
            
        elif status.get("reason") == "status_unavailable":
            device_name = status.get("device_name", "Пристрій")
            result = f"⚠️ Пристрій онлайн\n📱 {device_name}\nℹ️ Дані про напругу недоступні"
            
        elif status.get("reason") == "api_error":
            error_details = status.get("error_details", {})
            
            # Спроба отримати детальну інформацію про помилку
            if isinstance(error_details, dict):
                if "Error" in error_details:
                    error_msg = error_details.get("Error", "Невідома помилка")
                elif "msg" in error_details:
                    error_msg = error_details.get("msg", "Невідома помилка")
                else:
                    error_msg = str(error_details)
            else:
                error_msg = str(error_details)
            
            # Обрізаємо довгий текст
            if len(error_msg) > 200:
                error_msg = error_msg[:200] + "..."
            
            result = (
                f"❌ ПОМИЛКА TUYA API\n\n"
                f"Деталі: {error_msg}\n\n"
                f"💡 Перевірте:\n"
                f"• Чи додано IP Heroku до Tuya Whitelist\n"
                f"• Чи правильні API ключі\n"
                f"• Чи активний Tuya Cloud проект"
            )
            
        else:
            reason = status.get("reason", "невідома причина")
            result = f"❌ Помилка: {reason}"
        
        print(f"\n📤 [BOT] Відправляємо користувачу: {result}")
        print(f"="*60 + "\n")
        
        return result