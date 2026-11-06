import unittest
from unittest import mock

from telegram.error import BadRequest, Forbidden, RetryAfter

import broadcaster
from light_checker import Reading, StateTracker


class FakeBot:
    def __init__(self, failures=None):
        # user_id -> список винятків, які кидати при наступних спробах
        self.failures = {uid: list(errs) for uid, errs in (failures or {}).items()}
        self.sent = []
        self.markups = []

    async def send_message(self, chat_id, text, reply_markup=None):
        errors = self.failures.get(chat_id)
        if errors:
            raise errors.pop(0)
        self.sent.append((chat_id, text))
        self.markups.append(reply_markup)


class FakeChecker:
    def __init__(self, *values):
        self.values = list(values)

    def get_reading(self):
        return Reading(has_light=self.values.pop(0), fetched_at=0)


@mock.patch.object(broadcaster, "SEND_DELAY", 0)
class NotifyTest(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_user_is_unsubscribed(self):
        bot = FakeBot({2: [Forbidden("bot was blocked by the user")]})
        with mock.patch.object(broadcaster.db, "get_all_subscribed_users", return_value=[1, 2, 3]), \
                mock.patch.object(broadcaster.db, "update_subscription") as update:
            sent = await broadcaster.notify_subscribers(bot, "msg")
        self.assertEqual(sent, 2)
        self.assertEqual([uid for uid, _ in bot.sent], [1, 3])
        update.assert_called_once_with(2, False)

    async def test_alerts_carry_subscriber_keyboard(self):
        bot = FakeBot()
        with mock.patch.object(broadcaster.db, "get_all_subscribed_users", return_value=[1]):
            await broadcaster.notify_subscribers(bot, "msg")
        markup = bot.markups[0]
        self.assertEqual(markup.keyboard[0][0].text, "🔕 Відписатись")
        self.assertTrue(markup.is_persistent)

    async def test_retry_after_is_retried_once(self):
        bot = FakeBot({1: [RetryAfter(0)]})
        with mock.patch.object(broadcaster.db, "get_all_subscribed_users", return_value=[1]):
            sent = await broadcaster.notify_subscribers(bot, "msg")
        self.assertEqual(sent, 1)

    async def test_other_errors_do_not_stop_broadcast(self):
        bot = FakeBot({1: [BadRequest("Chat not found")]})
        with mock.patch.object(broadcaster.db, "get_all_subscribed_users", return_value=[1, 2]), \
                mock.patch.object(broadcaster.db, "update_subscription") as update:
            sent = await broadcaster.notify_subscribers(bot, "msg")
        self.assertEqual(sent, 1)
        update.assert_not_called()


@mock.patch.object(broadcaster, "SEND_DELAY", 0)
class RunCheckTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patches = [
            mock.patch.object(broadcaster.db, "get_all_subscribed_users", return_value=[42]),
            mock.patch.object(broadcaster.db, "save_light_state"),
        ]
        self.users, self.save = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)

    async def test_initial_state_is_saved_silently(self):
        bot, tracker = FakeBot(), StateTracker()
        delay = await broadcaster.run_check(bot, FakeChecker(True), tracker)
        self.assertEqual(delay, broadcaster.POLL_INTERVAL)
        self.save.assert_called_once()
        self.assertEqual(bot.sent, [])

    async def test_outage_alert_after_confirmation(self):
        bot, tracker = FakeBot(), StateTracker(True, 1000)
        checker = FakeChecker(False, False)

        delay = await broadcaster.run_check(bot, checker, tracker)
        self.assertEqual(delay, broadcaster.CONFIRM_INTERVAL)
        self.assertEqual(bot.sent, [])
        self.save.assert_not_called()

        delay = await broadcaster.run_check(bot, checker, tracker)
        self.assertEqual(delay, broadcaster.POLL_INTERVAL)
        self.assertEqual(bot.sent, [(42, "🔴 СВІТЛО ПРОПАЛО!")])
        self.assertIs(self.save.call_args.args[0]["has_light"], False)

    async def test_unknown_readings_change_nothing(self):
        bot, tracker = FakeBot(), StateTracker(True, 1000)
        for _ in range(3):
            await broadcaster.run_check(bot, FakeChecker(None), tracker)
        self.assertEqual(bot.sent, [])
        self.save.assert_not_called()
        self.assertTrue(tracker.has_light)

    async def test_alert_is_sent_even_if_saving_fails(self):
        self.save.side_effect = RuntimeError("db down")
        bot, tracker = FakeBot(), StateTracker(False, 1000)
        checker = FakeChecker(True, True)
        await broadcaster.run_check(bot, checker, tracker)
        await broadcaster.run_check(bot, checker, tracker)
        self.assertEqual(len(bot.sent), 1)
        self.assertTrue(bot.sent[0][1].startswith("🟢 СВІТЛО ПОВЕРНУЛОСЬ!"))


