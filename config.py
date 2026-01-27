import os

# Налаштування Shelly API
SHELLY_AUTH_KEY = os.getenv("SHELLY_AUTH_KEY", "YOUR_SHELLY_AUTH_KEY")
SHELLY_BASE_URL = "https://shelly-237-eu.shelly.cloud"
TARGET_MAC = "48f6eeb6ec00"  # Можна залишити None для автоматичного вибору

# Таймзона для відображення часу
TIMEZONE = os.getenv("TIMEZONE", "Europe/Kyiv")

# Налаштування Telegram бота
TELEGRAM_TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")