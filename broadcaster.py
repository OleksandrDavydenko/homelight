import asyncio
import logging
import time

from telegram import Bot
from config import TELEGRAM_TOKEN
from db import get_all_subscribed_users
from light_checker import LightChecker, get_current_state, set_current_state

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

POLL_INTERVAL = 60  # seconds

async def notify_subscribers(bot: Bot, message: str):
    """Відправка повідомлення всім підписаним користувачам"""
    user_ids=[203148640]
    #user_ids = get_all_subscribed_users()
    logger.info("notify_subscribers: retrieved %d subscribed users", len(user_ids))
    if not user_ids:
        logger.info("No subscribed users to notify")
        return

    logger.info("Sending notification to %d users", len(user_ids))
    for user_id in user_ids:
        try:
            logger.info("Sending message to user %s", user_id)
            await bot.send_message(chat_id=user_id, text=message)
            logger.info("Message sent successfully to user %s", user_id)
            await asyncio.sleep(0.05)  # small pause to avoid hitting rate limits
        except Exception as e:
            logger.exception("Failed to send message to %s: %s", user_id, e)

async def monitor_loop():
    bot = Bot(token=TELEGRAM_TOKEN)
    checker = LightChecker()
    iteration = 0

    logger.info("monitor_loop started")
    # Завантажуємо попередній стан зі змінної пам'яті
    last_state = get_current_state()
    if last_state:
        logger.info("Loaded saved state from memory: %s", last_state)
    
    while True:
        iteration += 1
        logger.info("=== Iteration %d ===", iteration)
        try:
            # Отримуємо сирий статус без форматування
            logger.info("Getting device status...")
            raw = await asyncio.to_thread(checker.get_real_device_status)
            logger.info("Raw status: %s", raw)
            
            # Поточний стан: key для порівняння + значення напруги
            current_state = {
                "has_light": raw.get("has_light"),
                "online": raw.get("online"),
                "reason": raw.get("reason"),
                "voltage_status": raw.get("voltage_status"),
                "voltage": raw.get("voltage")
            }
            logger.info("Current state: %s", current_state)

            if last_state is None:
                # Перший запуск - зберігаємо стан
                last_state = current_state
                logger.info("Initial state: %s", current_state)
                set_current_state(current_state)
            elif (
                current_state["has_light"] != last_state.get("has_light") or
                current_state["online"] != last_state.get("online") or
                current_state["reason"] != last_state.get("reason") or
                current_state["voltage_status"] != last_state.get("voltage_status") or
                current_state["voltage"] != last_state.get("voltage")
            ):
                # Стан змінився - надсилаємо повідомлення
                logger.warning(
                    "STATE CHANGED: %s -> %s",
                    {k: v for k, v in last_state.items()},
                    current_state
                )
                logger.info("Formatting full message...")
                message = await asyncio.to_thread(checker.check_light_status)
                logger.info("Calling notify_subscribers...")
                await notify_subscribers(bot, message)
                logger.info("notify_subscribers completed")
                
                # Оновлюємо стан у пам'яті
                set_current_state(current_state)
                last_state = current_state
            else:
                logger.debug("No change in state")
        except Exception as e:
            logger.exception("Error while checking status: %s", e)

        logger.info("Sleeping for %d seconds...", POLL_INTERVAL)
        await asyncio.sleep(POLL_INTERVAL)


def main():
    logger.info("Starting broadcaster...")
    asyncio.run(monitor_loop())


if __name__ == '__main__':
    main()
