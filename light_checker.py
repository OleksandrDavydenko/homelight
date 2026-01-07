import tinytuya
import time
from config import ACCESS_ID, ACCESS_SECRET, DEVICE_ID, TUYA_REGION

class LightChecker:
    def __init__(self):
        self.access_id = ACCESS_ID
        self.access_secret = ACCESS_SECRET
        self.device_id = DEVICE_ID
        self.region = TUYA_REGION
        
    def check_light_status(self):
        """Перевірка статусу світла - спрощена версія"""
        try:
            # Підключення до Tuya Cloud
            cloud = tinytuya.Cloud(
                apiRegion=self.region,
                apiKey=self.access_id,
                apiSecret=self.access_secret,
                apiDeviceID=self.device_id
            )
            
            # Отримуємо список пристроїв
            devices = cloud.getdevices()
            
            # Перевіряємо тип відповіді
            if isinstance(devices, list):
                # Шукаємо наш пристрій
                our_device = None
                for device in devices:
                    if isinstance(device, dict) and device.get("id") == self.device_id:
                        our_device = device
                        break
                
                if our_device:
                    device_name = our_device.get("name", "Розетка")
                    online = our_device.get("online", False)
                    
                    if online:
                        # Отримуємо детальний статус
                        status = cloud.getstatus(self.device_id)
                        
                        if isinstance(status, dict) and status.get("success"):
                            # Шукаємо напругу
                            for item in status.get("result", []):
                                if item.get("code") == "cur_voltage":
                                    voltage = item.get("value", 0) / 10.0
                                    if voltage > 100:
                                        return f"✅ СВІТЛО Є!\n\n📱 Пристрій: {device_name}\n🔌 Напруга: {voltage:.1f} В"
                                    else:
                                        return f"❌ СВІТЛА НЕМАЄ!\n\n📱 Пристрій: {device_name}\n🔌 Напруга: {voltage:.1f} В"
                            
                            return f"⚠️ Пристрій онлайн\n📱 {device_name}\nℹ️ Напруга не визначена"
                        else:
                            return f"⚠️ Пристрій онлайн\n📱 {device_name}\nℹ️ Дані недоступні"
                    else:
                        # Пристрій офлайн
                        update_time = our_device.get("update_time", 0)
                        offline_min = (int(time.time()) - update_time) // 60
                        return f"🔴 ПРИСТРІЙ OFFLINE\n\n📱 Пристрій: {device_name}\n⏱️ Офлайн вже: {offline_min} хв."
                else:
                    # Пристрій не знайдено
                    return "❌ Пристрій не знайдено"
            
            elif isinstance(devices, dict) and not devices.get("success"):
                # Помилка API
                error_msg = devices.get("msg", "Невідома помилка")
                return f"❌ Помилка Tuya: {error_msg}"
            
            else:
                return "❌ Помилка отримання даних"
                
        except Exception as e:
            return f"❌ Помилка підключення: {str(e)}"