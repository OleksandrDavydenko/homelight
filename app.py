import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Налаштування логування
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

# Перевірка наявності токена
if not BOT_TOKEN:
    logger.error("BOT_TOKEN не знайдено! Бот не може працювати без токена.")
    raise ValueError("BOT_TOKEN не знайдено!")

# Обробка команди /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просто відповідає на /start"""
    user = update.effective_user
    logger.info(f"Команда /start від користувача {user.id}")  # Логування отримання команди
    
    try:
        # Відповідь на команду /start
        await update.message.reply_text(
            f"👋 Привіт, {user.first_name}!\n"
            f"🆔 Твій ID: {user.id}\n"
            "🎉 Бот працює на Heroku!"
        )
    except Exception as e:
        # Логування помилок при відповіді
        logger.error(f"Помилка при відповіді на /start: {e}")
        await update.message.reply_text("❌ Виникла помилка. Спробуйте ще раз.")

# Обробка тестової команди /test
async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тестова команда"""
    logger.info("Команда /test отримана")
    try:
        # Відповідь на команду /test
        await update.message.reply_text("✅ Бот працює!")
    except Exception as e:
        # Логування помилок при відповіді
        logger.error(f"Помилка при відповіді на /test: {e}")
        await update.message.reply_text("❌ Виникла помилка. Спробуйте ще раз.")

def main():
    """Основна функція для запуску бота"""
    try:
        # Створення додатку бота
        app = Application.builder().token(BOT_TOKEN).build()

        # Додавання обробників команд
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("test", test))

        logger.info("Бот успішно запущено.")

        # Запускаємо бота через polling
        app.run_polling()

    except Exception as e:
        # Логування помилок при запуску бота
        logger.error(f"Помилка при запуску бота: {e}")

if __name__ == "__main__":
    main()
