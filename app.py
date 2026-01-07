import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackContext, filters
from light_checker import LightChecker
from config import TELEGRAM_TOKEN

# Налаштування логування
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Ініціалізація
light_checker = LightChecker()

# Клавіатура з однією кнопкою
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["🔌 Перевірити наявність електроенергії"]],
    resize_keyboard=True
)

async def start(update: Update, context: CallbackContext) -> None:
    """Обробник команди /start"""
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Вітаю, {user.first_name}!\n\nЯ перевіряю наявність електроенергії.\nНатисніть кнопку нижче ⤵️",
        reply_markup=MAIN_KEYBOARD
    )

async def check_light(update: Update, context: CallbackContext) -> None:
    """Перевірка світла"""
    await update.message.reply_text("🔄 Перевіряю...", reply_markup=MAIN_KEYBOARD)
    
    try:
        result = light_checker.check_light_status()
        await update.message.reply_text(f"📊 Результат:\n\n{result}", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        logger.error(f"Помилка: {e}")
        await update.message.reply_text("❌ Помилка. Спробуйте пізніше.", reply_markup=MAIN_KEYBOARD)

async def handle_message(update: Update, context: CallbackContext) -> None:
    """Обробник повідомлень"""
    text = update.message.text
    
    if text == "🔌 Перевірити наявність електроенергії":
        await check_light(update, context)
    else:
        await update.message.reply_text("Натисніть кнопку для перевірки ⤵️", reply_markup=MAIN_KEYBOARD)

def main() -> None:
    """Запуск бота"""
    if not TELEGRAM_TOKEN:
        print("❌ Помилка: TELEGRAM_TOKEN не встановлено!")
        return
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🤖 Бот запущено...")
    application.run_polling()

if __name__ == '__main__':
    main()