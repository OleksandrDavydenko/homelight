import logging
import asyncio
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, BotCommand, BotCommandScopeChat
from telegram.ext import Application, CommandHandler, CallbackContext, ChatMemberHandler, MessageHandler, filters
from light_checker import LightChecker
from config import TELEGRAM_TOKEN
from db import get_user_subscription, update_subscription, add_user
import broadcaster

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

light_checker = LightChecker()

# Глобальна змінна для зберігання клавіатури
_user_keyboards = {}

def get_subscription_keyboard(telegram_id):
    """Отримання клавіатури з кнопкою 'Підписатись' або 'Відписатись' залежно від статусу"""
    user_subscribed = get_user_subscription(telegram_id)

    if user_subscribed is True:
        keyboard = [
            [KeyboardButton("🔕 Відписатись")],
            [KeyboardButton("🔌 Перевірити наявність електроенергії")],
            [KeyboardButton("ℹ️ Довідка")]
        ]
    else:
        keyboard = [
            [KeyboardButton("🔔 Підписатись")],
            [KeyboardButton("🔌 Перевірити наявність електроенергії")],
            [KeyboardButton("ℹ️ Довідка")]
        ]
    
    # Зберігаємо клавіатуру у глобальному словнику
    _user_keyboards[telegram_id] = keyboard
    return keyboard

def get_cached_keyboard(telegram_id):
    """Отримати збережену клавіатуру або створити нову"""
    if telegram_id in _user_keyboards:
        return _user_keyboards[telegram_id]
    return get_subscription_keyboard(telegram_id)

async def set_bot_menu(app):
    """Асинхронне додавання команд у меню бота"""
    commands = [
        BotCommand("check", "Перевірити наявність світла"),
        BotCommand("help", "Показати довідку")
    ]
    try:
        await app.bot.set_my_commands(commands)
        logger.info("Bot menu commands set successfully")
        
        # Запускаємо broadcaster як фонове завдання
        try:
            logger.info("Starting broadcaster.monitor_loop as background task")
            # Перевіряємо, чи має broadcaster.monitor_loop приймати параметри
            import inspect
            sig = inspect.signature(broadcaster.monitor_loop)
            if len(sig.parameters) == 0:
                # Якщо monitor_loop не приймає параметрів
                asyncio.create_task(broadcaster.monitor_loop())
            else:
                # Якщо monitor_loop потребує параметр bot
                from telegram import Bot
                bot_instance = Bot(token=TELEGRAM_TOKEN)
                asyncio.create_task(broadcaster.monitor_loop(bot=bot_instance))
        except Exception as e:
            logger.exception(f"Failed to start broadcaster.monitor_loop: {e}")
    except Exception as e:
        logger.exception(f"Failed to set bot menu commands: {e}")

async def send_welcome_message(update: Update, context: CallbackContext) -> None:
    """Відправка повідомлення з кнопкою 'Підписатись' або 'Відписатись'"""
    user = update.effective_user
    telegram_id = user.id
    
    # Спочатку відправляємо повідомлення з клавіатурою
    keyboard = get_subscription_keyboard(telegram_id)
    reply_markup = ReplyKeyboardMarkup(
        keyboard, 
        resize_keyboard=True, 
        one_time_keyboard=False,
        selective=True  # Додаємо selective для кращої підтримки
    )
    
    welcome_message = (
        f"👋 Вітаю, {user.first_name}!\n\n"
        "Я бот для перевірки наявності електроенергії в будинку на Полтавській 64.\n"
        "Натисніть на потрібну кнопку."
    )
    
    # Використовуємо reply_text для відправки з клавіатурою
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)
    
    # Потім оновлюємо базу даних (асинхронно)
    try:
        await asyncio.to_thread(add_user, telegram_id, user.first_name, False)
        logger.info(f"User {telegram_id} added to database")
    except Exception as e:
        logger.exception(f"Failed to add user to database: {e}")
    
    # Оновлюємо меню команд
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

        await context.application.bot.set_my_commands(
            chat_commands, 
            scope=BotCommandScopeChat(chat_id=telegram_id)
        )
        logger.info(f"Set chat-scoped commands for {telegram_id}")
    except Exception as e:
        logger.exception(f"Failed to set chat commands: {e}")

