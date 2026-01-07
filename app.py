import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, CallbackContext, ChatMemberHandler

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

light_checker = LightChecker()

async def send_welcome_message(update: Update, context: CallbackContext) -> None:
    """Відправка повідомлення з кнопкою 'Підписатись' при вході в чат"""
    user = update.effective_user
    # Кнопка "Підписатись" — KeyboardButton для виведення внизу
    keyboard = [
        [KeyboardButton("Підписатись")]  # Це кнопка в рядку вводу
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

    # Кнопка "Перевірити наявність електроенергії" — InlineButton для кнопки в повідомленні
    inline_keyboard = [
        [InlineKeyboardButton("🔌 Перевірити наявність електроенергії", callback_data='check_light')]
    ]
    inline_reply_markup = InlineKeyboardMarkup(inline_keyboard)

    welcome_message = (
        f"👋 Вітаю, {user.first_name}!\n\n"
        "Я бот для перевірки наявності електроенергії.\n"
        "Натисніть кнопку 'Підписатись' або 'Перевірити наявність електроенергії'."
    )

    # Відправка привітального повідомлення з кнопкою "Підписатись" в полі вводу
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)
    # Додаємо кнопку "Перевірити наявність електроенергії" в повідомленні
    await update.message.reply_text("Натисніть для перевірки", reply_markup=inline_reply_markup)

async def button_callback(update: Update, context: CallbackContext) -> None:
    """Обробник натискання на кнопки"""
    query = update.callback_query
    await query.answer()

    if query.data == 'check_light':
        await query.edit_message_text(text="🔄 Перевіряю наявність електроенергії...")

        try:
            # Виконуємо перевірку
            result = "Стан: Світло є"  # Для тестування можна використовувати статичне повідомлення

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

async def chat_member_handler(update: Update, context: CallbackContext) -> None:
    """Обробник подій зміни статусу користувача в чаті"""
    # Якщо користувач приєднався до чату, відправляємо йому кнопку "Підписатись"
    if update.chat_member.new_chat_member.status == "member":
        await send_welcome_message(update, context)

def main() -> None:
    """Запуск бота"""
    # Створюємо додаток
    application = Application.builder().token('YOUR_BOT_TOKEN').build()

    # Додаємо обробники команд
    application.add_handler(CommandHandler("start", send_welcome_message))

    # Додаємо обробник зміни статусу члена чату (коли користувач приєднується)
    application.add_handler(ChatMemberHandler(chat_member_handler))  # Правильний спосіб

    # Додаємо обробник кнопок
    application.add_handler(CallbackQueryHandler(button_callback))

    # Запускаємо бота
    print("🤖 Бот запущено...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
