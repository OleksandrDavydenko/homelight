import tinytuya
import time
from datetime import datetime
from config import ACCESS_ID, ACCESS_SECRET, DEVICE_ID, TUYA_REGION
import logging

logger = logging.getLogger(__name__)

class LightChecker:
    def __init__(self):
        self.access_id = ACCESS_ID
        self.access_secret = ACCESS_SECRET
        self.device_id = DEVICE_ID
        self.region = TUYA_REGION
        
    def get_real_device_status(self):
        """Отримання реального статусу пристрою з перевіркою онлайн статусу"""
        logger.info(f"Перевірка статусу пристрою {self.device_id} в регіоні {self.region}")
        
        try:
            # Підключаємося до Tuya Cloud
            cloud = tinytuya.Cloud(
                apiRegion=self.region,
                apiKey=self.access_id,
                apiSecret=self.access_secret,
                apiDeviceID=self.device_id
            )
            
            logger.info(f"Підключення до Tuya Cloud...")
            
            # 1. Отримуємо інформацію про пристрої
            devices_info = cloud.getdevices()
            
            if devices_info is None:
                logger.error("Tuya API повернув None")
                return {
                    "has_light": None, 
                    "online": None, 
                    "reason": "api_returned_none",
                    "message": "❌ Tuya API не відповідає"
                }
            
            logger.info(f"Відповідь від API: {devices_info}")
            
            if isinstance(devices_info, dict) and devices_info.get("success", False):
                devices_list = devices_info.get("result", [])
                logger.info(f"Знайдено пристроїв: {len(devices_list)}")
                
                # Шукаємо нашу розетку
                our_device = None
                for device in devices_list:
                    if device.get("id") == self.device_id:
                        our_device = device
                        break
                
                if our_device:
                    logger.info(f"Знайдено пристрій: {our_device.get('name')}")
                    
                    # Перевіряємо онлайн статус
                    online_status = our_device.get("online", False)
                    update_time = our_device.get("update_time", 0)
                    current_time = int(time.time())
                    
                    logger.info(f"Статус пристрою: {'ONLINE' if online_status else 'OFFLINE'}")
                    
                    if not online_status:
                        offline_minutes = (current_time - update_time) // 60
                        return {
                            "has_light": False,
                            "online": False,
                            "reason": "device_offline",
                            "offline_since": update_time,
                            "offline_minutes": offline_minutes,
                            "message": f"🔴 РОЗЕТКА OFFLINE\n⏱️ Офлайн вже: {offline_minutes} хвилин"
                        }
                    else:
                        # Якщо пристрій онлайн, отримуємо детальний статус
                        logger.info("Отримання детального статусу...")
                        status_data = cloud.getstatus(self.device_id)
                        
                        if status_data and status_data.get("success", False):
                            return self._analyze_current_status(status_data, our_device)
                        else:
                            logger.error(f"Не вдалося отримати статус: {status_data}")
                            return {
                                "has_light": None, 
                                "online": True, 
                                "reason": "status_unavailable",
                                "message": "⚠️ Пристрій онлайн, але дані недоступні"
                            }
                else:
                    logger.error(f"Пристрій {self.device_id} не знайдено")
                    # Виводимо всі доступні пристрої для налагодження
                    device_list_str = "\n".join([f"{d.get('id')}: {d.get('name')}" for d in devices_list])
                    logger.info(f"Доступні пристрої:\n{device_list_str}")
                    
                    return {
                        "has_light": None, 
                        "online": None, 
                        "reason": "device_not_found",
                        "message": f"❌ Пристрій не знайдено. Доступні пристрої: {len(devices_list)}"
                    }
            else:
                error_msg = devices_info.get("msg", "Невідома помилка")
                error_code = devices_info.get("code", "Невідомий код")
                logger.error(f"Помилка Tuya API: {error_msg} (код: {error_code})")
                
                return {
                    "has_light": None, 
                    "online": None, 
                    "reason": f"api_error_{error_code}",
                    "message": f"❌ Помилка Tuya API: {error_msg}"
                }
                
        except Exception as e:
            logger.exception(f"Критична помилка підключення: {e}")
            return {
                "has_light": None, 
                "online": None, 
                "reason": f"connection_error: {str(e)}",
                "message": f"❌ Помилка підключення до Tuya: {str(e)}"
            }
    
    def _analyze_current_status(self, status_data, device_info):
        """Аналіз поточного статусу пристрою"""
        result = status_data.get("result", [])
        logger.info(f"Дані статусу: {result}")
        
        # Знаходимо ключові параметри
        voltage = None
        power = None
        current = None
        switch_state = None
        
        for item in result:
            code = item.get("code")
            value = item.get("value")
            
            if code == "cur_voltage":
                voltage = value / 10 if value is not None else None
            elif code == "cur_power":
                power = value / 10 if value is not None else None
            elif code == "cur_current":
                current = value / 1000 if value is not None else None
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
        
        logger.info(f"Результат аналізу: {message}")
        
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
        try:
            status = self.get_real_device_status()
            
            # Якщо статус None або помилка
            if status is None:
                return "❌ Не вдалося отримати статус. Спробуйте пізніше."
            
            # Повертаємо повідомлення з результату
            return status.get("message", "⚠️ Не вдалося отримати інформацію")
            
        except Exception as e:
            logger.exception(f"Помилка у check_light_status: {e}")
            return f"❌ Внутрішня помилка бота: {str(e)}"