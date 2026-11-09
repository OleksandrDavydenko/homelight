import asyncio
import contextlib
import logging
import time

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

import admins
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
    measure_router_latency,
    probe_router,
)

logger = logging.getLogger(__name__)

# Кнопка «Перевірити»: швидше за фонову перевірку, щоб користувач не чекав довго
CHECK_CACHE_SECONDS = 30
CHECK_ATTEMPTS = 2
CHECK_TIMEOUT = 8

ABOUT = "Я бот для перевірки наявності електроенергії в будинку на Полтавській 64."

# Які оновлення отримувати від Telegram (CALLBACK_QUERY — натискання кнопок під /admins)
ALLOWED_UPDATES = [Update.MESSAGE, Update.MY_CHAT_MEMBER, Update.CALLBACK_QUERY]

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
        commands.append(BotCommand("admins", "Адміни для повідомлень про інтернет"))
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
    "/router адреса порт — перевірити довільну адресу й порт\n\n"
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


def describe_latency(latencies: list[float], lost: int) -> str:
    """Рядок про якість зв'язку для /router (без оцінок «добре/погано» в мс)."""
    total = len(latencies) + lost
    if not latencies:
        return f"🟠 Зв'язок нестабільний: усі {total} спроб втрачено."
    avg, low, high = (round(v) for v in (sum(latencies) / len(latencies),
                                          min(latencies), max(latencies)))
    if lost == 0:
        return f"📶 Зв'язок стабільний: затримка {avg} мс (від {low} до {high}), втрат немає."
    if lost == 1:
        return f"📶 Зв'язок майже стабільний: затримка {avg} мс, втрачено 1 з {total}."
    return f"🟠 Зв'язок нестабільний: затримка {avg} мс, втрачено {lost} з {total}."


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
        await message.reply_text("ℹ️ Перевірку роутера не налаштовано: задайте ROUTER_HOST у Heroku."
                                 "\n\n" + ROUTER_USAGE)
        return

    await message.reply_text("🔄 Перевіряю зв'язок з роутером...")

    if args:
        # Разова перевірка іншої адреси чи порту: стан перевірки бота не змінюється
        result = await asyncio.to_thread(probe_router, host, port)
        if result == ROUTER_SILENT:
            text = f"🔴 {host}:{port} не відповідає."
        else:
            latency = await asyncio.to_thread(measure_router_latency, host, port)
            text = (f"🟢 {host}:{port} відповідає.\n{describe_latency(*latency)}\n\n"
                    f"Щоб бот перевіряв саме так, задайте в Heroku "
                    f"ROUTER_HOST={host} і ROUTER_PORT={port}.")
        await message.reply_text(text + "\n\nℹ️ Разова перевірка: налаштування бота не змінено.")
        return

    where = f"{host}, порт {port}"
    result, online = await asyncio.to_thread(checker.check_router)
    if online:
        latency = await asyncio.to_thread(measure_router_latency, host, port)
        text = f"🟢 Інтернет удома є\nРоутер на зв'язку ({where}).\n{describe_latency(*latency)}"
    elif result == ROUTER_REFUSED:
        # Суворий режим: раніше порт був відкритий, тож ця відповідь — не від роутера
        text = ("🔴 Інтернету вдома, ймовірно, немає\n"
                "Відповів не ваш роутер, а, схоже, обладнання провайдера.")
    elif checker.router_trusted:
        text = (f"🔴 Інтернету вдома немає\nРоутер не відповідає ({where}).\n"
                "Поки так, бот не бачить світла й нічого не надсилає підписникам.")
    else:
        text = (f"⚪️ Роутер не відповідає ({where})\n"
                "Він жодного разу не відповідав від запуску бота: схоже, неправильна адреса "
                "чи порт або провайдер не пропускає вхідні з'єднання. Поки так, бот визначає "
                "світло лише за датчиком.")
    await message.reply_text(text)


# ---------- /admins: хто отримує повідомлення про інтернет ----------

