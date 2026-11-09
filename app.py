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
    ROUTER_SILENT,
    LightChecker,
    StateTracker,
    format_status_message,
    probe_router,
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


def chat_commands(subscribed: bool, admin: bool = False) -> list[BotCommand]:
    toggle = (BotCommand("unsubscribe", "Відписатись") if subscribed
              else BotCommand("subscribe", "Підписатись"))
    commands = [toggle, *DEFAULT_COMMANDS]
    if admin:
        commands.append(BotCommand("router", "Перевірити роутер"))
        commands.append(BotCommand("testmode", "Тестовий режим сповіщень"))
    return commands


def is_admin(user_id: int) -> bool:
    return bool(config.ADMIN_CHAT_ID) and user_id == config.ADMIN_CHAT_ID


async def update_chat_commands(bot, chat_id: int, subscribed: bool) -> None:
    """Меню команд для конкретного чату. Помилка тут не критична."""
    try:
        await bot.set_my_commands(chat_commands(subscribed, admin=is_admin(chat_id)),
                                  scope=BotCommandScopeChat(chat_id=chat_id))
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


ROUTER_USAGE = (
    "Як користуватися:\n"
    "/router — перевірити роутер з налаштувань (ROUTER_HOST, ROUTER_PORT)\n"
    "/router 8443 — перевірити інший порт\n"
    "/router 195.38.8.114 8443 — перевірити довільну адресу й порт\n\n"
    "Перевірка з адресою чи портом разова: налаштування бота не змінюються."
)


def parse_router_args(args: list[str], host: str | None, port: int) -> tuple[str | None, int]:
    """Аргументи /router: [] | [порт] | [адреса] | [адреса, порт]. ValueError — якщо неправильні."""
    def parse_port(text: str) -> int:
        value = int(text)
        if not 1 <= value <= 65535:
            raise ValueError(text)
        return value

    if len(args) > 2:
        raise ValueError(args)
    if len(args) == 2:
        return args[0], parse_port(args[1])
    if len(args) == 1:
        return (host, parse_port(args[0])) if args[0].isdigit() else (args[0], port)
    return host, port


def describe_router_result(result: str, target: str) -> str:
    if result == ROUTER_OPEN:
        return f"✅ Роутер {target} відповідає: порт відкритий (з'єднання встановлено)."
    if result == ROUTER_REFUSED:
        return f"✅ Роутер {target} відповідає відмовою (порт закритий)."
    return f"❌ Роутер {target} не відповідає."


TESTMODE_USAGE = (
    "/testmode on — увімкнути: сповіщення про світло отримуєте лише ви\n"
    "/testmode off — вимкнути: сповіщення знову отримують усі підписники"
)


