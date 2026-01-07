import os

# Конфігурація зі змінних середовища (безпечніше для Heroku)
ACCESS_ID = os.getenv("ACCESS_ID", "")
ACCESS_SECRET = os.getenv("ACCESS_SECRET", "")
DEVICE_ID = os.getenv("DEVICE_ID", "")

# Налаштування Telegram бота
TELEGRAM_TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")