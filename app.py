import logging
import asyncio
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, BotCommand, BotCommandScopeChat
from telegram.ext import Application, CommandHandler, CallbackContext, ChatMemberHandler, MessageHandler, filters
from light_checker import LightChecker  # Переконайтесь, що ви імпортуєте цей клас
from config import TELEGRAM_TOKEN
from db import get_user_subscription, update_subscription, add_user  # Потрібні функції для роботи з БД
import broadcaster

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

    # Виконуємо строгу перевірку на True — це захист від рядкових значень ('false', 'True' тощо)
    if user_subscribed is True:  # Якщо користувач підписаний
        return [
            [KeyboardButton("🔕 Відписатись")],
            [KeyboardButton("🔌 Перевірити наявність електроенергії")],
            [KeyboardButton("ℹ️ Довідка")]
        ]
    else:  # Якщо користувач не підписаний
        return [
            [KeyboardButton("🔔 Підписатись")],
            [KeyboardButton("🔌 Перевірити наявність електроенергії")],
            [KeyboardButton("ℹ️ Довідка")]
        ]


async def set_bot_menu(app):
    """Асинхронне додавання команд у меню бота після ініціалізації додатку."""
    # Встановлюємо базові (дефолтні) команди — без `start`, оскільки
    # підписка/відписка налаштовуються індивідуально для кожного чату.
    commands = [
        BotCommand("check", "Перевірити наявність світла"),
        BotCommand("help", "Показати довідку")
    ]
    try:
        await app.bot.set_my_commands(commands)
        logger.info("Bot menu commands set successfully")
        # Запускаємо broadcaster як фонове завдання в тому самому event loop
        try:
            logger.info("Starting broadcaster.monitor_loop as background task")
            # Використовуємо create_task, щоб не чекати завершення монітора
            asyncio.create_task(broadcaster.monitor_loop())
        except Exception:
            logger.exception("Failed to start broadcaster.monitor_loop")
    except Exception as e:
        logger.exception("Failed to set bot menu commands: %s", e)

# Обробка команди /start або коли користувач тільки приєднується до бота
async def send_welcome_message(update: Update, context: CallbackContext) -> None:
    """Відправка повідомлення з кнопкою 'Підписатись' або 'Відписатись' при вході в чат"""
    user = update.effective_user
    telegram_id = user.id

    # Спочатку додаємо/оновлюємо користувача в базі (щоб клавіатура відображала актуальний стан)
    try:
        logger.info("send_welcome_message: upsert user %s (first_name=%s) subscribed=False", telegram_id, user.first_name)
        await asyncio.to_thread(add_user, telegram_id, user.first_name, False)
        logger.info("send_welcome_message: upsert finished for %s", telegram_id)
    except Exception:
        logger.exception("Не вдалося додати або оновити користувача в базі")

    # Оновлюємо меню команд конкретно для цього чату відповідно до статусу підписки
    try:
        user_subscribed = await asyncio.to_thread(get_user_subscription, telegram_id)
        chat_commands = [
            BotCommand("check", "Перевірити наявність світла"),
            BotCommand("help", "Показати довідку")
        ]
        if user_subscribed is True:
            chat_commands.insert(0, BotCommand("unsubscribe", "Відписатись"))
        else:
            chat_commands.insert(0, BotCommand("subscribe", "Підписатись"))

        await context.application.bot.set_my_commands(chat_commands, scope=BotCommandScopeChat(chat_id=telegram_id))
        logger.info("Set chat-scoped commands for %s: %s", telegram_id, [c.command for c in chat_commands])
    except Exception:
        logger.exception("Не вдалося встановити меню команд для чату")

    # Отримуємо клавіатуру з кнопками 'Підписатись'/'Відписатись' і 'Перевірити'
    keyboard = get_subscription_keyboard(telegram_id)
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

    welcome_message = (
        f"👋 Вітаю, {user.first_name}!\n\n"
        "Я бот для перевірки наявності електроенергії в будинку на Полтавській 64.\n"
        "Натисніть на потрібну кнопку."
    )
    # Відправка привітального повідомлення з кнопками
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)




