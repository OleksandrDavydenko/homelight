import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, CallbackContext, ChatMemberHandler, MessageHandler, filters
from light_checker import LightChecker  # Переконайтесь, що ви імпортуєте цей клас
from config import TELEGRAM_TOKEN
from db import get_user_subscription, update_subscription, add_user  # Потрібні функції для роботи з БД

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Ініціалізація LightChecker
light_checker = LightChecker()

# Функція для створення кнопки підписки/відписки
def get_subscription_keyboard(telegram_id):
    """Отримання клавіатури з кнопкою 'Підписатись' або 'Відписатись' залежно від статусу"""
    user_subscribed = get_user_subscription(telegram_id)
    
    if user_subscribed:  # Якщо користувач підписаний
        return [
            [KeyboardButton("Відписатись")],
            [KeyboardButton("🔌 Перевірити наявність електроенергії")]
        ]
    else:  # Якщо користувач не підписаний
        return [
            [KeyboardButton("Підписатись")],
            [KeyboardButton("🔌 Перевірити наявність електроенергії")]
        ]

# Обробка команди /start або коли користувач тільки приєднується до бота
async def send_welcome_message(update: Update, context: CallbackContext) -> None:
    """Відправка повідомлення з кнопкою 'Підписатись' або 'Відписатись' при вході в чат"""
    user = update.effective_user
    telegram_id = user.id

    # Отримуємо клавіатуру з кнопками 'Підписатись'/'Відписатись' і 'Перевірити'
    keyboard = get_subscription_keyboard(telegram_id)
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

    welcome_message = (
        f"👋 Вітаю, {user.first_name}!\n\n"
        "Я бот для перевірки наявності електроенергії.\n"
        "Натисніть на потрібну кнопку."
    )

    # Додаємо/оновлюємо користувача в базі (неблокуюче)
    try:
        await asyncio.to_thread(add_user, telegram_id, user.first_name, False)
    except Exception:
        logger.exception("Не вдалося додати або оновити користувача в базі")

    # Відправка привітального повідомлення з кнопками
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)

# Обробка натискання на кнопку "Підписатись" або "Відписатись"
async def subscription_button_callback(update: Update, context: CallbackContext) -> None:
    """Обробник натискання на кнопку 'Підписатись' або 'Відписатись'"""
    query = update.callback_query
    await query.answer()

    telegram_id = update.effective_user.id
    user_subscribed = get_user_subscription(telegram_id)

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
        
    elif query.data == 'subscribe' or query.data == 'unsubscribe':
        if user_subscribed:  # Якщо підписаний, відписуємо
            update_subscription(telegram_id, False)
            await query.edit_message_text("❌ Ви відписалися від отримання оновлень.")
        else:  # Якщо не підписаний, підписуємо
            update_subscription(telegram_id, True)
            await query.edit_message_text("✅ Ви підписалися на отримання оновлень.")

        # Оновлюємо кнопку на протилежну
        await send_welcome_message(update, context)


async def handle_text_message(update: Update, context: CallbackContext) -> None:
    """Обробник текстових повідомлень для ReplyKeyboardMarkup кнопок 'Підписатись'/'Відписатись'"""
    if not update.message or not update.effective_user:
        return

    text = update.message.text.strip()
    telegram_id = update.effective_user.id
    first_name = update.effective_user.first_name or ''

    try:
        if text == 'Підписатись':
            await asyncio.to_thread(add_user, telegram_id, first_name, True)
            await update.message.reply_text('✅ Ви підписалися на отримання оновлень.')
            # Оновлюємо клавіатуру з новою кнопкою
            keyboard = get_subscription_keyboard(telegram_id)
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
            await update.message.reply_text('Натисніть для подальших дій:', reply_markup=reply_markup)
        elif text == 'Відписатись':
            await asyncio.to_thread(update_subscription, telegram_id, False)
            await update.message.reply_text('❌ Ви відписалися від отримання оновлень.')
            # Оновлюємо клавіатуру з новою кнопкою
            keyboard = get_subscription_keyboard(telegram_id)
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
            await update.message.reply_text('Натисніть для подальших дій:', reply_markup=reply_markup)
        elif text == '🔌 Перевірити наявність електроенергії':
            await update.message.reply_text("🔄 Перевіряю наявність електроенергії...")
            try:
                # Виконуємо перевірку
                result = light_checker.check_light_status()
                # Надсилаємо нове повідомлення з результатом
                await update.message.reply_text(f"📊 РЕЗУЛЬТАТ ПЕРЕВІРКИ:\n\n{result}")
            except Exception as e:
                logger.error(f"Помилка при перевірці світла: {e}")
                await update.message.reply_text("❌ Сталася помилка при перевірці. Спробуйте пізніше.")
    except Exception as e:
        logger.exception(f"Помилка при обробці повідомлення: {e}")
        await update.message.reply_text('Сталася помилка. Спробуйте пізніше.')

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
    # Якщо користувач приєднався до чату, відправляємо йому кнопку "Підписатись"
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
    application.add_handler(CallbackQueryHandler(subscription_button_callback))

    # Обробник текстових повідомлень (клавіатура ReplyKeyboardMarkup)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # Запускаємо бота
    print("🤖 Бот запущено...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
