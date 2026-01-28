
# broadcaster.py - спрощена версія
import asyncio
import logging
import time

from telegram import Bot
from db import get_all_subscribed_users
from config import TELEGRAM_TOKEN
from light_checker import LightChecker

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

POLL_INTERVAL = 90  # seconds

async def notify_subscribers(bot: Bot, message: str):
    """Відправка повідомлення всім підписаним користувачам"""

    user_ids=[203148640]
    #user_ids = get_all_subscribed_users()
    logger.info("Sending notification to %d users", len(user_ids))
    
    for user_id in user_ids:
        try:
            await bot.send_message(chat_id=user_id, text=message)
            logger.info("Message sent successfully to user %s", user_id)
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.exception("Failed to send message to %s: %s", user_id, e)

async def monitor_loop():
    bot = Bot(token=TELEGRAM_TOKEN)
    checker = LightChecker()
    
    logger.info("monitor_loop started - using check_for_alerts() only")
    
    while True:
        try:
            # Використовуємо ТІЛЬКИ check_for_alerts() - він сам відстежує зміни
            alert_message = checker.check_for_alerts()
            
            if alert_message:
                logger.info(f"Alert detected: {alert_message[:50]}...")
                await notify_subscribers(bot, alert_message)
            else:
                logger.debug("No alerts - status unchanged")
                
        except Exception as e:
            logger.exception("Error while checking status: %s", e)

        logger.info("Sleeping for %d seconds...", POLL_INTERVAL)
        await asyncio.sleep(POLL_INTERVAL)

def main():
    logger.info("Starting broadcaster...")
    asyncio.run(monitor_loop())

if __name__ == '__main__':
    main()