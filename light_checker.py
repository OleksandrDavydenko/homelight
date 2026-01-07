import tinytuya
import time
import json
from config import ACCESS_ID, ACCESS_SECRET, DEVICE_ID, TUYA_REGION

class LightChecker:
    def __init__(self):
        self.access_id = ACCESS_ID
        self.access_secret = ACCESS_SECRET
        self.device_id = DEVICE_ID
        self.region = TUYA_REGION
        
    def check_light_status(self):
        """Перевірка статусу світла"""
        try:
            print(f"🔧 DEBUG: Початок перевірки світла")
            # Підключення до Tuya Cloud
            cloud = tinytuya.Cloud(
                apiRegion=self.region,
                apiKey=self.access_id,
                apiSecret=self.access_secret,
                apiDeviceID=self.device_id
            )
            
            # Отримуємо список пристроїв
            response = cloud.getdevices()
            
            if isinstance(response, list):
                our_device = None
                for device in response:
                    if device.get("id") == self.device_id:
                        our_device = device
                        break
                
                if our_device:
                    device_name = our_device.get("name", "Розетка")
                    online = our_device.get("online", False)
                    
                    if online:
                        # Отримуємо детальний статус
                        status_response = cloud.getstatus(self.device_id)
                        
                        if isinstance(status_response, dict) and status_response.get("success"):
                            for item in status_response.get("result", []):
                                if item.get("code") == "cur_voltage":
                                    voltage = item.get("value", 0) / 10.0
                                    if voltage > 100:
                                        return f"✅ СВІТЛО Є!\n\n📱 Пристрій: {device_name}\n🔌 Напруга: {voltage:.1f} В"
                                    else:
                                        return f"❌ СВІТЛА НЕМАЄ!\n\n📱 Пристрій: {device_name}\n🔌 Напруга: {voltage:.1f} В"
                        
                        return f"⚠️ Пристрій онлайн\n📱 {device_name}\nℹ️ Напруга не визначена"
                    else:
                        update_time = our_device.get("update_time", 0)
                        offline_min = (int(time.time()) - update_time) // 60
                        return f"🔴 ПРИСТРІЙ OFFLINE\n\n📱 Пристрій: {device_name}\n⏱️ Офлайн вже: {offline_min} хв."
                else:
                    return "❌ Пристрій не знайдено"
            else:
                return "❌ Невірна відповідь від API Tuya"
                
        except Exception as e:
            return f"❌ Помилка підключення: {str(e)}"
