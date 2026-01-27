import os

""" # Конфігурація зі змінних середовища (безпечніше для Heroku)
ACCESS_ID = os.getenv("ACCESS_ID", "")
ACCESS_SECRET = os.getenv("ACCESS_SECRET", "")
DEVICE_ID = os.getenv("DEVICE_ID", "")

# Налаштування Telegram бота
TELEGRAM_TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")

TUYA_REGION = os.getenv("TUYA_REGION", "eu")
# Таймзона для відображення часу (наприклад, 'Europe/Kyiv')
TIMEZONE = os.getenv("TIMEZONE", "Europe/Kyiv") """


SHELLY_AUTH_KEY = os.getenv("SHELLY_AUTH_KEY", "YOUR_SHELLY_AUTH_KEY")
SHELLY_BASE_URL = "https://shelly-237-eu.shelly.cloud"
TARGET_MAC = "48f6eeb6ec00"  # Можна залишити None для автоматичного вибору