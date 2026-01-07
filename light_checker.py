import tinytuya
import time
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
        try:
            # Підключаємося до європейського регіону
            cloud = tinytuya.Cloud(
                apiRegion=self.region,
                apiKey=self.access_id,
                apiSecret=self.access_secret,
                apiDeviceID=self.device_id
            )
            
            # 1. Отримуємо інформацію про пристрій (де є поле 'online')
            devices_info = cloud.getdevices(self.device_id)  # ЗАПАМ'ЯТАЙТЕ: передаємо ID!
            
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
                        offline_minutes = (current_time - update_time) // 60
                        return {
                            "has_light": False,
                            "online": False,
                            "reason": "device_offline",
                            "offline_since": update_time,
                            "offline_minutes": offline_minutes,
                            "device_name": our_device.get("name"),
                            "ip_address": our_device.get("ip")
                        }
                    else:
                        # Якщо пристрій онлайн, перевіряємо реальні показники
                        status_data = cloud.getstatus(self.device_id)
                        
                        if status_data and status_data.get("success"):
                            return self.analyze_current_status(status_data, our_device)
                        else:
                            return {
                                "has_light": None, 
                                "online": True, 
                                "reason": "status_unavailable",
                                "device_name": our_device.get("name")
                            }
                else:
                    return {
                        "has_light": None, 
                        "online": None, 
                        "reason": "device_not_found"
                    }
            else:
                return {
                    "has_light": None, 
                    "online": None, 
                    "reason": "api_error",
                    "error_details": devices_info
                }
                
        except Exception as e:
            return {
                "has_light": None, 
                "online": None, 
                "reason": f"connection_error: {str(e)}"
            }
    
    def analyze_current_status(self, status_data, device_info):
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
                voltage = value / 10  # Конвертуємо в вольти
            elif code == "cur_power":
                power = value / 10  # Конвертуємо в вати
            elif code == "cur_current":
                current = value / 1000  # Конвертуємо в ампери
            elif code == "switch_1":
                switch_state = value
        
        # Визначаємо, чи є світло
        has_light = False
        
        if voltage is not None:
            if voltage > 100:  # Якщо напруга більше 100В
                if power is not None and power > 0:
                    # Є напруга і споживання - точно є світло
                    has_light = True
                elif current is not None and current > 0:
                    # Є напруга і струм - точно є світло
                    has_light = True
                else:
                    # Є напруга, але немає споживання
                    has_light = True  # Напруга є - припускаємо що світло є
            else:
                # Напруги немає або вона дуже низька
                has_light = False
        
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
        status = self.get_real_device_status()
        
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
            
            return (
                f"🔴 ПРИСТРІЙ OFFLINE\n\n"
                f"📱 Пристрій: {device_name}\n"
                f"⏱️ Офлайн вже: {time_str}\n\n"
                f"💡 ВИСНОВОК: Пристрій не підключений до інтернету"
            )
            
        elif status.get("has_light") is True:
            device_name = status.get("device_name", "Пристрій")
            voltage = status.get("voltage", 0)
            power = status.get("power", 0)
            
            message = f"✅ СВІТЛО Є!\n\n📱 Пристрій: {device_name}\n🔌 Напруга: {voltage:.1f} В"
            
            if power:
                message += f"\n💡 Потужність: {power:.1f} Вт"
            
            return message
            
        elif status.get("has_light") is False:
            device_name = status.get("device_name", "Пристрій")
            voltage = status.get("voltage", 0)
            
            return (
                f"❌ СВІТЛА НЕМАЄ!\n\n"
                f"📱 Пристрій: {device_name}\n"
                f"🔌 Напруга: {voltage:.1f} В\n\n"
                f"💡 ВИСНОВОК: Напруги немає або вона занадто низька"
            )
            
        elif status.get("reason") == "status_unavailable":
            device_name = status.get("device_name", "Пристрій")
            return f"⚠️ Пристрій онлайн\n📱 {device_name}\nℹ️ Дані про напругу недоступні"
            
        else:
            reason = status.get("reason", "невідома причина")
            return f"❌ Помилка: {reason}"