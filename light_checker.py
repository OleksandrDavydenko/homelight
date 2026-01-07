import tinytuya
import time
import logging
from config import ACCESS_ID, ACCESS_SECRET, DEVICE_ID, TUYA_REGION, DEBUG

logger = logging.getLogger(__name__)

class LightChecker:
    def __init__(self):
        self.access_id = ACCESS_ID
        self.access_secret = ACCESS_SECRET
        self.device_id = DEVICE_ID
        self.region = TUYA_REGION
        
    def get_real_device_status(self):
        """Отримання реального статусу пристрою"""
        logger.info(f"🔄 Перевірка пристрою {self.device_id} в регіоні {self.region}")
        
        try:
            # Підключаємося до Tuya Cloud
            cloud = tinytuya.Cloud(
                apiRegion=self.region,
                apiKey=self.access_id,
                apiSecret=self.access_secret,
                apiDeviceID=self.device_id
            )
            
            logger.info("📡 Запит до Tuya API...")
            
            # Отримуємо список пристроїв
            devices_list = cloud.getdevices()
            
            if DEBUG:
                logger.info(f"🔍 Дані пристроїв: {devices_list}")
            
            # Перевіряємо тип відповіді
            if isinstance(devices_list, list):
                logger.info(f"✅ Отримано список з {len(devices_list)} пристроїв")
                
                # Шукаємо наш пристрій
                our_device = None
                for device in devices_list:
                    if isinstance(device, dict) and device.get("id") == self.device_id:
                        our_device = device
                        break
                
                if our_device:
                    device_name = our_device.get("name", "Невідомий пристрій")
                    online_status = our_device.get("online", False)
                    logger.info(f"📱 Знайдено пристрій: {device_name}")
                    logger.info(f"🌐 Статус: {'ONLINE' if online_status else 'OFFLINE'}")
                    
                    if not online_status:
                        update_time = our_device.get("update_time", 0)
                        current_time = int(time.time())
                        offline_minutes = (current_time - update_time) // 60
                        
                        return {
                            "has_light": False,
                            "online": False,
                            "device_name": device_name,
                            "message": f"🔴 ПРИСТРІЙ OFFLINE\n📱 {device_name}\n⏱️ Офлайн вже: {offline_minutes} хв.",
                            "offline_minutes": offline_minutes
                        }
                    else:
                        # Пристрій онлайн - отримуємо детальний статус
                        logger.info("📊 Отримання детального статусу...")
                        status_data = cloud.getstatus(self.device_id)
                        
                        if DEBUG:
                            logger.info(f"🔍 Статус пристрою: {status_data}")
                        
                        if isinstance(status_data, dict) and status_data.get("success", False):
                            return self._analyze_current_status(status_data, our_device)
                        else:
                            return {
                                "has_light": None,
                                "online": True,
                                "device_name": device_name,
                                "message": f"⚠️ Пристрій онлайн, але дані недоступні\n📱 {device_name}"
                            }
                else:
                    # Пристрій не знайдено
                    device_names = [d.get("name", "Без назви") for d in devices_list]
                    return {
                        "has_light": None,
                        "online": None,
                        "message": f"❌ Пристрій {self.device_id} не знайдено\n📱 Доступні пристрої: {', '.join(device_names)}"
                    }
            elif isinstance(devices_list, dict):
                # Старий формат відповіді
                if devices_list.get("success", False):
                    devices = devices_list.get("result", [])
                    # Обробка аналогічна вище
                    # ... (додайте обробку старого формату)
                else:
                    error_msg = devices_list.get("msg", "Невідома помилка")
                    return {
                        "has_light": None,
                        "online": None,
                        "message": f"❌ Помилка Tuya API: {error_msg}"
                    }
            else:
                return {
                    "has_light": None,
                    "online": None,
                    "message": f"❌ Невірний формат відповіді: {type(devices_list)}"
                }
                
        except Exception as e:
            logger.exception(f"💥 Критична помилка: {e}")
            return {
                "has_light": None,
                "online": None,
                "message": f"❌ Помилка підключення: {str(e)}"
            }
    
    def _analyze_current_status(self, status_data, device_info):
        """Аналіз поточного статусу пристрою"""
        result = status_data.get("result", [])
        device_name = device_info.get("name", "Невідомий пристрій")
        
        logger.info(f"🔍 Аналіз даних: {result}")
        
        # Знаходимо ключові параметри
        voltage = None
        power = None
        current = None
        switch_state = None
        
        for item in result:
            if isinstance(item, dict):
                code = item.get("code")
                value = item.get("value")
                
                if code == "cur_voltage" and value is not None:
                    voltage = value / 10.0
                elif code == "cur_power" and value is not None:
                    power = value / 10.0
                elif code == "cur_current" and value is not None:
                    current = value / 1000.0
                elif code == "switch_1":
                    switch_state = value
        
        # Визначаємо, чи є світло
        if voltage is not None:
            if voltage > 100:
                if (power is not None and power > 0) or (current is not None and current > 0):
                    message = f"✅ СВІТЛО Є!\n📱 {device_name}\n🔌 Напруга: {voltage:.1f} В"
                    if power:
                        message += f"\n💡 Потужність: {power:.1f} Вт"
                    has_light = True
                else:
                    message = f"⚠️ НАПРУГА Є ({voltage:.1f} В), але немає споживання\n📱 {device_name}\nМожливо, нічого не підключено"
                    has_light = True
            else:
                message = f"❌ СВІТЛА НЕМАЄ!\n📱 {device_name}\n🔌 Напруга: {voltage:.1f} В"
                has_light = False
        else:
            message = f"⚠️ НЕ ВДАЛОСЯ ОТРИМАТИ НАПРУГУ\n📱 {device_name}"
            has_light = None
        
        logger.info(f"📋 Результат: {message}")
        
        return {
            "has_light": has_light,
            "online": True,
            "voltage": voltage,
            "power": power,
            "current": current,
            "switch_state": switch_state,
            "device_name": device_name,
            "message": message
        }
    
    def check_light_status(self):
        """Основна функція для перевірки статусу світла"""
        try:
            status = self.get_real_device_status()
            return status.get("message", "⚠️ Не вдалося отримати інформацію")
        except Exception as e:
            logger.exception(f"Помилка у check_light_status: {e}")
            return f"❌ Внутрішня помилка: {str(e)}"