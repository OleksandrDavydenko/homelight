"""Фонова перевірка стану світла і розсилка сповіщень підписникам.

Запускається з app.py (post_init) як фонова задача в тому ж event loop,
що й бот. Окремо цей модуль не запускається: другий процес-розсилач
дублював би сповіщення.
"""
import asyncio
import logging
import time

from telegram import Bot
from telegram.error import Forbidden, RetryAfter, TelegramError

import config
import db
from keyboard import build_keyboard
from light_checker import (
    LightChecker,
    Reading,
    StateTracker,
    format_alert,
    format_duration,
    format_moment,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL = 90      # с між звичайними перевірками
CONFIRM_INTERVAL = 20   # с до повторної перевірки, коли стан, схоже, змінився
SEND_DELAY = 0.05       # с між повідомленнями (ліміт Telegram ~30 повідомлень/с)
ADMIN_ALERT_AFTER = 180  # с без даних про стан світла, після яких повідомити власника


async def _send(bot: Bot, user_id: int, text: str, reply_markup=None) -> bool:
    """Надсилає повідомлення одному користувачу. True — якщо успішно."""
    for attempt in (1, 2):
        try:
            await bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup)
            return True
        except RetryAfter as e:
            if attempt == 2:
                logger.error("Flood control для %s, повідомлення не надіслано", user_id)
                return False
            logger.warning("Flood control: чекаю %s с", e.retry_after)
            await asyncio.sleep(float(e.retry_after) + 1)
        except Forbidden:
            logger.info("Користувач %s заблокував бота — відписую", user_id)
            try:
                await asyncio.to_thread(db.update_subscription, user_id, False)
            except Exception:
                logger.exception("Не вдалося відписати %s", user_id)
            return False
        except TelegramError as e:
            logger.error("Не вдалося надіслати повідомлення %s: %s", user_id, e)
            return False
    return False


TEST_MODE_KEY = "test_mode"
TEST_MODE_PREFIX = "🧪 Тестовий режим — підписники цього не отримали.\n\n"


def is_test_mode() -> bool:
    """Тестовий режим: сповіщення отримує лише власник (вмикається командою /testmode)."""
    return db.get_setting(TEST_MODE_KEY) == "1"


def set_test_mode(enabled: bool) -> None:
    db.set_setting(TEST_MODE_KEY, "1" if enabled else "0")


async def notify_subscribers(bot: Bot, message: str) -> int:
    """Розсилає повідомлення всім підписникам. Повертає кількість успішних відправок.

    У тестовому режимі повідомлення отримує лише власник (ADMIN_CHAT_ID).
    """
    if await asyncio.to_thread(is_test_mode):
        if not config.ADMIN_CHAT_ID:
            logger.warning("Тестовий режим, але ADMIN_CHAT_ID не задано — сповіщення нікому не надіслано")
            return 0
        logger.info("Тестовий режим: сповіщення лише власнику: %s", message.splitlines()[0])
        sent = await _send(bot, config.ADMIN_CHAT_ID, TEST_MODE_PREFIX + message,
                           reply_markup=build_keyboard(True))
        return int(sent)

    user_ids = await asyncio.to_thread(db.get_all_subscribed_users)
    logger.info("Розсилка %d підписникам: %s", len(user_ids), message.splitlines()[0])
    # Адресати підписані, тож у клавіатурі кнопка «Відписатись».
    # Заодно повертає клавіатуру тим, у кого вона зникла.
    keyboard = build_keyboard(True)
    sent = 0
    for user_id in user_ids:
        if await _send(bot, user_id, message, reply_markup=keyboard):
            sent += 1
        await asyncio.sleep(SEND_DELAY)
    logger.info("Розсилку завершено: %d/%d", sent, len(user_ids))
    return sent


class AdminWatch:
    """Службові сповіщення лише власнику бота (ADMIN_CHAT_ID), не підписникам.

    Якщо стан світла невідомий довше за alert_after (немає інтернету в будинку
    або Shelly Cloud не відповідає), надсилає одне попередження, а коли дані
    знову з'являються — повідомлення про відновлення.
    """

    def __init__(self, chat_id: int | None, alert_after: float = ADMIN_ALERT_AFTER):
        self.chat_id = chat_id
        self.alert_after = alert_after
        self._unknown_since: int | None = None
        self._alerted = False

    async def observe(self, bot: Bot, reading: Reading, now: int) -> None:
        if not self.chat_id:
            return

        if reading.known:
            if self._alerted:
                gap = format_duration(now - self._unknown_since)
                await _send(bot, self.chat_id, f"✅ Зв'язок відновився (не було {gap}).")
            self._unknown_since, self._alerted = None, False
            return

        if self._unknown_since is None:
            self._unknown_since = now
        if self._alerted or now - self._unknown_since < self.alert_after:
            return

        self._alerted = True
        since = format_moment(self._unknown_since, now)
        if reading.internet is False:
            text = (f"⚠️ У будинку немає інтернету з {since} (роутер не відповідає).\n"
                    "Стан світла невідомий, підписникам нічого не надсилається.")
        else:
            error = (reading.error or "невідома помилка")[:300]
            text = f"⚠️ Не вдається отримати дані з Shelly з {since}:\n{error}"
        await _send(bot, self.chat_id, text)


async def run_check(bot: Bot, checker: LightChecker, tracker: StateTracker,
                    admin_watch: AdminWatch | None = None) -> float:
    """Одна перевірка: читання → підтвердження зміни → збереження → розсилка.

    Повертає, через скільки секунд робити наступну перевірку.
    """
    reading = await asyncio.to_thread(checker.get_reading)
    now = int(time.time())

    if admin_watch is not None:
        try:
            await admin_watch.observe(bot, reading, now)
        except Exception:
            logger.exception("Не вдалося надіслати службове сповіщення")

    change = tracker.observe(reading, now)

    if change is not None:
        try:
            await asyncio.to_thread(db.save_light_state, tracker.to_dict())
        except Exception:
            # Стан у пам'яті вже оновлено, тож розсилку все одно робимо
            logger.exception("Не вдалося зберегти стан світла в БД")

        if change.initial:
            logger.info("Початковий стан: світло %s", "Є" if change.has_light else "НЕМАЄ")
        else:
            logger.info("Зміна стану: %s", change)
            await notify_subscribers(bot, format_alert(change))

    return CONFIRM_INTERVAL if tracker.pending else POLL_INTERVAL


async def monitor_loop(bot: Bot, checker: LightChecker, tracker: StateTracker,
                       admin_watch: AdminWatch | None = None) -> None:
    logger.info("Фонова перевірка світла запущена")
    while True:
        delay = POLL_INTERVAL
        try:
            delay = await run_check(bot, checker, tracker, admin_watch)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Помилка під час перевірки світла")
        await asyncio.sleep(delay)
