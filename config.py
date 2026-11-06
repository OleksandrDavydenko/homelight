import os

# Telegram
TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")

# Shelly Cloud
SHELLY_AUTH_KEY = os.getenv("SHELLY_AUTH_KEY")
SHELLY_BASE_URL = os.getenv("SHELLY_BASE_URL", "https://shelly-237-eu.shelly.cloud")
# MAC пристрою; порожнє значення — брати перший пристрій з акаунта
TARGET_MAC = os.getenv("TARGET_MAC", "48f6eeb6ec00") or None

# База даних
DATABASE_URL = os.getenv("DATABASE_URL")

# Таймзона для відображення часу
TIMEZONE = os.getenv("TIMEZONE", "Europe/Kyiv")

_REQUIRED = {
    "BOT_TOKEN": TELEGRAM_TOKEN,
    "SHELLY_AUTH_KEY": SHELLY_AUTH_KEY,
    "DATABASE_URL": DATABASE_URL,
}


def validate() -> None:
    """Зупиняє запуск, якщо не задані обов'язкові змінні середовища."""
    missing = [name for name, value in _REQUIRED.items() if not value]
    if missing:
        raise SystemExit("Не задано змінні середовища: " + ", ".join(missing))
