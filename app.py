import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    CallbackContext,
    ChatMemberHandler,
)
from light_checker import LightChecker  # Переконайтесь, що ви імпортуєте цей клас
from config import TELEGRAM_TOKEN

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

light_checker = LightChecker()  # Ініціалізація LightChecker

async def send_welcome_message(update: Update, context: CallbackContext) -> None:
    """Відправка повідомлення з кнопкою 'Перевірити наявність електроенергії' при вході в чат"""
    user = update.effective_user
    keyboard = [
        [InlineKeyboardButton("🔌 Перевірити наявність електроенергії", callback_data='check_light')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    welcome_message = (
        f"👋 Вітаю, {user.first_name}!\n\n"
        "Я бот для перевірки наявності електроенергії.\n"
        "Натисніть кнопку нижче, щоб перевірити статус."
    )

    await update.message.reply_text(welcome_message, reply_markup=reply_markup)

async def button_callback(update: Update, context: CallbackContext) -> None:
    """Обробник натискання на кнопки"""
    query = update.callback_query
    await query.answer()

    if query.data == 'check_light':
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

async def chat_member_handler(update: Update, context: CallbackContext) -> None:
    """Обробник подій зміни статусу користувача в чаті"""
    # Якщо користувач приєднався до чату, відправляємо йому кнопку "Перевірити наявність електроенергії"
    if update.chat_member.new_chat_member.status == "member":
        await send_welcome_message(update, context)

def main() -> None:
    """Запуск бота"""
    # Створюємо додаток
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Додаємо обробники команд
    application.add_handler(CommandHandler("start", send_welcome_message))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("check", check_command))

    # Додаємо обробник зміни статусу члена чату (коли користувач приєднується)
    application.add_handler(ChatMemberHandler(chat_member_handler))  # Правильний спосіб

    # Додаємо обробник кнопок
    application.add_handler(CallbackQueryHandler(button_callback))

    # Запускаємо бота
    print("🤖 Бот запущено...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
