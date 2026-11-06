import asyncio
import contextlib
import logging
import time

from telegram import BotCommand, BotCommandScopeChat, ReplyKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import broadcaster
import config
import db
from keyboard import BTN_CHECK, BTN_INFO, BTN_SUBSCRIBE, BTN_UNSUBSCRIBE, build_keyboard
from light_checker import (
    ROUTER_OPEN,
    ROUTER_REFUSED,
    LightChecker,
    StateTracker,
    format_status_message,
)

logger = logging.getLogger(__name__)

# Кнопка «Перевірити»: швидше за фонову перевірку, щоб користувач не чекав довго
CHECK_CACHE_SECONDS = 30
CHECK_ATTEMPTS = 2
CHECK_TIMEOUT = 8

ABOUT = "Я бот для перевірки наявності електроенергії в будинку на Полтавській 64."

DEFAULT_COMMANDS = [
    BotCommand("check", "Перевірити наявність світла"),
    BotCommand("help", "Показати довідку"),
]


async def keyboard_for(user_id: int) -> ReplyKeyboardMarkup:
    """Клавіатура з урахуванням того, чи підписаний користувач."""
    return build_keyboard(await asyncio.to_thread(db.get_user_subscription, user_id))


def chat_commands(subscribed: bool) -> list[BotCommand]:
    toggle = (BotCommand("unsubscribe", "Відписатись") if subscribed
              else BotCommand("subscribe", "Підписатись"))
    return [toggle, *DEFAULT_COMMANDS]


async def update_chat_commands(bot, chat_id: int, subscribed: bool) -> None:
    """Меню команд для конкретного чату. Помилка тут не критична."""
    try:
        await bot.set_my_commands(chat_commands(subscribed), scope=BotCommandScopeChat(chat_id=chat_id))
    except TelegramError as e:
        logger.warning("Не вдалося оновити меню команд для %s: %s", chat_id, e)


# ---------- Обробники ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, message = update.effective_user, update.effective_message
    if user is None or message is None:
        return
    await asyncio.to_thread(db.ensure_user, user.id, user.first_name or "")
    subscribed = await asyncio.to_thread(db.get_user_subscription, user.id)
    await message.reply_text(
        f"👋 Вітаю, {user.first_name}!\n\n{ABOUT}\nНатисніть на потрібну кнопку.",
        reply_markup=build_keyboard(subscribed),
    )
    await update_chat_commands(context.bot, user.id, subscribed)


async def _change_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE,
                               subscribe: bool) -> None:
    user, message = update.effective_user, update.effective_message
    if user is None or message is None:
        return
    await asyncio.to_thread(db.set_subscription, user.id, user.first_name or "", subscribe)
    text = ("✅ Ви підписалися на отримання оновлень." if subscribe
            else "❌ Ви відписалися від отримання оновлень.")
    await message.reply_text(text, reply_markup=build_keyboard(subscribe))
    await update_chat_commands(context.bot, user.id, subscribe)


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _change_subscription(update, context, True)


async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _change_subscription(update, context, False)


async def check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, message = update.effective_user, update.effective_message
    if user is None or message is None:
        return
    await message.reply_text("🔄 Перевіряю наявність електроенергії...")
    checker: LightChecker = context.bot_data["checker"]
    tracker: StateTracker = context.bot_data["tracker"]
    reading = await asyncio.to_thread(
        checker.get_reading,
        max_age=CHECK_CACHE_SECONDS, attempts=CHECK_ATTEMPTS, timeout=CHECK_TIMEOUT,
    )
    await message.reply_text(format_status_message(reading, tracker, int(time.time())),
                             reply_markup=await keyboard_for(user.id))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, message = update.effective_user, update.effective_message
    if user is None or message is None:
        return
    await message.reply_text(
        "Доступні команди:\n"
        "/start - Почати роботу з ботом\n"
        "/check - Перевірити наявність світла\n"
        "/subscribe - Підписатись на сповіщення\n"
        "/unsubscribe - Відписатись від сповіщень\n"
        "/help - Показати це повідомлення\n\n"
        f"Ви також можете натиснути кнопку '{BTN_CHECK}' у клавіатурі.",
        reply_markup=await keyboard_for(user.id),
    )