async def handle_text_message(update: Update, context: CallbackContext) -> None:
    """Обробник текстових повідомлень для ReplyKeyboardMarkup кнопок 'Підписатись'/'Відписатись'"""
    if not update.message or not update.effective_user:
        return

    text = update.message.text.strip()
    telegram_id = update.effective_user.id
    first_name = update.effective_user.first_name or ''

    try:
        if text == '🔔 Підписатись':
            logger.info("handle_text_message: subscribe requested for %s", telegram_id)
            await asyncio.to_thread(add_user, telegram_id, first_name, True)
            logger.info("handle_text_message: subscribe finished for %s", telegram_id)
            await update.message.reply_text('✅ Ви підписалися на отримання оновлень.')
            # Оновлюємо клавіатуру з новою кнопкою
            keyboard = get_subscription_keyboard(telegram_id)
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
            await update.message.reply_text('Натисніть для подальших дій:', reply_markup=reply_markup)
            # Оновлюємо chat-scoped меню команд
            try:
                chat_commands = [
                    BotCommand("unsubscribe", "Відписатись"),
                    BotCommand("check", "Перевірити наявність світла"),
                    BotCommand("help", "Показати довідку")
                ]
                await context.application.bot.set_my_commands(chat_commands, scope=BotCommandScopeChat(chat_id=telegram_id))
            except Exception:
                logger.exception("Failed to update chat commands after subscribe")
        elif text == '🔕 Відписатись':
            logger.info("handle_text_message: unsubscribe requested for %s", telegram_id)
            await asyncio.to_thread(update_subscription, telegram_id, False)
            logger.info("handle_text_message: unsubscribe finished for %s", telegram_id)
            await update.message.reply_text('❌ Ви відписалися від отримання оновлень.')
            # Оновлюємо клавіатуру з новою кнопкою
            keyboard = get_subscription_keyboard(telegram_id)
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
            await update.message.reply_text('Натисніть для подальших дій:', reply_markup=reply_markup)
            # Оновлюємо chat-scoped меню команд
            try:
                chat_commands = [
                    BotCommand("subscribe", "Підписатись"),
                    BotCommand("check", "Перевірити наявність світла"),
                    BotCommand("help", "Показати довідку")
                ]
                await context.application.bot.set_my_commands(chat_commands, scope=BotCommandScopeChat(chat_id=telegram_id))
            except Exception:
                logger.exception("Failed to update chat commands after unsubscribe")
        elif text == '🔌 Перевірити наявність електроенергії':
            await update.message.reply_text("🔄 Перевіряю наявність електроенергії...")
            try:
                # Виконуємо перевірку в потоці, щоб не блокувати event loop
                result = await asyncio.to_thread(light_checker.check_light_status)
                # Надсилаємо нове повідомлення з результатом
                await update.message.reply_text(f"📊 РЕЗУЛЬТАТ ПЕРЕВІРКИ:\n\n{result}")
            except Exception as e:
                logger.error(f"Помилка при перевірці світла: {e}")
                await update.message.reply_text("❌ Сталася помилка при перевірці. Спробуйте пізніше.")
        elif text == 'ℹ️ Довідка':
            info_text = (
                "Я бот для перевірки наявності електроенергії в будинку на Полтавській 64.\n\n"
                "- Можна натиснути '🔌 Перевірити наявність електроенергії' для миттєвої перевірки.\n"
                "- Натисніть '🔔 Підписатись' щоб отримувати повідомлення при зміни статусу, або '🔕 Відписатись' щоб вимкнути сповіщення.\n\n"
                "Якщо потрібна допомога — напишіть /help"
            )
            await update.message.reply_text(info_text)
    except Exception as e:
        logger.exception(f"Помилка при обробці повідомлення: {e}")
        await update.message.reply_text('Сталася помилка. Спробуйте пізніше.')

async def help_command(update: Update, context: CallbackContext) -> None:
    """Обробник команди /help"""
    help_text = (
        "Доступні команди:\n"
        "/start - Почати роботу з ботом\n"
        "/check - Перевірити наявність світла\n"
        "/help - Показати це повідомлення\n\n"
        "Ви також можете натиснути кнопку '🔌 Перевірити наявність електроенергії' у клавіатурі."
    )

    await update.message.reply_text(help_text)


