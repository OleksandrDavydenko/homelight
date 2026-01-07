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
        """Перевірка статусу світла - версія з детальним дебагом"""
        try:
            print(f"🔧 DEBUG: Початок перевірки світла")
            print(f"🔧 DEBUG: Access ID: {self.access_id[:10]}...")
            print(f"🔧 DEBUG: Device ID: {self.device_id}")
            print(f"🔧 DEBUG: Region: {self.region}")
            
            # Підключення до Tuya Cloud
            print(f"🔧 DEBUG: Підключення до Tuya Cloud...")
            cloud = tinytuya.Cloud(
                apiRegion=self.region,
                apiKey=self.access_id,
                apiSecret=self.access_secret,
                apiDeviceID=self.device_id
            )
            
            # Отримуємо список пристроїв
            print(f"🔧 DEBUG: Виклик cloud.getdevices()...")
            response = cloud.getdevices()
            
            # Детальний вивід відповіді
            print(f"📡 DEBUG: Відповідь getdevices():")
            print(f"📡 Тип відповіді: {type(response)}")
            print(f"📡 Повна відповідь: {json.dumps(response, indent=2, ensure_ascii=False)}")
            
            # Визначаємо тип відповіді
            if isinstance(response, list):
                print(f"✅ DEBUG: Отримано список з {len(response)} пристроїв")
                
                # Успішно отримали список пристроїв
                our_device = None
                for idx, device in enumerate(response):
                    if isinstance(device, dict):
                        device_id = device.get("id", "немає")
                        print(f"🔍 DEBUG: Пристрій #{idx+1}: ID={device_id}, Name={device.get('name')}")
                        if device.get("id") == self.device_id:
                            our_device = device
                            print(f"🎯 DEBUG: Знайдено наш пристрій!")
                
                if our_device:
                    device_name = our_device.get("name", "Розетка")
                    online = our_device.get("online", False)
                    print(f"📱 DEBUG: Назва пристрою: {device_name}")
                    print(f"🌐 DEBUG: Статус online: {online}")
                    
                    if online:
                        # Отримуємо детальний статус
                        print(f"🔧 DEBUG: Виклик cloud.getstatus()...")
                        status_response = cloud.getstatus(self.device_id)
                        
                        print(f"📡 DEBUG: Відповідь getstatus():")
                        print(f"📡 Тип відповіді: {type(status_response)}")
                        if isinstance(status_response, dict):
                            print(f"📡 Повна відповідь: {json.dumps(status_response, indent=2, ensure_ascii=False)}")
                        
                        if isinstance(status_response, dict) and status_response.get("success"):
                            print(f"✅ DEBUG: Успішно отримали статус пристрою")
                            # Успішно отримали статус
                            for idx, item in enumerate(status_response.get("result", [])):
                                print(f"📊 DEBUG: Параметр #{idx+1}: {item}")
                                if item.get("code") == "cur_voltage":
                                    voltage = item.get("value", 0) / 10.0
                                    print(f"⚡ DEBUG: Напруга: {voltage} В")
                                    if voltage > 100:
                                        return f"✅ СВІТЛО Є!\n\n📱 Пристрій: {device_name}\n🔌 Напруга: {voltage:.1f} В"
                                    else:
                                        return f"❌ СВІТЛА НЕМАЄ!\n\n📱 Пристрій: {device_name}\n🔌 Напруга: {voltage:.1f} В"
                            
                            # Якщо напругу не знайдено
                            print(f"⚠️ DEBUG: Напругу не знайдено в відповіді")
                            return f"⚠️ Пристрій онлайн\n📱 {device_name}\nℹ️ Напруга не визначена"
                        else:
                            # Помилка отримання статусу
                            print(f"❌ DEBUG: Не вдалося отримати статус пристрою")
                            return f"⚠️ Пристрій онлайн\n📱 {device_name}\nℹ️ Не вдалося отримати детальний статус"
                    else:
                        # Пристрій офлайн
                        update_time = our_device.get("update_time", 0)
                        offline_min = (int(time.time()) - update_time) // 60
                        print(f"🔴 DEBUG: Пристрій OFFLINE, офлайн {offline_min} хв.")
                        return f"🔴 ПРИСТРІЙ OFFLINE\n\n📱 Пристрій: {device_name}\n⏱️ Офлайн вже: {offline_min} хв."
                else:
                    # Пристрій не знайдено
                    print(f"❌ DEBUG: Пристрій {self.device_id} не знайдено в списку")
                    return "❌ Пристрій не знайдено"
            
            elif isinstance(response, dict):
                print(f"📋 DEBUG: Отримано словник (можливо помилка)")
                # Можливо, це помилка
                if response.get("success") is False:
                    error_msg = response.get("msg", "Невідома помилка")
                    error_code = response.get("code", "немає")
                    print(f"❌ DEBUG: Помилка Tuya: {error_msg}, код: {error_code}")
                    return f"❌ Помилка Tuya: {error_msg}"
                else:
                    # Невідомий формат словника
                    print(f"⚠️ DEBUG: Невідомий формат словника")
                    return "❌ Невідомий формат відповіді"
            else:
                print(f"⚠️ DEBUG: Невідомий тип відповіді: {type(response)}")
                return "❌ Невідомий тип відповіді"
                
        except Exception as e:
            print(f"💥 DEBUG: Виняток: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            return f"❌ Помилка підключення: {str(e)}"