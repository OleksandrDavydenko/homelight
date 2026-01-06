import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler

# Логування
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

async def start(update: Update, context):
    """Просто відповідає на /start"""
    user = update.effective_user
    logger.info(f"Користувач {user.id} написав /start")
    
    await update.message.reply_text(
        f"👋 Привіт, {user.first_name}!\n"
        f"🆔 Твій ID: {user.id}\n"
        "🎉 Бот працює на Heroku!"
    )

async def test(update: Update, context):
    """Тестова команда"""
    await update.message.reply_text("✅ Бот працює!")

def main():
    if not BOT_TOKEN:
        logger.error("Немає BOT_TOKEN!")
        return
    
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test))
    
    logger.info("Бот запущено")
    app.run_polling()

if __name__ == "__main__":
    main()