async def info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, message = update.effective_user, update.effective_message
    if user is None or message is None:
        return
    await message.reply_text(
        f"{ABOUT}\n\n"
        f"- Натисніть '{BTN_CHECK}' для миттєвої перевірки.\n"
        f"- Натисніть '{BTN_SUBSCRIBE}', щоб отримувати повідомлення, коли світло зникає "
        f"або з'являється, або '{BTN_UNSUBSCRIBE}', щоб вимкнути сповіщення.\n\n"
        "Якщо потрібна допомога — напишіть /help",
        reply_markup=await keyboard_for(user.id),
    )


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Будь-яке інше повідомлення в приватному чаті — показати клавіатуру."""
    user, message = update.effective_user, update.effective_message
    if user is None or message is None:
        return
    await message.reply_text("Оберіть дію з клавіатури:", reply_markup=await keyboard_for(user.id))


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Користувач заблокував бота — відписуємо, щоб не слати йому сповіщення."""
    change = update.my_chat_member
    if change is None or change.chat.type != ChatType.PRIVATE:
        return
    if change.new_chat_member.status in (ChatMemberStatus.BANNED, ChatMemberStatus.LEFT):
        logger.info("Користувач %s заблокував бота — відписую", change.chat.id)
        await asyncio.to_thread(db.update_subscription, change.chat.id, False)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Помилка під час обробки оновлення", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Сталася помилка. Спробуйте пізніше.")
        except TelegramError as e:
            logger.warning("Не вдалося повідомити користувача про помилку: %s", e)


# ---------- Запуск ----------

async def post_init(app: Application) -> None:
    # Без БД бот працювати не може: якщо тут впаде, процес завершиться,
    # і хостинг його перезапустить
    await asyncio.to_thread(db.init_db)
    state = await asyncio.to_thread(db.get_light_state)

    checker = LightChecker()
    tracker = StateTracker.from_dict(state)
    admin_watch = broadcaster.AdminWatch(config.ADMIN_CHAT_ID)
    app.bot_data["checker"] = checker
    app.bot_data["tracker"] = tracker
    app.bot_data["monitor_task"] = asyncio.create_task(
        broadcaster.monitor_loop(app.bot, checker, tracker, admin_watch), name="light-monitor"
    )

    if not config.ADMIN_CHAT_ID:
        logger.info("ADMIN_CHAT_ID не задано — службові сповіщення власнику вимкнені")
    await log_router_check(checker)

    try:
        await app.bot.set_my_commands(DEFAULT_COMMANDS)
    except TelegramError as e:
        logger.warning("Не вдалося встановити меню команд: %s", e)


async def log_router_check(checker: LightChecker) -> None:
    """Під час старту перевіряє, чи відповідає домашній роутер, і пише результат у лог.

    IP-адресу в лог не пишемо — лише порт.
    """
    if not checker.router_host:
        logger.info("ROUTER_HOST не задано — перевірка інтернету в будинку вимкнена")
        return
    result, _ = await asyncio.to_thread(checker.check_router)
    port = checker.router_port
    if result == ROUTER_OPEN:
        logger.info("Роутер (порт %s) відкритий — перевірка інтернету в будинку працює "
                    "в найнадійнішому режимі", port)
    elif result == ROUTER_REFUSED:
        logger.info("Роутер (порт %s) відповідає відмовою — перевірка працює; надійніше "
                    "відкрити порт (віддалене керування роутером)", port)
    else:
        logger.warning("Роутер (порт %s) не відповідає: перевірте ROUTER_HOST/ROUTER_PORT "
                       "або ввімкніть віддалене керування роутером", port)


async def post_stop(app: Application) -> None:
    task = app.bot_data.pop("monitor_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("check", check))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))

    app.add_handler(MessageHandler(filters.Text([BTN_SUBSCRIBE]), subscribe))
    app.add_handler(MessageHandler(filters.Text([BTN_UNSUBSCRIBE]), unsubscribe))
    app.add_handler(MessageHandler(filters.Text([BTN_CHECK]), check))
    app.add_handler(MessageHandler(filters.Text([BTN_INFO]), info))

    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

    # Останнім: усе інше в приватному чаті (текст, стікери, невідомі команди)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE, show_menu))

    app.add_error_handler(error_handler)


def build_application() -> Application:
    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_TOKEN)
        .concurrent_updates(True)
        .post_init(post_init)
        .post_stop(post_stop)
        .build()
    )
    register_handlers(app)
    return app


def main() -> None:
    config.validate()
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    # httpx на рівні INFO пише кожен URL запиту, а в ньому — токен бота
    logging.getLogger("httpx").setLevel(logging.WARNING)

    app = build_application()
    logger.info("🤖 Бот запущено")
    app.run_polling(allowed_updates=[Update.MESSAGE, Update.MY_CHAT_MEMBER])


if __name__ == "__main__":
    main()
