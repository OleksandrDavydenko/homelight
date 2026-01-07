import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackContext,
    filters
)
from light_checker import LightChecker
from config import TELEGRAM_TOKEN

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Ініціалізація перевірки світла
light_checker = LightChecker()

# Створюємо клавіатуру з однією кнопкою
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["🔌 Перевірити наявність електроенергії"]],
    resize_keyboard=True,
    one_time_keyboard=False
)

async def start(update: Update, context: CallbackContext) -> None:
    """Обробник команди /start"""
    user = update.effective_user
    welcome_message = (
        f"👋 Вітаю, {user.first_name}!\n\n"
        "Я бот для перевірки наявності електроенергії.\n"
        "Натисніть кнопку нижче, щоб перевірити статус."
    )
    
    await update.message.reply_text(
        welcome_message,
        reply_markup=MAIN_KEYBOARD
    )

async def check_light(update: Update, context: CallbackContext) -> None:
    """Обробник для перевірки світла"""
    # Відправляємо повідомлення про початок перевірки
    processing_message = await update.message.reply_text(
        "🔄 Перевіряю наявність електроенергії...",
        reply_markup=MAIN_KEYBOARD
    )
    
    try:
        # Виконуємо перевірку
        result = light_checker.check_light_status()
        
        # Редагуємо попереднє повідомлення з результатом
        await processing_message.edit_text(
            f"📊 РЕЗУЛЬТАТ ПЕРЕВІРКИ:\n\n{result}",
            reply_markup=MAIN_KEYBOARD
        )
        
    except Exception as e:
        logger.error(f"Помилка при перевірці світла: {e}")
        await processing_message.edit_text(
            "❌ Сталася помилка при перевірці. Спробуйте пізніше.",
            reply_markup=MAIN_KEYBOARD
        )

async def handle_message(update: Update, context: CallbackContext) -> None:
    """Обробник текстових повідомлень"""
    text = update.message.text
    
    if text == "🔌 Перевірити наявність електроенергії":
        await check_light(update, context)
    else:
        await update.message.reply_text(
            "Натисніть кнопку 'Перевірити наявність електроенергії' для перевірки статусу.",
            reply_markup=MAIN_KEYBOARD
        )

def main() -> None:
    """Запуск бота"""
    # Створюємо додаток
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Додаємо обробники команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Запускаємо бота
    print("🤖 Бот запущено...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()