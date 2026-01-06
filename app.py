import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler

# Налаштування логування
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

async def start(update: Update, context):
    """Просто відповідає на /start"""
    user = update.effective_user
    logger.info(f"Користувач {user.id} написав /start")
    
    try:
        await update.message.reply_text(
            f"👋 Привіт, {user.first_name}!\n"
            f"🆔 Твій ID: {user.id}\n"
            "🎉 Бот працює на Heroku!"
        )
    except Exception as e:
        logger.error(f"Помилка при відповіді на /start: {e}")
        await update.message.reply_text("❌ Виникла помилка. Спробуйте ще раз.")

async def test(update: Update, context):
    """Тестова команда"""
    try:
        await update.message.reply_text("✅ Бот працює!")
    except Exception as e:
        logger.error(f"Помилка при відповіді на /test: {e}")
        await update.message.reply_text("❌ Виникла помилка. Спробуйте ще раз.")

def main():
    """Основна функція для запуску бота"""
    # Перевірка на наявність токена
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не знайдено! Бот не може працювати без токена.")
        return
    
    try:
        app = Application.builder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("test", test))

        logger.info("Бот успішно запущено.")
        app.run_polling()
    except Exception as e:
        logger.error(f"Помилка при запуску бота: {e}")

if __name__ == "__main__":
    main()
