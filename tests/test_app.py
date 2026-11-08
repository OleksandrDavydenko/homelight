import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from telegram import Chat, Message, MessageEntity, Update, User
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import NetworkError
from telegram.ext import CommandHandler

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


def make_context(**bot_data):
    return SimpleNamespace(bot=SimpleNamespace(set_my_commands=mock.AsyncMock()), bot_data=bot_data)


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


def router_checker(result=None, online=True, host="195.0.2.10"):
    checker = mock.Mock(router_host=host, router_port=80)
    checker.check_router.return_value = (result, online)
    return checker


def reply_texts(update):
    return [c.args[0] for c in update.effective_message.reply_text.call_args_list]


class RouterCommandTest(unittest.IsolatedAsyncioTestCase):
    async def run_router(self, checker):
        update = make_update()
        await app.router_command(update, make_context(checker=checker))
        return reply_texts(update)

    async def test_not_configured(self):
        checker = router_checker(host=None)
        texts = await self.run_router(checker)
        self.assertEqual(len(texts), 1)
        self.assertIn("ROUTER_HOST не задано", texts[0])
        checker.check_router.assert_not_called()

    async def test_open_port(self):
        texts = await self.run_router(router_checker(light_checker.ROUTER_OPEN))
        self.assertEqual(texts[0], "🔄 Перевіряю роутер...")
        self.assertIn("✅ Роутер 195.0.2.10:80 відповідає: порт відкритий", texts[1])
        self.assertTrue(texts[1].endswith("Висновок бота: інтернет у будинку є."))

    async def test_refused(self):
        texts = await self.run_router(router_checker(light_checker.ROUTER_REFUSED))
        self.assertIn("відповідає відмовою", texts[1])
        self.assertNotIn("обладнання провайдера", texts[1])
        self.assertTrue(texts[1].endswith("інтернет у будинку є."))

    async def test_refused_in_strict_mode(self):
        texts = await self.run_router(router_checker(light_checker.ROUTER_REFUSED, online=False))
        self.assertIn("обладнання провайдера", texts[1])
        self.assertTrue(texts[1].endswith("інтернет у будинку немає."))

    async def test_silent(self):
        texts = await self.run_router(router_checker(light_checker.ROUTER_SILENT, online=False))
        self.assertIn("❌ Роутер 195.0.2.10:80 не відповідає.", texts[1])
        self.assertTrue(texts[1].endswith("інтернет у будинку немає."))


def command_update(user_id, text="/router"):
    message = Message(
        message_id=1, date=datetime.now(timezone.utc), text=text,
        chat=Chat(id=user_id, type=Chat.PRIVATE), from_user=User(id=user_id, first_name="x", is_bot=False),
        entities=[MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=len(text))],
    )
    return Update(update_id=1, message=message)


class AdminOnlyTest(unittest.TestCase):
    def router_handlers(self, admin_id):
        with mock.patch.object(app.config, "TELEGRAM_TOKEN", "123:abc"), \
                mock.patch.object(app.config, "ADMIN_CHAT_ID", admin_id):
            application = app.build_application()
        return [h for h in application.handlers[0]
                if isinstance(h, CommandHandler) and "router" in h.commands]

    def test_router_command_only_for_admin(self):
        [handler] = self.router_handlers(777)
        self.assertTrue(handler.filters.check_update(command_update(777)))
        self.assertFalse(handler.filters.check_update(command_update(123)))

    def test_no_router_command_without_admin(self):
        self.assertEqual(self.router_handlers(None), [])

    def test_admin_menu_has_router(self):
        self.assertIn("router", [c.command for c in app.chat_commands(True, admin=True)])
        self.assertNotIn("router", [c.command for c in app.chat_commands(True)])
        with mock.patch.object(app.config, "ADMIN_CHAT_ID", 777):
            self.assertTrue(app.is_admin(777))
            self.assertFalse(app.is_admin(123))
        with mock.patch.object(app.config, "ADMIN_CHAT_ID", None):
            self.assertFalse(app.is_admin(777))


class LifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_monitor_starts_even_if_menu_fails_and_stops_cleanly(self):
        started = asyncio.Event()

        async def fake_monitor(bot, checker, tracker, admin_watch):
            self.assertIsInstance(admin_watch, app.broadcaster.AdminWatch)
            started.set()
            await asyncio.Event().wait()

        application = SimpleNamespace(
            bot=SimpleNamespace(set_my_commands=mock.AsyncMock(side_effect=NetworkError("down"))),
            bot_data={},
        )
        with mock.patch.object(app.db, "init_db") as init_db, \
                mock.patch.object(app.db, "get_light_state",
                                  return_value={"has_light": False, "since": 123, "offline": False, "light_since": None}), \
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
                mock.patch.object(app.broadcaster, "monitor_loop", fake_monitor):
            await app.post_init(application)
            await app.post_stop(application)
        admin_call = application.bot.set_my_commands.call_args_list[-1]
        self.assertEqual(admin_call.kwargs["scope"].chat_id, 777)
        self.assertEqual([c.command for c in admin_call.args[0]],
                         ["unsubscribe", "check", "help", "router"])

    def test_build_application_registers_handlers(self):
        with mock.patch.object(app.config, "TELEGRAM_TOKEN", "123:abc"), \
                mock.patch.object(app.config, "ADMIN_CHAT_ID", None):
            application = app.build_application()
        self.assertEqual(len(application.handlers[0]), 11)
        self.assertEqual(len(application.error_handlers), 1)


if __name__ == "__main__":
    unittest.main()
