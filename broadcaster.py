import asyncio
import logging
import time

from telegram import Bot
from config import TELEGRAM_TOKEN
from db import get_all_subscribed_users
from light_checker import LightChecker

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

POLL_INTERVAL = 90  # seconds

async def notify_subscribers(bot: Bot, message: str):
    user_ids = get_all_subscribed_users()
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
#перевірити чи працює

async def monitor_loop():
    bot = Bot(token=TELEGRAM_TOKEN)
    checker = LightChecker()
    last_key = None
    iteration = 0

    logger.info("monitor_loop started")
    while True:
        iteration += 1
        logger.info("=== Iteration %d ===", iteration)
        try:
            # Отримуємо сирий статус без форматування (щоб порівнювати стабільні поля)
            logger.info("Getting device status...")
            raw = await asyncio.to_thread(checker.get_real_device_status)
            logger.info("Raw status: %s", raw)
            # Ключ, за яким будемо визначати зміну — has_light, online, reason
            key = (raw.get("has_light"), raw.get("online"), raw.get("reason"))
            logger.info("Status key: %s", key)

            if last_key is None:
                last_key = key
                logger.info("Initial status key: %s", key)
            elif key != last_key:
                logger.warning("STATUS CHANGED: %s -> %s", last_key, key)
                # Форматуємо повне повідомлення для відправки безпосередньо перед розсилкою
                logger.info("Formatting full message...")
                message = await asyncio.to_thread(checker.check_light_status)
                logger.info("Calling notify_subscribers...")
                await notify_subscribers(bot, message)
                logger.info("notify_subscribers completed")
                last_key = key
            else:
                logger.debug("No change in status")
        except Exception as e:
            logger.exception("Error while checking status: %s", e)

        logger.info("Sleeping for %d seconds...", POLL_INTERVAL)
        await asyncio.sleep(POLL_INTERVAL)


def main():
    logger.info("Starting broadcaster...")
    asyncio.run(monitor_loop())


if __name__ == '__main__':
    main()