async def subscribe_command(update: Update, context: CallbackContext) -> None:
    """Обробник команди /subscribe"""
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id
    first_name = update.effective_user.first_name or ''
    try:
        await asyncio.to_thread(add_user, telegram_id, first_name, True)
        await update.message.reply_text('✅ Ви підписалися на отримання оновлень.')
        # Оновлюємо chat-scoped меню команд
        try:
            chat_commands = [
                BotCommand("unsubscribe", "Відписатись"),
                BotCommand("check", "Перевірити наявність світла"),
                BotCommand("help", "Показати довідку")
            ]
            await context.application.bot.set_my_commands(chat_commands, scope=BotCommandScopeChat(chat_id=telegram_id))
        except Exception:
            logger.exception("Failed to update chat commands after /subscribe")
        # Оновлюємо клавіатуру користувача
        try:
            keyboard = get_subscription_keyboard(telegram_id)
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
            await update.message.reply_text('Натисніть для подальших дій:', reply_markup=reply_markup)
        except Exception:
            logger.exception("Failed to update reply keyboard after /subscribe")
    except Exception:
        logger.exception("Error handling /subscribe")
        await update.message.reply_text('Сталася помилка. Спробуйте пізніше.')


async def unsubscribe_command(update: Update, context: CallbackContext) -> None:
    """Обробник команди /unsubscribe"""
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id
    try:
        await asyncio.to_thread(update_subscription, telegram_id, False)
        await update.message.reply_text('❌ Ви відписалися від отримання оновлень.')
        # Оновлюємо chat-scoped меню команд
        try:
            chat_commands = [
                BotCommand("subscribe", "Підписатись"),
                BotCommand("check", "Перевірити наявність світла"),
                BotCommand("help", "Показати довідку")
            ]
            await context.application.bot.set_my_commands(chat_commands, scope=BotCommandScopeChat(chat_id=telegram_id))
        except Exception:
            logger.exception("Failed to update chat commands after /unsubscribe")
        # Оновлюємо клавіатуру користувача
        try:
            keyboard = get_subscription_keyboard(telegram_id)
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
            await update.message.reply_text('Натисніть для подальших дій:', reply_markup=reply_markup)
        except Exception:
            logger.exception("Failed to update reply keyboard after /unsubscribe")
    except Exception:
        logger.exception("Error handling /unsubscribe")
        await update.message.reply_text('Сталася помилка. Спробуйте пізніше.')

async def check_command(update: Update, context: CallbackContext) -> None:
    """Обробник команди /check"""
    # Поводимося так само, як при натисканні кнопки клавіатури
    await update.message.reply_text("🔄 Перевіряю наявність електроенергії...")
    try:
        result = await asyncio.to_thread(light_checker.check_light_status)
        await update.message.reply_text(f"📊 РЕЗУЛЬТАТ ПЕРЕВІРКИ:\n\n{result}")
    except Exception as e:
        logger.error(f"Помилка при перевірці світла: {e}")
        await update.message.reply_text("❌ Сталася помилка при перевірці. Спробуйте пізніше.")

async def chat_member_handler(update: Update, context: CallbackContext) -> None:
    """Обробник подій зміни статусу користувача в чаті"""
    # Якщо користувач приєднався до чату, відправляємо йому кнопку "Підписатись"
    if update.chat_member.new_chat_member.status == "member":
        await send_welcome_message(update, context)

def main() -> None:
    """Запуск бота"""
    # Створюємо додаток
    application = Application.builder().token(TELEGRAM_TOKEN).post_init(set_bot_menu).build()

    # Додаємо обробники команд
    application.add_handler(CommandHandler("start", send_welcome_message))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CommandHandler("subscribe", subscribe_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))

    # Додаємо обробник зміни статусу члена чату (коли користувач приєднується)
    application.add_handler(ChatMemberHandler(chat_member_handler))  # Правильний спосіб

    # Обробник текстових повідомлень (клавіатура ReplyKeyboardMarkup)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # Запускаємо бота
    print("🤖 Бот запущено...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