async def handle_text_message(update: Update, context: CallbackContext) -> None:
    """Обробник текстових повідомлень"""
    if not update.message or not update.effective_user:
        return

    text = update.message.text.strip()
    telegram_id = update.effective_user.id
    first_name = update.effective_user.first_name or ''

    try:
        if text == '🔔 Підписатись':
            logger.info(f"Subscribe requested for {telegram_id}")
            await asyncio.to_thread(add_user, telegram_id, first_name, True)
            
            # Відправляємо підтвердження
            await update.message.reply_text('✅ Ви підписалися на отримання оновлень.')
            
            # Оновлюємо клавіатуру
            keyboard = get_subscription_keyboard(telegram_id)
            reply_markup = ReplyKeyboardMarkup(
                keyboard, 
                resize_keyboard=True, 
                one_time_keyboard=False,
                selective=True
            )
            await update.message.reply_text('Натисніть для подальших дій:', reply_markup=reply_markup)
            
            # Оновлюємо меню команд
            try:
                chat_commands = [
                    BotCommand("unsubscribe", "Відписатись"),
                    BotCommand("check", "Перевірити наявність світла"),
                    BotCommand("help", "Показати довідку")
                ]
                await context.application.bot.set_my_commands(
                    chat_commands, 
                    scope=BotCommandScopeChat(chat_id=telegram_id)
                )
            except Exception as e:
                logger.exception(f"Failed to update chat commands: {e}")
                
        elif text == '🔕 Відписатись':
            logger.info(f"Unsubscribe requested for {telegram_id}")
            await asyncio.to_thread(update_subscription, telegram_id, False)
            
            await update.message.reply_text('❌ Ви відписалися від отримання оновлень.')
            
            # Оновлюємо клавіатуру
            keyboard = get_subscription_keyboard(telegram_id)
            reply_markup = ReplyKeyboardMarkup(
                keyboard, 
                resize_keyboard=True, 
                one_time_keyboard=False,
                selective=True
            )
            await update.message.reply_text('Натисніть для подальших дій:', reply_markup=reply_markup)
            
            # Оновлюємо меню команд
            try:
                chat_commands = [
                    BotCommand("subscribe", "Підписатись"),
                    BotCommand("check", "Перевірити наявність світла"),
                    BotCommand("help", "Показати довідку")
                ]
                await context.application.bot.set_my_commands(
                    chat_commands, 
                    scope=BotCommandScopeChat(chat_id=telegram_id)
                )
            except Exception as e:
                logger.exception(f"Failed to update chat commands: {e}")
                
        elif text == '🔌 Перевірити наявність електроенергії':
            await update.message.reply_text("🔄 Перевіряю наявність електроенергії...")
            try:
                result = await asyncio.to_thread(light_checker.check_light_status)
                await update.message.reply_text(result)
            except Exception as e:
                logger.error(f"Error checking light: {e}")
                await update.message.reply_text("❌ Сталася помилка при перевірці. Спробуйте пізніше.")
                
        elif text == 'ℹ️ Довідка':
            info_text = (
                "Я бот для перевірки наявності електроенергії в будинку на Полтавській 64.\n\n"
                "- Можна натиснути '🔌 Перевірити наявність електроенергії' для миттєвої перевірки.\n"
                "- Натисніть '🔔 Підписатись' щоб отримувати повідомлення при зміни статусу, або '🔕 Відписатись' щоб вимкнути сповіщення.\n\n"
                "Якщо потрібна допомога — напишіть /help"
            )
            await update.message.reply_text(info_text)
            
        else:
            # Для будь-якого іншого тексту - показуємо клавіатуру знову
            keyboard = get_cached_keyboard(telegram_id)
            reply_markup = ReplyKeyboardMarkup(
                keyboard, 
                resize_keyboard=True, 
                one_time_keyboard=False,
                selective=True
            )
            await update.message.reply_text(
                "Оберіть дію з клавіатури:",
                reply_markup=reply_markup
            )
            
    except Exception as e:
        logger.exception(f"Error handling message: {e}")
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
        
        # Оновлюємо клавіатуру
        keyboard = get_subscription_keyboard(telegram_id)
        reply_markup = ReplyKeyboardMarkup(
            keyboard, 
            resize_keyboard=True, 
            one_time_keyboard=False,
            selective=True
        )
        await update.message.reply_text('Натисніть для подальших дій:', reply_markup=reply_markup)
        
    except Exception as e:
        logger.exception(f"Error in /subscribe: {e}")
        await update.message.reply_text('Сталася помилка. Спробуйте пізніше.')

