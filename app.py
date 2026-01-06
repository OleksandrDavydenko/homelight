import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Налаштування логування
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просто відповідає на /start"""
    user = update.effective_user
    logger.info(f"Команда /start від користувача {user.id}")
    
    try:
        await update.message.reply_text(
            f"👋 Привіт, {user.first_name}!\n"
            f"🆔 Твій ID: {user.id}\n"
            "🎉 Бот працює на Heroku!"
        )
    except Exception as e:
        logger.error(f"Помилка при відповіді на /start: {e}")
        await update.message.reply_text("❌ Виникла помилка. Спробуйте ще раз.")

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тестова команда"""
    logger.info("Команда /test отримана")
    try:
        await update.message.reply_text("✅ Бот працює!")
    except Exception as e:
        logger.error(f"Помилка при відповіді на /test: {e}")
        await update.message.reply_text("❌ Виникла помилка. Спробуйте ще раз.")

def main():
    """Основна функція для запуску бота"""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не знайдено! Бот не може працювати без токена.")
        return
    
    try:
        app = Application.builder().token(BOT_TOKEN).build()

        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("test", test))

        logger.info("Бот успішно запущено.")
        # Запускаємо бота через webhook (треба мати сервер або змінити налаштування на Heroku)
        app.run_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", 5000)), url_path=BOT_TOKEN)
    except Exception as e:
        logger.error(f"Помилка при запуску бота: {e}")

if __name__ == "__main__":
    main()
