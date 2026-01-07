import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    CallbackContext,
)
from light_checker import LightChecker
from config import TELEGRAM_TOKEN

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

light_checker = LightChecker()

async def start(update: Update, context: CallbackContext) -> None:
    """Обробник команди /start"""
    keyboard = [
        [InlineKeyboardButton("🔌 Перевірити наявність електроенергії", callback_data='check_light')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    user = update.effective_user
    welcome_message = (
        f"👋 Вітаю, {user.first_name}!\n\n"
        "Я бот для перевірки наявності електроенергії.\n"
        "Натисніть кнопку нижче, щоб перевірити статус."
    )
    
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)

async def button_callback(update: Update, context: CallbackContext) -> None:
    """Обробник натискань на кнопки"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'check_light':
        # Відправляємо повідомлення про обробку
        await query.edit_message_text(text="🔄 Перевіряю наявність електроенергії...")
        
        try:
            # Виконуємо перевірку
            result = light_checker.check_light_status()
            
            # Формуємо нову клавіатуру
            keyboard = [
                [InlineKeyboardButton("🔄 Перевірити ще раз", callback_data='check_light')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Оновлюємо повідомлення з результатом
            await query.edit_message_text(
                text=f"📊 РЕЗУЛЬТАТ ПЕРЕВІРКИ:\n\n{result}",
                reply_markup=reply_markup
            )
            
        except Exception as e:
            logger.error(f"Помилка при перевірці світла: {e}")
            keyboard = [
                [InlineKeyboardButton("🔄 Спробувати ще раз", callback_data='check_light')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                text="❌ Сталася помилка при перевірці. Спробуйте пізніше.",
                reply_markup=reply_markup
            )

async def help_command(update: Update, context: CallbackContext) -> None:
    """Обробник команди /help"""
    keyboard = [
        [InlineKeyboardButton("🔌 Перевірити наявність електроенергії", callback_data='check_light')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    help_text = (
        "Доступні команди:\n"
        "/start - Почати роботу з ботом\n"
        "/check - Перевірити наявність світла\n"
        "/help - Показати це повідомлення\n\n"
        "Просто натисніть кнопку нижче для перевірки."
    )
    
    await update.message.reply_text(help_text, reply_markup=reply_markup)

async def check_command(update: Update, context: CallbackContext) -> None:
    """Обробник команди /check"""
    keyboard = [
        [InlineKeyboardButton("🔌 Перевірити наявність електроенергії", callback_data='check_light')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Натисніть кнопку для перевірки наявності електроенергії:",
        reply_markup=reply_markup
    )

def main() -> None:
    """Запуск бота"""
    # Створюємо додаток
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Додаємо обробники команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("check", check_command))
    
    # Додаємо обробник кнопок
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Запускаємо бота
    print("🤖 Бот запущено...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
