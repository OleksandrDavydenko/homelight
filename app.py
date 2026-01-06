import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Налаштування
BOT_TOKEN = '7507075036:AAGDt6Ycp9xOg3l9210kSLZoAkhgI1t2gqU' #os.getenv("BOT_TOKEN")

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================ TELEGRAM БОТ ================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start - показує кнопку"""
    user = update.effective_user
    
    # Створюємо клавіатуру з однією кнопкою
    keyboard = [
        [InlineKeyboardButton("👋 Натисни мене!", callback_data='hello_button')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Привіт, {user.first_name}! 👋\n\n"
        "Я бот з однією кнопкою на Heroku!\n\n"
        "👇 Натисни кнопку нижче:",
        reply_markup=reply_markup
    )
    
    logger.info(f"Користувач {user.id} написав /start")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник натискання кнопки"""
    query = update.callback_query
    user = query.from_user
    
    # Обробляємо натискання кнопки
    if query.data == 'hello_button':
        await query.answer()  # Закриваємо "завантаження" на кнопці
        
        import random
        messages = [
            f"🎉 Вітаю, {user.first_name}! Ти натиснув кнопку!",
            f"👏 Супер, {user.first_name}! Кнопка працює!",
            f"🚀 Ура, {user.first_name}! Ти це зробив!",
        ]
        
        message = random.choice(messages)
        await query.edit_message_text(
            text=f"{message}\n\n"
                 f"🆔 Твій ID: {user.id}\n\n"
                 "💡 Напиши /start щоб отримати кнопку знову!"
        )
        
        logger.info(f"Користувач {user.id} натиснув кнопку")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    await update.message.reply_text(
        "🤖 Бот з однією кнопкою\n\n"
        "Команди:\n"
        "/start - отримати кнопку\n"
        "/help - довідка"
    )

def main():
    """Запустити бота"""
    if not BOT_TOKEN:
        logger.error("❌ ПОМИЛКА: Не вказано BOT_TOKEN!")
        return
    
    # Створюємо додаток
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Додаємо обробники команд
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info("🤖 Бот запускається...")
    
    # Запускаємо бота
    application.run_polling()

if __name__ == "__main__":
    main()