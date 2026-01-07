import tinytuya
import time
from datetime import datetime
from config import ACCESS_ID, ACCESS_SECRET, DEVICE_ID

class LightChecker:
    def __init__(self):
        self.access_id = ACCESS_ID
        self.access_secret = ACCESS_SECRET
        self.device_id = DEVICE_ID
    
    def get_real_device_status(self):
        """Отримання реального статусу пристрою з перевіркою онлайн статусу"""
        try:
            # Підключаємося до європейського регіону
            cloud = tinytuya.Cloud(
                apiRegion="eu",
                apiKey=self.access_id,
                apiSecret=self.access_secret,
                apiDeviceID=self.device_id
            )
            
            # 1. Отримуємо інформацію про пристрій
            devices_info = cloud.getdevices(self.device_id)
            
            if devices_info and devices_info.get("success"):
                devices_list = devices_info.get("result", [])
                
                # Шукаємо нашу розетку
                our_device = None
                for device in devices_list:
                    if device.get("id") == self.device_id:
                        our_device = device
                        break
                
                if our_device:
                    # КЛЮЧОВЕ: Перевіряємо онлайн статус
                    online_status = our_device.get("online", False)
                    update_time = our_device.get("update_time", 0)
                    current_time = int(time.time())
                    
                    if not online_status:
                        return {
                            "has_light": False,
                            "online": False,
                            "reason": "device_offline",
                            "offline_since": update_time,
                            "offline_minutes": (current_time - update_time) // 60,
                            "message": "🔴 РОЗЕТКА OFFLINE"
                        }
                    else:
                        # Якщо пристрій онлайн, перевіряємо реальні показники
                        status_data = cloud.getstatus(self.device_id)
                        
                        if status_data and status_data.get("success"):
                            return self._analyze_current_status(status_data, our_device)
                        else:
                            return {
                                "has_light": None, 
                                "online": True, 
                                "reason": "status_unavailable",
                                "message": "⚠️ Не вдалося отримати поточний статус"
                            }
                else:
                    return {
                        "has_light": None, 
                        "online": None, 
                        "reason": "device_not_found",
                        "message": "❌ Пристрій не знайдено"
                    }
            else:
                return {
                    "has_light": None, 
                    "online": None, 
                    "reason": "api_error",
                    "message": "❌ Помилка API"
                }
                
        except Exception as e:
            return {
                "has_light": None, 
                "online": None, 
                "reason": f"connection_error: {str(e)}",
                "message": f"❌ Помилка підключення: {str(e)}"
            }
    
    def _analyze_current_status(self, status_data, device_info):
        """Аналіз поточного статусу пристрою"""
        result = status_data.get("result", [])
        
        # Знаходимо ключові параметри
        voltage = None
        power = None
        current = None
        switch_state = None
        
        for item in result:
            code = item.get("code")
            value = item.get("value")
            
            if code == "cur_voltage":
                voltage = value / 10
            elif code == "cur_power":
                power = value / 10
            elif code == "cur_current":
                current = value / 1000
            elif code == "switch_1":
                switch_state = value
        
        # Визначаємо, чи є світло
        has_light = False
        message = ""
        
        if voltage is not None:
            if voltage > 100:
                if (power is not None and power > 0) or (current is not None and current > 0):
                    has_light = True
                    message = f"✅ СВІТЛО Є\n🔌 Напруга: {voltage:.1f} В"
                    if power:
                        message += f"\n💡 Потужність: {power:.1f} Вт"
                else:
                    has_light = True  # Напруга є - припускаємо що світло є
                    message = f"⚠️ НАПРУГА Є ({voltage:.1f} В), але немає споживання\nМожливо, нічого не підключено до розетки"
            else:
                message = f"❌ НАПРУГИ НЕМАЄ ({voltage:.1f} В)\n💡 ВИСНОВОК: СВІТЛА НЕМАЄ"
        else:
            message = "⚠️ НЕ ВДАЛОСЯ ОТРИМАТИ НАПРУГУ\n💡 ВИСНОВОК: НЕВІДОМО"
        
        return {
            "has_light": has_light,
            "online": device_info.get("online", False),
            "voltage": voltage,
            "power": power,
            "current": current,
            "switch_state": switch_state,
            "device_name": device_info.get("name"),
            "ip_address": device_info.get("ip"),
            "timestamp": int(time.time()),
            "message": message
        }
    
    def check_light_status(self):
        """Основна функція для перевірки статусу світла"""
        status = self.get_real_device_status()
        
        # Формуємо остаточне повідомлення
        if status.get("online") is False:
            final_message = f"{status.get('message')}\n"
            final_message += f"⏱️ Офлайн вже: {status.get('offline_minutes', 0)} хвилин\n"
            final_message += "💡 ВИСНОВОК: СВІТЛА НЕМАЄ - розетка не підключена до інтернету"
        elif status.get("has_light") is True:
            final_message = f"🟢 РОЗЕТКА ONLINE\n"
            final_message += f"{status.get('message')}\n"
            final_message += "💡 ВИСНОВОК: СВІТЛО Є"
        elif status.get("has_light") is False:
            final_message = f"{status.get('message')}\n"
            final_message += "💡 ВИСНОВОК: СВІТЛА НЕМАЄ"
        else:
            final_message = f"{status.get('message')}\n"
            final_message += "⚠️ СТАТУС НЕВІДОМИЙ\n"
            final_message += "💡 Можливо: проблеми з підключенням або API"
        
        return final_message