ADMIN = 777
NO_INTERNET = Reading(has_light=None, fetched_at=0, online=False, internet=False,
                      error="Немає інтернету в будинку")
SHELLY_ERROR = Reading(has_light=None, fetched_at=0, error="Shelly API помилка: wrong_auth_key")
LIGHT = Reading(has_light=True, fetched_at=0, online=True)


class AdminWatchTest(unittest.IsolatedAsyncioTestCase):
    async def feed(self, watch, bot, readings, start=1000, step=90):
        for i, reading in enumerate(readings):
            await watch.observe(bot, reading, start + i * step)

    async def test_short_gap_is_not_reported(self):
        bot, watch = FakeBot(), broadcaster.AdminWatch(ADMIN)
        await self.feed(watch, bot, [NO_INTERNET, NO_INTERNET, LIGHT])  # 180 с без даних — ще не час
        self.assertEqual(bot.sent, [])

    async def test_long_internet_outage_is_reported_once_and_recovery_too(self):
        bot, watch = FakeBot(), broadcaster.AdminWatch(ADMIN)
        await self.feed(watch, bot, [NO_INTERNET] * 6 + [LIGHT])
        self.assertEqual([chat for chat, _ in bot.sent], [ADMIN, ADMIN])
        self.assertIn("У будинку немає інтернету", bot.sent[0][1])
        self.assertIn("підписникам нічого не надсилається", bot.sent[0][1])
        self.assertEqual(bot.sent[1][1], "✅ Зв'язок відновився (не було 9 хв 0 сек).")

    async def test_shelly_error_is_reported_with_reason(self):
        bot, watch = FakeBot(), broadcaster.AdminWatch(ADMIN)
        await self.feed(watch, bot, [SHELLY_ERROR] * 4)
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("Не вдається отримати дані з Shelly", bot.sent[0][1])
        self.assertIn("wrong_auth_key", bot.sent[0][1])

    async def test_disabled_without_admin_chat_id(self):
        bot, watch = FakeBot(), broadcaster.AdminWatch(None)
        await self.feed(watch, bot, [NO_INTERNET] * 6 + [LIGHT])
        self.assertEqual(bot.sent, [])


@mock.patch.object(broadcaster, "SEND_DELAY", 0)
class RunCheckAdminTest(unittest.IsolatedAsyncioTestCase):
    async def test_subscribers_never_get_internet_alerts(self):
        class Checker:
            def get_reading(self):
                return NO_INTERNET

        bot, tracker = FakeBot(), StateTracker(True, 0)
        watch = broadcaster.AdminWatch(ADMIN, alert_after=0)
        with mock.patch.object(broadcaster.db, "get_all_subscribed_users", return_value=[1, 2]), \
                mock.patch.object(broadcaster.db, "save_light_state") as save:
            for _ in range(5):
                await broadcaster.run_check(bot, Checker(), tracker, watch)
        self.assertEqual([chat for chat, _ in bot.sent], [ADMIN])  # лише адміну, один раз
        save.assert_not_called()
        self.assertTrue(tracker.has_light)  # стан світла не змінився


if __name__ == "__main__":
    unittest.main()