ADD_PAGE_SIZE = 8  # скільки користувачів показувати на одній сторінці «➕ Додати»
WELCOME_ADMIN = ("👋 Вас додано: ви отримуватимете повідомлення, коли вдома зникає "
                 "й повертається інтернет.")
WELCOME_ADMIN_SUBSCRIBE = ("\n\nВони надходять лише тим, хто підписаний на сповіщення про світло, — "
                           "натисніть «🔔 Підписатись».")
NOT_SUBSCRIBED_MARK = "🔕"


def user_label(user_id: int, name: str | None, username: str | None = None) -> str:
    """Ім'я, @username (якщо відомий) і повний Telegram ID, щоб точно розрізнити людей."""
    label = (name or "").strip() or "Без імені"
    if username:
        label += f" @{username}"
    return f"{label} · {user_id}"


async def remember_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запам'ятовує ім'я та @username кожного, хто взаємодіє з ботом (для /admins).

    Працює перед усіма іншими обробниками. У БД пише лише тоді, коли щось
    змінилося з минулого разу (або вперше після запуску бота).
    """
    user = update.effective_user
    if user is None or user.is_bot:
        return
    info = (user.first_name or "", user.username or None)
    known = context.bot_data.setdefault("known_users", {})
    if known.get(user.id) == info:
        return
    try:
        await asyncio.to_thread(db.remember_user, user.id, *info)
        known[user.id] = info
    except Exception:
        logger.exception("Не вдалося запам'ятати користувача %s", user.id)


async def admins_view() -> tuple[str, InlineKeyboardMarkup]:
    """Список отримувачів із кнопками «❌» для адмінів з БД і «➕ Додати»."""
    db_ids = await asyncio.to_thread(admins.load_ids)
    heroku_ids = [i for i in config.INTERNET_ALERT_CHAT_IDS if i not in db_ids]
    others = [i for i in dict.fromkeys(db_ids + heroku_ids) if i != config.ADMIN_CHAT_ID]
    users = await asyncio.to_thread(db.get_users, others) if others else {}
    subscribed = await asyncio.to_thread(db.filter_subscribed, [config.ADMIN_CHAT_ID, *others])

    def mark(user_id: int) -> str:
        return "" if user_id in subscribed else f" {NOT_SUBSCRIBED_MARK}"

    lines = ["👥 Хто отримує повідомлення про інтернет:",
             f"• Ви (власник){mark(config.ADMIN_CHAT_ID)}"]
    buttons = []
    for user_id in others:
        label = user_label(user_id, *users.get(user_id, ("", None)))
        if user_id in db_ids:
            lines.append(f"• {label}{mark(user_id)}")
            buttons.append([InlineKeyboardButton(f"❌ {label}", callback_data=f"adm:del:{user_id}")])
        else:
            lines.append(f"• {label}{mark(user_id)} (задано в Heroku)")
    if any(mark(i) for i in [config.ADMIN_CHAT_ID, *others]):
        lines.append(f"\n{NOT_SUBSCRIBED_MARK} — не підписані на сповіщення про світло, тому "
                     "повідомлень про інтернет не отримують.")
    if not others:
        lines.append("\nЩоб додати людину, попросіть її натиснути /start у боті, "
                     "а потім натисніть «➕ Додати».")
    buttons.append([InlineKeyboardButton("➕ Додати", callback_data="adm:add")])
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def add_admin_view(page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """Користувачі, які ще не отримують повідомлень про інтернет, сторінками (новіші першими)."""
    current = admins.recipients(await asyncio.to_thread(admins.load_ids))
    total = await asyncio.to_thread(db.count_users, current)
    pages = max(1, -(-total // ADD_PAGE_SIZE))  # округлення вгору
    page = min(max(page, 0), pages - 1)
    users = await asyncio.to_thread(db.get_recent_users, ADD_PAGE_SIZE, current,
                                    page * ADD_PAGE_SIZE)

    subscribed = (await asyncio.to_thread(db.filter_subscribed, [u[0] for u in users])
                  if users else set())

    if users:
        text = "Кого додати? Людина має спершу натиснути /start у боті. Нові користувачі — вгорі."
        if len(subscribed) < len(users):
            text += (f"\n{NOT_SUBSCRIBED_MARK} — ще не підписані на сповіщення про світло: "
                     "повідомлення про інтернет отримуватимуть, коли підпишуться.")
        if pages > 1:
            text += f"\n\nСторінка {page + 1} з {pages} (усього {total})."
    else:
        text = "Поки немає кого додати: попросіть людину натиснути /start у боті."

    buttons = [[InlineKeyboardButton(
                    ("" if uid in subscribed else f"{NOT_SUBSCRIBED_MARK} ") + user_label(uid, name, username),
                    callback_data=f"adm:pick:{uid}")]
               for uid, name, username in users]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Попередні", callback_data=f"adm:add:{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("Далі ➡️", callback_data=f"adm:add:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("↩️ До списку адмінів", callback_data="adm:list")])
    return text, InlineKeyboardMarkup(buttons)


async def admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/admins — лише для власника: хто отримує повідомлення про інтернет."""
    message = update.effective_message
    if message is None:
        return
    text, markup = await admins_view()
    await message.reply_text(text, reply_markup=markup)


