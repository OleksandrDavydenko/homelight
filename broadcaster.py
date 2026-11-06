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

import db
from light_checker import LightChecker, StateTracker, format_alert

logger = logging.getLogger(__name__)

POLL_INTERVAL = 90      # с між звичайними перевірками
CONFIRM_INTERVAL = 20   # с до повторної перевірки, коли стан, схоже, змінився
SEND_DELAY = 0.05       # с між повідомленнями (ліміт Telegram ~30 повідомлень/с)


async def _send(bot: Bot, user_id: int, text: str) -> bool:
    """Надсилає повідомлення одному користувачу. True — якщо успішно."""
    for attempt in (1, 2):
        try:
            await bot.send_message(chat_id=user_id, text=text)
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


async def notify_subscribers(bot: Bot, message: str) -> int:
    """Розсилає повідомлення всім підписникам. Повертає кількість успішних відправок."""
    user_ids = await asyncio.to_thread(db.get_all_subscribed_users)
    logger.info("Розсилка %d підписникам: %s", len(user_ids), message.splitlines()[0])
    sent = 0
    for user_id in user_ids:
        if await _send(bot, user_id, message):
            sent += 1
        await asyncio.sleep(SEND_DELAY)
    logger.info("Розсилку завершено: %d/%d", sent, len(user_ids))
    return sent


async def run_check(bot: Bot, checker: LightChecker, tracker: StateTracker) -> float:
    """Одна перевірка: читання → підтвердження зміни → збереження → розсилка.

    Повертає, через скільки секунд робити наступну перевірку.
    """
    reading = await asyncio.to_thread(checker.get_reading)
    change = tracker.observe(reading, int(time.time()))

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


async def announcement_job(bot: Bot, announcement_id: str, send_at: float,
                           deadline: float, text: str) -> bool:
    """Один раз надсилає оголошення підписникам о send_at. True — якщо надіслано зараз.

    Якщо воркер стартував після send_at, але до deadline, надсилає одразу.
    Повторну відправку (наприклад, після перезапуску воркера) блокує позначка в БД.
    """
    try:
        if await asyncio.to_thread(db.is_announcement_sent, announcement_id):
            logger.info("Оголошення %s уже надіслано раніше", announcement_id)
            return False
        if time.time() > deadline:
            logger.info("Оголошення %s застаріло, не надсилаю", announcement_id)
            return False

        delay = send_at - time.time()
        if delay > 0:
            logger.info("Оголошення %s заплановано через %d с", announcement_id, delay)
            await asyncio.sleep(delay)

        if not await asyncio.to_thread(db.claim_announcement, announcement_id, int(time.time())):
            logger.info("Оголошення %s уже надсилається іншим процесом", announcement_id)
            return False
        sent = await notify_subscribers(bot, text)
        logger.info("Оголошення %s надіслано %d підписникам", announcement_id, sent)
        return True
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Не вдалося надіслати оголошення %s", announcement_id)
        return False


async def monitor_loop(bot: Bot, checker: LightChecker, tracker: StateTracker) -> None:
    logger.info("Фонова перевірка світла запущена")
    while True:
        delay = POLL_INTERVAL
        try:
            delay = await run_check(bot, checker, tracker)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Помилка під час перевірки світла")
        await asyncio.sleep(delay)