async def unsubscribe_command(update: Update, context: CallbackContext) -> None:
    """Обробник команди /unsubscribe"""
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id
    
    try:
        await asyncio.to_thread(update_subscription, telegram_id, False)
        await update.message.reply_text('❌ Ви відписалися від отримання оновлень.')
        
        # Оновлюємо клавіатуру
        keyboard = get_subscription_keyboard(telegram_id)
        reply_markup = ReplyKeyboardMarkup(
            keyboard, 
            resize_keyboard=True, 
            one_time_keyboard=False,
            selective=True
        )
        await update.message.reply_text('Натисніть для подальших дій:', reply_markup=reply_markup)
        
    except Exception as e:
        logger.exception(f"Error in /unsubscribe: {e}")
        await update.message.reply_text('Сталася помилка. Спробуйте пізніше.')

async def check_command(update: Update, context: CallbackContext) -> None:
    """Обробник команди /check"""
    await update.message.reply_text("🔄 Перевіряю наявність електроенергії...")
    try:
        result = await asyncio.to_thread(light_checker.check_light_status)
        await update.message.reply_text(result)
    except Exception as e:
        logger.error(f"Помилка при перевірці світла: {e}")
        await update.message.reply_text("❌ Сталася помилка при перевірці. Спробуйте пізніше.")

async def chat_member_handler(update: Update, context: CallbackContext) -> None:
    """Обробник подій зміни статусу користувача в чаті"""
    if update.chat_member.new_chat_member.status == "member":
        await send_welcome_message(update, context)

def main() -> None:
    """Запуск бота"""
    application = Application.builder().token(TELEGRAM_TOKEN).post_init(set_bot_menu).build()

    # Додаємо обробники
    application.add_handler(CommandHandler("start", send_welcome_message))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CommandHandler("subscribe", subscribe_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    
    # Важливо: ChatMemberHandler має бути доданий першим
    application.add_handler(ChatMemberHandler(chat_member_handler, ChatMemberHandler.MY_CHAT_MEMBER))
    
    # Обробник всіх текстових повідомлень
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    
    # Додатковий обробник для збереження клавіатури
    async def keep_keyboard(update: Update, context: CallbackContext):
        """Зберігає клавіатуру після будь-якого повідомлення"""
        if update.message and update.effective_user:
            telegram_id = update.effective_user.id
            # Якщо це не текст або команда - показуємо клавіатуру
            if not update.message.text or update.message.text.startswith('/'):
                keyboard = get_cached_keyboard(telegram_id)
                reply_markup = ReplyKeyboardMarkup(
                    keyboard, 
                    resize_keyboard=True, 
                    one_time_keyboard=False,
                    selective=True
                )
                await update.message.reply_text(
                    "Оберіть дію з клавіатури:",
                    reply_markup=reply_markup
                )
    
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, keep_keyboard), group=1)

    print("🤖 Бот запущено...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()