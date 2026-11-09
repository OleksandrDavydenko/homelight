import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from telegram import Chat, Message, MessageEntity, Update, User
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import NetworkError
from telegram.ext import CallbackQueryHandler, CommandHandler, TypeHandler

import app
import light_checker
from light_checker import Reading, StateTracker


def make_update(user_id=7, first_name="Олена", text=None):
    message = SimpleNamespace(text=text, reply_text=mock.AsyncMock())
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, first_name=first_name),
        effective_chat=SimpleNamespace(id=user_id),
        effective_message=message,
    )


def make_context(args=None, **bot_data):
    return SimpleNamespace(bot=SimpleNamespace(set_my_commands=mock.AsyncMock()),
                           bot_data=bot_data, args=args)


def first_button(reply_text_mock):
    markup = reply_text_mock.call_args.kwargs["reply_markup"]
    return markup.keyboard[0][0].text


class HandlersTest(unittest.IsolatedAsyncioTestCase):
    async def test_start_keeps_existing_subscription(self):
        update, context = make_update(), make_context()
        with mock.patch.object(app.db, "ensure_user") as ensure, \
                mock.patch.object(app.db, "set_subscription") as set_sub, \
                mock.patch.object(app.db, "get_user_subscription", return_value=True):
            await app.start(update, context)
        ensure.assert_called_once_with(7, "Олена")
        set_sub.assert_not_called()
        self.assertEqual(first_button(update.effective_message.reply_text), app.BTN_UNSUBSCRIBE)
        commands = context.bot.set_my_commands.call_args.args[0]
        self.assertEqual(commands[0].command, "unsubscribe")

    async def test_subscribe_button(self):
        update, context = make_update(text=app.BTN_SUBSCRIBE), make_context()
        with mock.patch.object(app.db, "set_subscription") as set_sub:
            await app.subscribe(update, context)
        set_sub.assert_called_once_with(7, "Олена", True)
        self.assertEqual(first_button(update.effective_message.reply_text), app.BTN_UNSUBSCRIBE)
        self.assertEqual(context.bot.set_my_commands.call_args.args[0][0].command, "unsubscribe")

    async def test_unsubscribe_survives_command_menu_failure(self):
        update, context = make_update(), make_context()
        context.bot.set_my_commands.side_effect = NetworkError("timeout")
        with mock.patch.object(app.db, "set_subscription") as set_sub:
            await app.unsubscribe(update, context)
        set_sub.assert_called_once_with(7, "Олена", False)
        self.assertEqual(first_button(update.effective_message.reply_text), app.BTN_SUBSCRIBE)

    async def test_check_uses_cache_and_does_not_touch_state(self):
        checker = mock.Mock()
        checker.get_reading.return_value = Reading(has_light=True, fetched_at=0, voltage=230)
        tracker = StateTracker(False, 0)
        update, context = make_update(), make_context(checker=checker, tracker=tracker)
        with mock.patch.object(app.db, "get_user_subscription", return_value=False):
            await app.check(update, context)
        self.assertEqual(first_button(update.effective_message.reply_text), app.BTN_SUBSCRIBE)
        checker.get_reading.assert_called_once_with(
            max_age=app.CHECK_CACHE_SECONDS, attempts=app.CHECK_ATTEMPTS, timeout=app.CHECK_TIMEOUT)
        self.assertIn("СВІТЛО Є", update.effective_message.reply_text.call_args.args[0])
        self.assertEqual((tracker.has_light, tracker.since), (False, 0))

    async def test_help_and_info_restore_keyboard(self):
        for handler in (app.help_command, app.info):
            update = make_update()
            with mock.patch.object(app.db, "get_user_subscription", return_value=True):
                await handler(update, make_context())
            self.assertEqual(first_button(update.effective_message.reply_text), app.BTN_UNSUBSCRIBE)

    def test_keyboard_is_visible_in_private_chats(self):
        markup = app.build_keyboard(False)
        self.assertFalse(markup.selective)  # з selective у приватному чаті клавіатуру не видно
        self.assertTrue(markup.is_persistent)
        self.assertTrue(markup.resize_keyboard)

    async def test_blocked_bot_unsubscribes(self):
        update = SimpleNamespace(my_chat_member=SimpleNamespace(
            chat=SimpleNamespace(id=7, type=ChatType.PRIVATE),
            new_chat_member=SimpleNamespace(status=ChatMemberStatus.BANNED),
        ))
        with mock.patch.object(app.db, "update_subscription") as update_sub:
            await app.on_my_chat_member(update, make_context())
        update_sub.assert_called_once_with(7, False)

    async def test_unblocked_bot_does_nothing(self):
        update = SimpleNamespace(my_chat_member=SimpleNamespace(
            chat=SimpleNamespace(id=7, type=ChatType.PRIVATE),
            new_chat_member=SimpleNamespace(status=ChatMemberStatus.MEMBER),
        ))
        with mock.patch.object(app.db, "update_subscription") as update_sub:
            await app.on_my_chat_member(update, make_context())
        update_sub.assert_not_called()


