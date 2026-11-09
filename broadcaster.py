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

    Якщо дані з Shelly Cloud недоступні довше за alert_after, надсилає одне
    попередження, а коли дані знову з'являються — повідомлення про відновлення.
    Зникнення інтернету вдома відстежує окремо InternetWatch.
    """

    def __init__(self, chat_id: int | None, alert_after: float = ADMIN_ALERT_AFTER):
        self.chat_id = chat_id
        self.alert_after = alert_after
        self._unknown_since: int | None = None
        self._alerted = False

    async def observe(self, bot: Bot, reading: Reading, now: int) -> None:
        if not self.chat_id:
            return

        # internet=False — дані з Shelly Cloud отримано, просто вдома немає інтернету
        # (про це повідомляє InternetWatch), тож для цієї перевірки все гаразд
        if reading.known or reading.internet is False:
            if self._alerted:
                gap = format_duration(now - self._unknown_since)
                await _send(bot, self.chat_id, f"✅ Дані з Shelly знову надходять (не було {gap}).")
            self._unknown_since, self._alerted = None, False
            return

        if self._unknown_since is None:
            self._unknown_since = now
        if self._alerted or now - self._unknown_since < self.alert_after:
            return

        self._alerted = True
        since = format_moment(self._unknown_since, now)
        error = (reading.error or "невідома помилка")[:300]
        await _send(bot, self.chat_id, f"⚠️ Не вдається отримати дані з Shelly з {since}:\n{error}")


class InternetWatch:
    """Окрема перевірка інтернету вдома — повідомлення лише власнику, не підписникам.

    Роутер перевіряється на кожній ітерації, незалежно від Shelly. Якщо він
    мовчить довше за alert_after, власник отримує одне попередження, а коли
    роутер знову відповідає — повідомлення про відновлення.
    """

    def __init__(self, chat_id: int | None, alert_after: float = ADMIN_ALERT_AFTER):
        self.chat_id = chat_id
        self.alert_after = alert_after
        self._down_since: int | None = None
        self._alerted = False

    @property
    def pending(self) -> bool:
        """Роутер уже мовчить, але попередження ще не надіслано."""
        return bool(self.chat_id) and self._down_since is not None and not self._alerted

    async def observe(self, bot: Bot, internet: bool | None, shelly_online: bool | None,
                      now: int) -> None:
        """internet: True/False — чи відповідає роутер; None — перевірка недоступна."""
        if not self.chat_id or internet is None:
            return

        if internet:
            if self._alerted:
                gap = format_duration(now - self._down_since)
                await _send(bot, self.chat_id, f"✅ Інтернет удома відновився (не було {gap}).")
            self._down_since, self._alerted = None, False
            return

        if self._down_since is None:
            self._down_since = now
        if self._alerted or now - self._down_since < self.alert_after:
            return

        self._alerted = True
        text = f"⚠️ Вдома немає інтернету з {format_moment(self._down_since, now)} (роутер не відповідає)."
        if shelly_online:
            text += ("\nАле Shelly на зв'язку, тож вихідний інтернет є — схоже, не проходять "
                     "вхідні з'єднання на реальну IP. Поки так, бот не відрізнить відключення "
                     "світла від збою інтернету.")
        await _send(bot, self.chat_id, text)


async def _check_internet(checker: LightChecker) -> bool | None:
    """Чи є інтернет удома (за роутером); None — перевірка не налаштована чи недоступна."""
    if not getattr(checker, "router_host", None):
        return None
    try:
        # Свіжа перевірка; get_reading потім використає цей самий результат
        _, online = await asyncio.to_thread(checker.check_router)
    except Exception:
        logger.exception("Не вдалося перевірити роутер")
        return None
    # Мовчанню роутера, який жодного разу не відповідав, не довіряємо
    return online if online or checker.router_trusted else None


async def run_check(bot: Bot, checker: LightChecker, tracker: StateTracker,
                    admin_watch: AdminWatch | None = None,
                    internet_watch: InternetWatch | None = None) -> float:
    """Одна перевірка: інтернет удома → читання Shelly → підтвердження зміни →
    збереження → розсилка.

    Повертає, через скільки секунд робити наступну перевірку.
    """
    internet = await _check_internet(checker) if internet_watch is not None else None
    reading = await asyncio.to_thread(checker.get_reading)
    now = int(time.time())

    if admin_watch is not None:
        try:
            await admin_watch.observe(bot, reading, now)
        except Exception:
            logger.exception("Не вдалося надіслати службове сповіщення")

    if internet_watch is not None:
        try:
            await internet_watch.observe(bot, internet, reading.online, now)
        except Exception:
            logger.exception("Не вдалося надіслати сповіщення про інтернет")

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

    pending = tracker.pending or (internet_watch is not None and internet_watch.pending)
    return CONFIRM_INTERVAL if pending else POLL_INTERVAL


async def monitor_loop(bot: Bot, checker: LightChecker, tracker: StateTracker,
                       admin_watch: AdminWatch | None = None,
                       internet_watch: InternetWatch | None = None) -> None:
    logger.info("Фонова перевірка світла запущена")
    while True:
        delay = POLL_INTERVAL
        try:
            delay = await run_check(bot, checker, tracker, admin_watch, internet_watch)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Помилка під час перевірки світла")
        await asyncio.sleep(delay)
