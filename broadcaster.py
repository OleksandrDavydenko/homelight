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
    last_status = None

    while True:
        try:
            # Якщо check_light_status є блокуючою, виконуємо в окремому потоці
            status = await asyncio.to_thread(checker.check_light_status)

            if last_status is None:
                last_status = status
                logger.info("Initial status: %s", status)
            elif status != last_status:
                logger.info("Status changed: %s -> %s", last_status, status)
                message = f"🔔 Зміна статусу електроенергії:\n\n{status}"
                await notify_subscribers(bot, message)
                last_status = status
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