def router_checker(result=None, online=True, host="195.0.2.10", trusted=True):
    checker = mock.Mock(router_host=host, router_port=80, router_trusted=trusted)
    checker.check_router.return_value = (result, online)
    return checker


def reply_texts(update):
    return [c.args[0] for c in update.effective_message.reply_text.call_args_list]


GOOD_LATENCY = ([30.0, 28.4, 41.2, 31.0, 29.9], 0)


@mock.patch.object(app, "measure_router_latency", mock.Mock(return_value=GOOD_LATENCY))
class RouterCommandTest(unittest.IsolatedAsyncioTestCase):
    async def run_router(self, checker, args=None):
        update = make_update()
        await app.router_command(update, make_context(args=args, checker=checker))
        texts = reply_texts(update)
        for text in texts:  # жодних технічних слів і назв пристроїв
            for word in ("відмов", "порт закритий", "порт відкритий", "Shelly"):
                self.assertNotIn(word, text)
        return texts

    async def test_not_configured(self):
        checker = router_checker(host=None)
        texts = await self.run_router(checker)
        self.assertEqual(len(texts), 1)
        self.assertIn("Перевірку роутера не налаштовано", texts[0])
        checker.check_router.assert_not_called()

    async def test_router_online(self):
        for result in (light_checker.ROUTER_OPEN, light_checker.ROUTER_REFUSED):
            texts = await self.run_router(router_checker(result))
            self.assertEqual(texts[0], "🔄 Перевіряю зв'язок з роутером...")
            self.assertEqual(texts[1], "🟢 Інтернет удома є\n"
                                       "Роутер на зв'язку (195.0.2.10, порт 80).\n"
                                       "📶 Зв'язок стабільний: затримка 32 мс (від 28 до 41), втрат немає.")

    async def test_refused_in_strict_mode(self):
        texts = await self.run_router(router_checker(light_checker.ROUTER_REFUSED, online=False))
        self.assertTrue(texts[1].startswith("🔴 Інтернету вдома, ймовірно, немає"))
        self.assertIn("обладнання провайдера", texts[1])

    async def test_silent(self):
        texts = await self.run_router(router_checker(light_checker.ROUTER_SILENT, online=False))
        self.assertEqual(texts[1], "🔴 Інтернету вдома немає\n"
                                   "Роутер не відповідає (195.0.2.10, порт 80).\n"
                                   "Поки так, бот не бачить світла й нічого не надсилає підписникам.")

    async def test_silent_router_that_never_answered(self):
        checker = router_checker(light_checker.ROUTER_SILENT, online=False, trusted=False)
        texts = await self.run_router(checker)
        self.assertTrue(texts[1].startswith("⚪️ Роутер не відповідає (195.0.2.10, порт 80)"))
        self.assertIn("жодного разу не відповідав", texts[1])
        self.assertIn("лише за датчиком", texts[1])

    async def test_latency_not_measured_when_router_silent(self):
        with mock.patch.object(app, "measure_router_latency") as measure:
            await self.run_router(router_checker(light_checker.ROUTER_SILENT, online=False))
        measure.assert_not_called()

    async def test_one_off_port(self):
        checker = router_checker()
        with mock.patch.object(app, "probe_router", return_value=light_checker.ROUTER_REFUSED) as probe:
            texts = await self.run_router(checker, args=["8443"])
        probe.assert_called_once_with("195.0.2.10", 8443)
        checker.check_router.assert_not_called()  # стан перевірки бота не чіпаємо
        self.assertTrue(texts[1].startswith("🟢 195.0.2.10:8443 відповідає.\n📶 Зв'язок стабільний"))
        self.assertIn("ROUTER_HOST=195.0.2.10 і ROUTER_PORT=8443", texts[1])
        self.assertTrue(texts[1].endswith("ℹ️ Разова перевірка: налаштування бота не змінено."))

    async def test_one_off_host_and_port_without_router_host(self):
        with mock.patch.object(app, "probe_router", return_value=light_checker.ROUTER_SILENT) as probe:
            texts = await self.run_router(router_checker(host=None), args=["198.51.100.7", "22"])
        probe.assert_called_once_with("198.51.100.7", 22)
        self.assertTrue(texts[1].startswith("🔴 198.51.100.7:22 не відповідає."))
        self.assertNotIn("ROUTER_PORT=", texts[1])

    async def test_bad_arguments(self):
        for args in (["99999"], ["host", "port"], ["a", "1", "b"]):
            with mock.patch.object(app, "probe_router") as probe:
                texts = await self.run_router(router_checker(), args=args)
            probe.assert_not_called()
            self.assertIn("Не вдалося розібрати", texts[0])

    def test_parse_router_args(self):
        parse = app.parse_router_args
        self.assertEqual(parse([], "h", 80), ("h", 80))
        self.assertEqual(parse(["8443"], "h", 80), ("h", 8443))
        self.assertEqual(parse(["other"], "h", 80), ("other", 80))
        self.assertEqual(parse(["other", "22"], None, 80), ("other", 22))

    def test_describe_latency(self):
        self.assertEqual(app.describe_latency([20.0, 30.0], 0),
                         "📶 Зв'язок стабільний: затримка 25 мс (від 20 до 30), втрат немає.")
        self.assertEqual(app.describe_latency([20.0, 30.0, 40.0, 50.0], 1),
                         "📶 Зв'язок майже стабільний: затримка 35 мс, втрачено 1 з 5.")
        self.assertEqual(app.describe_latency([20.0, 40.0, 60.0], 2),
                         "🟠 Зв'язок нестабільний: затримка 40 мс, втрачено 2 з 5.")
        self.assertEqual(app.describe_latency([], 5), "🟠 Зв'язок нестабільний: усі 5 спроб втрачено.")


