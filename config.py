import os

# Telegram
TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")

# Shelly Cloud
SHELLY_AUTH_KEY = os.getenv("SHELLY_AUTH_KEY")
SHELLY_BASE_URL = os.getenv("SHELLY_BASE_URL", "https://shelly-237-eu.shelly.cloud")
# MAC пристрою; порожнє значення — брати перший пристрій з акаунта
TARGET_MAC = os.getenv("TARGET_MAC", "48f6eeb6ec00") or None

# Домашній роутер на ДБЖ (білий IP або домен). Якщо Shelly офлайн, а роутер
# відповідає, — інтернет у будинку є, отже світла в місті немає. Порожньо — вимкнено.
ROUTER_HOST = os.getenv("ROUTER_HOST", "").strip() or None
ROUTER_PORT = int(os.getenv("ROUTER_PORT", "80"))

# Telegram ID власника бота для службових сповіщень (немає інтернету в будинку,
# Shelly Cloud не відповідає). Порожньо — вимкнено.
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0")) or None


def parse_chat_ids(value: str | None) -> tuple[list[int], list[str]]:
    """Розбирає «123, 456» у ([123, 456], []); неправильні значення — у другий список."""
    ids, invalid = [], []
    for token in (value or "").replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            ids.append(int(token))
        except ValueError:
            invalid.append(token)
    return ids, invalid


# Додаткові Telegram ID (через кому), які отримують лише повідомлення про
# відсутність інтернету вдома. Власник (ADMIN_CHAT_ID) отримує їх і так.
INTERNET_ALERT_CHAT_IDS, _INVALID_INTERNET_ALERT_IDS = parse_chat_ids(
    os.getenv("INTERNET_ALERT_CHAT_IDS")
)


def internet_alert_chat_ids() -> list[int]:
    """Хто отримує повідомлення про інтернет: власник і спеціальні користувачі, без дублікатів."""
    ids = ([ADMIN_CHAT_ID] if ADMIN_CHAT_ID else []) + INTERNET_ALERT_CHAT_IDS
    return list(dict.fromkeys(ids))

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
    if _INVALID_INTERNET_ALERT_IDS:
        raise SystemExit("INTERNET_ALERT_CHAT_IDS має містити Telegram ID через кому, "
                         "неправильні значення: " + ", ".join(_INVALID_INTERNET_ALERT_IDS))
