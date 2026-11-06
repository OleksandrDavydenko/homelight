"""Клавіатура з кнопками під полем вводу."""
from telegram import KeyboardButton, ReplyKeyboardMarkup

# Тексти кнопок не змінювати: їх надсилають клавіатури, які вже є в користувачів
BTN_SUBSCRIBE = "🔔 Підписатись"
BTN_UNSUBSCRIBE = "🔕 Відписатись"
BTN_CHECK = "🔌 Перевірити наявність електроенергії"
BTN_INFO = "ℹ️ Довідка"


def build_keyboard(subscribed: bool) -> ReplyKeyboardMarkup:
    # Без selective: з ним Telegram показує клавіатуру лише згаданим користувачам
    # або автору повідомлення, на яке бот відповідає цитатою, — а в приватному
    # чаті бот відповідає без цитати, і клавіатуру не бачив ніхто.
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_UNSUBSCRIBE if subscribed else BTN_SUBSCRIBE)],
            [KeyboardButton(BTN_CHECK)],
            [KeyboardButton(BTN_INFO)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