def command_update(user_id, text="/router"):
    message = Message(
        message_id=1, date=datetime.now(timezone.utc), text=text,
        chat=Chat(id=user_id, type=Chat.PRIVATE), from_user=User(id=user_id, first_name="x", is_bot=False),
        entities=[MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=len(text))],
    )
    return Update(update_id=1, message=message)


class TestmodeCommandTest(unittest.IsolatedAsyncioTestCase):
    async def run_testmode(self, args=None, current=None):
        update = make_update()
        with mock.patch.object(app.db, "get_setting", return_value=current), \
                mock.patch.object(app.db, "set_setting") as set_setting:
            await app.testmode_command(update, make_context(args=args))
        return reply_texts(update), set_setting

    async def test_status(self):
        texts, set_setting = await self.run_testmode(current="1")
        self.assertIn("Тестовий режим увімкнено", texts[0])
        set_setting.assert_not_called()
        texts, _ = await self.run_testmode(current=None)
        self.assertIn("Тестовий режим вимкнено", texts[0])

    async def test_on_and_off(self):
        texts, set_setting = await self.run_testmode(["on"])
        set_setting.assert_called_once_with("test_mode", "1")
        self.assertIn("отримуєте лише ви", texts[0])
        texts, set_setting = await self.run_testmode(["OFF"])
        set_setting.assert_called_once_with("test_mode", "0")
        self.assertIn("знову отримують усі підписники", texts[0])

    async def test_bad_argument(self):
        texts, set_setting = await self.run_testmode(["maybe"])
        set_setting.assert_not_called()
        self.assertIn("Не зрозумів", texts[0])


