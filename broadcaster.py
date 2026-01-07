import asyncio
import logging
import time

from telegram import Bot
from config import TELEGRAM_TOKEN
from db import get_all_subscribed_users
from light_checker import LightChecker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

POLL_INTERVAL = 60  # seconds

async def notify_subscribers(bot: Bot, message: str):
    user_ids = get_all_subscribed_users()
    if not user_ids:
        logger.info("No subscribed users to notify")
        return

    logger.info("Sending notification to %d users", len(user_ids))
    for user_id in user_ids:
        try:
            await bot.send_message(chat_id=user_id, text=message)
            await asyncio.sleep(0.05)  # small pause to avoid hitting rate limits
        except Exception as e:
            logger.exception("Failed to send message to %s: %s", user_id, e)


async def monitor_loop():
    bot = Bot(token=TELEGRAM_TOKEN)
    checker = LightChecker()
    last_key = None

    while True:
        try:
            # Отримуємо сирий статус без форматування (щоб порівнювати стабільні поля)
            raw = await asyncio.to_thread(checker.get_real_device_status)
            # Ключ, за яким будемо визначати зміну — has_light, online, reason
            key = (raw.get("has_light"), raw.get("online"), raw.get("reason"))

            if last_key is None:
                last_key = key
                logger.info("Initial status key: %s", key)
            elif key != last_key:
                logger.info("Status key changed: %s -> %s", last_key, key)
                # Форматуємо повне повідомлення для відправки безпосередньо перед розсилкою
                message = await asyncio.to_thread(checker.check_light_status)
                await notify_subscribers(bot, message)
                last_key = key
            else:
                logger.debug("No change in status")
        except Exception as e:
            logger.exception("Error while checking status: %s", e)

        await asyncio.sleep(POLL_INTERVAL)


def main():
    logger.info("Starting broadcaster...")
    asyncio.run(monitor_loop())


if __name__ == '__main__':
    main()