async def testmode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/testmode [on|off] — лише для власника: сповіщення тільки власнику замість підписників."""
    message = update.effective_message
    if message is None:
        return
    args = [a.lower() for a in (context.args or [])]

    if not args:
        enabled = await asyncio.to_thread(broadcaster.is_test_mode)
        state = ("🧪 Тестовий режим увімкнено: сповіщення отримуєте лише ви." if enabled
                 else "Тестовий режим вимкнено: сповіщення отримують усі підписники.")
        await message.reply_text(f"{state}\n\n{TESTMODE_USAGE}")
        return
    if len(args) != 1 or args[0] not in ("on", "off"):
        await message.reply_text("⚠️ Не зрозумів команду.\n\n" + TESTMODE_USAGE)
        return

    enabled = args[0] == "on"
    await asyncio.to_thread(broadcaster.set_test_mode, enabled)
    if enabled:
        await message.reply_text("🧪 Тестовий режим увімкнено. Сповіщення про світло тепер отримуєте "
                                 "лише ви, підписники нічого не отримують.\n"
                                 "Вимкнути: /testmode off")
    else:
        await message.reply_text("✅ Тестовий режим вимкнено. Сповіщення знову отримують усі підписники.")


async def router_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/router — лише для власника: перевірити домашній роутер просто зараз.

    Нічого не розсилає, тож нею можна безпечно перевірити налаштування
    (наприклад, вийнявши кабель інтернету з роутера) або підібрати порт.
    """
    message = update.effective_message
    if message is None:
        return
    checker: LightChecker = context.bot_data["checker"]
    args = context.args or []
    try:
        host, port = parse_router_args(args, checker.router_host, checker.router_port)
    except ValueError:
        await message.reply_text("⚠️ Не вдалося розібрати адресу чи порт.\n\n" + ROUTER_USAGE)
        return
    if not host:
        await message.reply_text("ℹ️ ROUTER_HOST не задано в налаштуваннях Heroku.\n\n" + ROUTER_USAGE)
        return

    await message.reply_text("🔄 Перевіряю роутер...")
    target = f"{host}:{port}"

    if args:
        # Разова перевірка іншої адреси чи порту: стан перевірки бота не змінюється
        result = await asyncio.to_thread(probe_router, host, port)
        text = describe_router_result(result, target)
        if result != ROUTER_SILENT:
            text += (f"\n\nЩоб бот перевіряв роутер саме так, задайте в Heroku "
                     f"ROUTER_HOST={host} і ROUTER_PORT={port}.")
        text += "\n\nЦе разова перевірка: налаштування бота не змінено."
        await message.reply_text(text)
        return

    result, online = await asyncio.to_thread(checker.check_router)
    text = describe_router_result(result, target)
    if result == ROUTER_OPEN:
        text += "\nЦе найнадійніший режим."
    elif result == ROUTER_REFUSED and not online:
        text += ("\nАле раніше цей порт був відкритий, тож бот вважає таку відмову "
                 "відповіддю обладнання провайдера, а не вашого роутера.")

    if online:
        conclusion = "інтернет у будинку є."
    elif checker.router_trusted:
        conclusion = "інтернет у будинку немає."
    else:
        conclusion = ("роутер жодного разу не відповідав від запуску, тож бот не довіряє "
                      "його мовчанню й працює так, ніби ROUTER_HOST не задано.")
    await message.reply_text(f"{text}\n\nВисновок бота: {conclusion}")


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
    internet_watch = broadcaster.InternetWatch(config.ADMIN_CHAT_ID)
    app.bot_data["checker"] = checker
    app.bot_data["tracker"] = tracker
    app.bot_data["monitor_task"] = asyncio.create_task(
        broadcaster.monitor_loop(app.bot, checker, tracker, admin_watch, internet_watch),
        name="light-monitor",
    )

    if not config.ADMIN_CHAT_ID:
        logger.info("ADMIN_CHAT_ID не задано — службові сповіщення власнику вимкнені")
    if await asyncio.to_thread(broadcaster.is_test_mode):
        logger.warning("🧪 Тестовий режим увімкнено: сповіщення про світло отримує лише власник "
                       "(вимкнути: /testmode off)")
    await log_router_check(checker)

    try:
        await app.bot.set_my_commands(DEFAULT_COMMANDS)
    except TelegramError as e:
        logger.warning("Не вдалося встановити меню команд: %s", e)

    if config.ADMIN_CHAT_ID:
        # Меню власника з командою /router — одразу, без очікування /start
        try:
            subscribed = await asyncio.to_thread(db.get_user_subscription, config.ADMIN_CHAT_ID)
            await update_chat_commands(app.bot, config.ADMIN_CHAT_ID, subscribed)
        except Exception:
            logger.exception("Не вдалося встановити меню команд власника")


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
                       "або ввімкніть віддалене керування роутером. Поки роутер жодного разу "
                       "не відповів, бот не довіряє його мовчанню", port)


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
    if config.ADMIN_CHAT_ID:
        # Лише для власника; для інших це невідомі команди (спрацює show_menu)
        only_admin = filters.User(user_id=config.ADMIN_CHAT_ID)
        app.add_handler(CommandHandler("router", router_command, filters=only_admin))
        app.add_handler(CommandHandler("testmode", testmode_command, filters=only_admin))

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