NAMES = {111: "Олена", 222: "Петро", 333: "Іван", 777: "Власник"}
USERNAMES = {333: "ivan_k"}  # @username відомий лише для Івана


class AdminsCommandTest(unittest.IsolatedAsyncioTestCase):
    """/special і кнопки під ним. Власник — 777, Олена (111) додана в БД, Петро (222) — у Heroku."""

    def setUp(self):
        self.stored = "111"
        self.recent_users = [333, 222, 111, 777]
        self.subscribed = {777, 111, 222, 333}
        patches = [
            mock.patch.object(app.config, "ADMIN_CHAT_ID", 777),
            mock.patch.object(app.config, "INTERNET_ALERT_CHAT_IDS", [222]),
            mock.patch.object(app.db, "get_setting", side_effect=lambda key: self.stored),
            mock.patch.object(app.db, "set_setting", side_effect=self.store),
            mock.patch.object(app.db, "get_users",
                              side_effect=lambda ids: {i: (NAMES[i], USERNAMES.get(i))
                                                       for i in ids if i in NAMES}),
            mock.patch.object(app.db, "get_recent_users", side_effect=self.recent),
            mock.patch.object(app.db, "count_users", side_effect=self.count),
            mock.patch.object(app.db, "filter_subscribed",
                              side_effect=lambda ids: {i for i in ids if i in self.subscribed}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def store(self, key, value):
        self.assertEqual(key, app.admins.SETTING_KEY)
        self.stored = value

    def candidates(self, exclude):
        return [uid for uid in self.recent_users if uid not in exclude]

    def recent(self, limit, exclude, offset=0):
        return [(uid, NAMES.get(uid, f"User{uid}"), USERNAMES.get(uid))
                for uid in self.candidates(exclude)[offset:offset + limit]]

    def count(self, exclude):
        return len(self.candidates(exclude))

    async def press(self, data, user_id=777, watch=None):
        query = SimpleNamespace(data=data, from_user=SimpleNamespace(id=user_id),
                                answer=mock.AsyncMock(), edit_message_text=mock.AsyncMock())
        context = SimpleNamespace(bot=SimpleNamespace(send_message=mock.AsyncMock()),
                                  bot_data={"internet_watch": watch} if watch else {})
        await app.admins_callback(SimpleNamespace(callback_query=query), context)
        return query, context

    @staticmethod
    def buttons(markup):
        return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]

    async def test_list(self):
        update = make_update(user_id=777)
        await app.admins_command(update, make_context())
        call = update.effective_message.reply_text.call_args
        self.assertEqual(call.args[0], "👥 Спеціальні користувачі\nОтримують повідомлення про зникнення й відновлення інтернету вдома:\n"
                                       "• Ви (власник)\n"
                                       "• Олена · 111\n"
                                       "• Петро · 222 (задано в Heroku)")
        self.assertEqual(self.buttons(call.kwargs["reply_markup"]),
                         [("❌ Олена · 111", "adm:del:111"), ("➕ Додати", "adm:add")])

    async def test_unsubscribed_are_marked_owner_too(self):
        self.subscribed = {222}
        update = make_update(user_id=777)
        await app.admins_command(update, make_context())
        self.assertEqual(update.effective_message.reply_text.call_args.args[0],
                         "👥 Спеціальні користувачі\nОтримують повідомлення про зникнення й відновлення інтернету вдома:\n"
                         "• Ви (власник) 🔕\n"
                         "• Олена · 111 🔕\n"
                         "• Петро · 222 (задано в Heroku)\n"
                         "\n🔕 — не підписані на сповіщення про світло, тому повідомлень про "
                         "інтернет не отримують.")

    async def test_add_marks_unsubscribed(self):
        self.subscribed = {777, 111, 222}
        query, _ = await self.press("adm:add")
        call = query.edit_message_text.call_args
        self.assertIn("🔕 — ще не підписані", call.args[0])
        self.assertEqual(self.buttons(call.kwargs["reply_markup"])[0],
                         ("🔕 Іван @ivan_k · 333", "adm:pick:333"))

    async def test_pick_unsubscribed_asks_to_subscribe(self):
        self.subscribed = {777, 111, 222}
        query, context = await self.press("adm:pick:333")
        context.bot.send_message.assert_awaited_once_with(
            chat_id=333, text=app.WELCOME_ADMIN + app.WELCOME_ADMIN_SUBSCRIBE)
        query.answer.assert_awaited_once_with(
            "✅ Іван @ivan_k · 333 додано. Повідомлення отримуватиме, коли підпишеться")

    async def test_add_shows_only_people_who_are_not_admins_yet(self):
        query, _ = await self.press("adm:add")
        call = query.edit_message_text.call_args
        self.assertTrue(call.args[0].startswith("Кого додати до спеціальних користувачів?"))
        self.assertNotIn("Сторінка", call.args[0])  # усі вміщуються на одну сторінку
        self.assertEqual(self.buttons(call.kwargs["reply_markup"]),
                         [("Іван @ivan_k · 333", "adm:pick:333"), ("↩️ Назад до списку", "adm:list")])

    async def test_add_pages(self):
        self.recent_users = list(range(1001, 1021)) + [111, 222, 777]  # 20 кандидатів, по 8 на сторінку
        self.subscribed |= set(range(1001, 1021))
        query, _ = await self.press("adm:add")
        call = query.edit_message_text.call_args
        self.assertIn("Сторінка 1 з 3 (усього 20).", call.args[0])
        buttons = self.buttons(call.kwargs["reply_markup"])
        self.assertEqual(buttons[0], ("User1001 · 1001", "adm:pick:1001"))
        self.assertEqual(buttons[8:], [("Далі ➡️", "adm:add:1"), ("↩️ Назад до списку", "adm:list")])

        query, _ = await self.press("adm:add:1")
        call = query.edit_message_text.call_args
        self.assertIn("Сторінка 2 з 3", call.args[0])
        buttons = self.buttons(call.kwargs["reply_markup"])
        self.assertEqual(buttons[0], ("User1009 · 1009", "adm:pick:1009"))
        self.assertEqual(buttons[8:], [("⬅️ Попередні", "adm:add:0"), ("Далі ➡️", "adm:add:2"),
                                       ("↩️ Назад до списку", "adm:list")])

        query, _ = await self.press("adm:add:2")
        buttons = self.buttons(query.edit_message_text.call_args.kwargs["reply_markup"])
        self.assertEqual(len(buttons), 4 + 2)  # 4 останні + «Попередні» + «До списку»
        self.assertEqual(buttons[-2:], [("⬅️ Попередні", "adm:add:1"), ("↩️ Назад до списку", "adm:list")])

        query, _ = await self.press("adm:add:99")  # за межами — остання сторінка
        self.assertIn("Сторінка 3 з 3", query.edit_message_text.call_args.args[0])

    async def test_nobody_to_add(self):
        self.recent_users = [111, 222, 777]
        query, _ = await self.press("adm:add")
        self.assertTrue(query.edit_message_text.call_args.args[0].startswith("Поки немає кого додати"))

    async def test_pick_adds_admin_and_updates_recipients_at_once(self):
        watch = app.broadcaster.InternetWatch([777, 222, 111])
        query, context = await self.press("adm:pick:333", watch=watch)
        self.assertEqual(self.stored, "111,333")
        self.assertEqual(watch.chat_ids, [777, 222, 111, 333])
        context.bot.send_message.assert_awaited_once_with(chat_id=333, text=app.WELCOME_ADMIN)
        query.answer.assert_awaited_once_with("✅ Іван @ivan_k · 333 тепер отримує повідомлення про інтернет")
        self.assertIn("• Іван @ivan_k · 333", query.edit_message_text.call_args.args[0])

    async def test_delete(self):
        watch = app.broadcaster.InternetWatch([777, 222, 111])
        query, _ = await self.press("adm:del:111", watch=watch)
        self.assertEqual(self.stored, "")
        self.assertEqual(watch.chat_ids, [777, 222])
        query.answer.assert_awaited_once_with("Видалено: Олена · 111")
        self.assertNotIn("Олена", query.edit_message_text.call_args.args[0])

    async def test_back_to_list(self):
        query, _ = await self.press("adm:list")
        self.assertTrue(query.edit_message_text.call_args.args[0].startswith("👥"))

    async def test_buttons_rejected_for_anyone_but_owner(self):
        query, context = await self.press("adm:pick:333", user_id=123)
        query.answer.assert_awaited_once_with("Недоступно")
        query.edit_message_text.assert_not_called()
        context.bot.send_message.assert_not_called()
        self.assertEqual(self.stored, "111")


class RememberUserTest(unittest.IsolatedAsyncioTestCase):
    def update(self, user_id=7, first_name="Олена", username="olena", is_bot=False):
        return SimpleNamespace(effective_user=SimpleNamespace(
            id=user_id, first_name=first_name, username=username, is_bot=is_bot))

    async def test_writes_only_when_something_changed(self):
        context = make_context()
        with mock.patch.object(app.db, "remember_user") as remember:
            await app.remember_user(self.update(), context)
            await app.remember_user(self.update(), context)          # те саме — без запису
            await app.remember_user(self.update(username="olena_new"), context)
            await app.remember_user(self.update(username=None), context)
        self.assertEqual(remember.call_args_list, [
            mock.call(7, "Олена", "olena"),
            mock.call(7, "Олена", "olena_new"),
            mock.call(7, "Олена", None),
        ])

    async def test_bots_and_empty_updates_are_ignored(self):
        with mock.patch.object(app.db, "remember_user") as remember:
            await app.remember_user(self.update(is_bot=True), make_context())
            await app.remember_user(SimpleNamespace(effective_user=None), make_context())
        remember.assert_not_called()

    async def test_db_error_does_not_break_handling(self):
        context = make_context()
        with mock.patch.object(app.db, "remember_user", side_effect=RuntimeError("db down")) as remember:
            await app.remember_user(self.update(), context)
            await app.remember_user(self.update(), context)  # не запам'ятали — спробує ще раз
        self.assertEqual(remember.call_count, 2)

    def test_registered_before_all_other_handlers(self):
        with mock.patch.object(app.config, "TELEGRAM_TOKEN", "123:abc"):
            application = app.build_application()
        [handler] = application.handlers[-1]
        self.assertIsInstance(handler, TypeHandler)
        self.assertIs(handler.callback, app.remember_user)

    def test_user_label(self):
        self.assertEqual(app.user_label(123, "Влад", "vlad"), "Влад @vlad · 123")
        self.assertEqual(app.user_label(123, "Влад"), "Влад · 123")
        self.assertEqual(app.user_label(123, "", None), "Без імені · 123")


class AdminOnlyTest(unittest.TestCase):
    def router_handlers(self, admin_id, command="router"):
        with mock.patch.object(app.config, "TELEGRAM_TOKEN", "123:abc"), \
                mock.patch.object(app.config, "ADMIN_CHAT_ID", admin_id):
            application = app.build_application()
        return [h for h in application.handlers[0]
                if isinstance(h, CommandHandler) and command in h.commands]

    def test_admin_commands_only_for_admin(self):
        for command in ("router", "testmode", "special", "admins"):  # /admins — стара назва
            [handler] = self.router_handlers(777, command)
            self.assertTrue(handler.filters.check_update(command_update(777, f"/{command}")))
            self.assertFalse(handler.filters.check_update(command_update(123, f"/{command}")))

    def test_no_admin_commands_without_admin(self):
        for command in ("router", "testmode", "special", "admins"):
            self.assertEqual(self.router_handlers(None, command), [])

    def test_admin_buttons_handler_only_with_admin(self):
        def callback_handlers(admin_id):
            with mock.patch.object(app.config, "TELEGRAM_TOKEN", "123:abc"), \
                    mock.patch.object(app.config, "ADMIN_CHAT_ID", admin_id):
                application = app.build_application()
            return [h for h in application.handlers[0] if isinstance(h, CallbackQueryHandler)]
        self.assertEqual(len(callback_handlers(777)), 1)
        self.assertEqual(callback_handlers(None), [])

    def test_callback_queries_are_received(self):
        self.assertIn(Update.CALLBACK_QUERY, app.ALLOWED_UPDATES)

    def test_admin_menu_has_router(self):
        admin_menu = [c.command for c in app.chat_commands(True, admin=True)]
        self.assertIn("router", admin_menu)
        self.assertIn("special", admin_menu)
        self.assertNotIn("router", [c.command for c in app.chat_commands(True)])
        self.assertNotIn("special", [c.command for c in app.chat_commands(True)])
        with mock.patch.object(app.config, "ADMIN_CHAT_ID", 777):
            self.assertTrue(app.is_admin(777))
            self.assertFalse(app.is_admin(123))
        with mock.patch.object(app.config, "ADMIN_CHAT_ID", None):
            self.assertFalse(app.is_admin(777))


class LifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_monitor_starts_even_if_menu_fails_and_stops_cleanly(self):
        started = asyncio.Event()

        async def fake_monitor(bot, checker, tracker, admin_watch, internet_watch):
            self.assertIsInstance(admin_watch, app.broadcaster.AdminWatch)
            self.assertIsInstance(internet_watch, app.broadcaster.InternetWatch)
            self.assertEqual(internet_watch.chat_ids, app.config.internet_alert_chat_ids())
            started.set()
            await asyncio.Event().wait()

        application = SimpleNamespace(
            bot=SimpleNamespace(set_my_commands=mock.AsyncMock(side_effect=NetworkError("down"))),
            bot_data={},
        )
        with mock.patch.object(app.db, "init_db") as init_db, \
                mock.patch.object(app.db, "get_light_state",
                                  return_value={"has_light": False, "since": 123, "offline": False, "light_since": None}), \
                mock.patch.object(app.db, "get_setting", return_value=None), \
                mock.patch.object(app.broadcaster, "monitor_loop", fake_monitor):
            await app.post_init(application)
            await asyncio.wait_for(started.wait(), 1)

        init_db.assert_called_once()
        tracker = application.bot_data["tracker"]
        self.assertEqual((tracker.has_light, tracker.since), (False, 123))

        task = application.bot_data["monitor_task"]
        await app.post_stop(application)
        self.assertTrue(task.cancelled())

    async def test_admin_gets_router_menu_on_start(self):
        async def fake_monitor(*args):
            await asyncio.Event().wait()

        application = SimpleNamespace(bot=SimpleNamespace(set_my_commands=mock.AsyncMock()), bot_data={})
        with mock.patch.object(app.config, "ADMIN_CHAT_ID", 777), \
                mock.patch.object(app.db, "init_db"), \
                mock.patch.object(app.db, "get_light_state", return_value=None), \
                mock.patch.object(app.db, "get_user_subscription", return_value=True), \
                mock.patch.object(app.db, "get_setting", return_value="1"), \
                mock.patch.object(app.broadcaster, "monitor_loop", fake_monitor):
            await app.post_init(application)
            await app.post_stop(application)
        admin_call = application.bot.set_my_commands.call_args_list[-1]
        self.assertEqual(admin_call.kwargs["scope"].chat_id, 777)
        self.assertEqual([c.command for c in admin_call.args[0]],
                         ["unsubscribe", "check", "help", "router", "testmode", "special"])

    def test_build_application_registers_handlers(self):
        with mock.patch.object(app.config, "TELEGRAM_TOKEN", "123:abc"), \
                mock.patch.object(app.config, "ADMIN_CHAT_ID", None):
            application = app.build_application()
        self.assertEqual(len(application.handlers[0]), 11)
        self.assertEqual(len(application.error_handlers), 1)


if __name__ == "__main__":
    unittest.main()