async def admins_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопки під /admins: adm:list | adm:add[:<сторінка>] | adm:pick:<id> | adm:del:<id>."""
    query = update.callback_query
    if query is None:
        return
    if not is_admin(query.from_user.id):
        await query.answer("Недоступно")
        return

    parts = (query.data or "").split(":")
    action = parts[1] if len(parts) > 1 else "list"
    popup = None

    if action in ("pick", "del"):
        try:
            user_id = int(parts[2])
        except (IndexError, ValueError):
            await query.answer()
            return
        ids = await asyncio.to_thread(admins.load_ids)
        users = await asyncio.to_thread(db.get_users, [user_id])
        label = user_label(user_id, *users.get(user_id, ("", None)))
        if action == "pick":
            is_subscribed = bool(await asyncio.to_thread(db.filter_subscribed, [user_id]))
            if user_id not in ids and user_id != config.ADMIN_CHAT_ID:
                ids.append(user_id)
                welcome = WELCOME_ADMIN + ("" if is_subscribed else WELCOME_ADMIN_SUBSCRIBE)
                try:
                    await context.bot.send_message(chat_id=user_id, text=welcome)
                except TelegramError as e:
                    logger.warning("Не вдалося привітати нового адміна %s: %s", user_id, e)
            popup = (f"✅ {label} тепер отримує повідомлення про інтернет" if is_subscribed
                     else f"✅ {label} додано. Повідомлення отримуватиме, коли підпишеться")
        else:
            ids = [i for i in ids if i != user_id]
            popup = f"Видалено: {label}"
        await asyncio.to_thread(admins.save_ids, ids)
        watch = context.bot_data.get("internet_watch")
        if watch is not None:
            watch.chat_ids = admins.recipients(ids)
        text, markup = await admins_view()
    elif action == "add":
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        text, markup = await add_admin_view(page)
    else:
        text, markup = await admins_view()

    await query.answer(popup)
    try:
        await query.edit_message_text(text, reply_markup=markup)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


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
    db_admin_ids = await asyncio.to_thread(admins.load_ids)
    internet_watch = broadcaster.InternetWatch(admins.recipients(db_admin_ids))
    app.bot_data["checker"] = checker
    app.bot_data["tracker"] = tracker
    # /admins змінює список отримувачів на льоту, без перезапуску
    app.bot_data["internet_watch"] = internet_watch
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
    # Група -1 виконується перед усіма: запам'ятати ім'я та @username для /admins
    app.add_handler(TypeHandler(Update, remember_user), group=-1)

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
        app.add_handler(CommandHandler("admins", admins_command, filters=only_admin))
        # Кнопки під /admins; хто натиснув, перевіряється в самому обробнику
        app.add_handler(CallbackQueryHandler(admins_callback, pattern=r"^adm:"))

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
    app.run_polling(allowed_updates=ALLOWED_UPDATES)


if __name__ == "__main__":
    main()
