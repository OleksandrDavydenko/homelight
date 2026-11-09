"""Спеціальні користувачі — отримують повідомлення про інтернет удома.

Власник (ADMIN_CHAT_ID) отримує їх завжди. Спеціальних користувачів власник додає
й видаляє в Telegram командою /special — їх список зберігається в БД
(bot_settings, ключ internet_alert_ids). Змінна INTERNET_ALERT_CHAT_IDS теж
працює, але задані в ній ID кнопками не видалити.
"""
import config
import db

SETTING_KEY = "internet_alert_ids"


def load_ids() -> list[int]:
    """Спеціальні користувачі, додані через /special."""
    ids, _ = config.parse_chat_ids(db.get_setting(SETTING_KEY))
    return list(dict.fromkeys(ids))


def save_ids(ids: list[int]) -> None:
    db.set_setting(SETTING_KEY, ",".join(str(i) for i in dict.fromkeys(ids)))


def recipients(db_ids: list[int]) -> list[int]:
    """Усі, хто отримує повідомлення про інтернет: власник, змінна Heroku й спеціальні користувачі з БД."""
    return list(dict.fromkeys(config.internet_alert_chat_ids() + list(db_ids)))
