import unittest
from unittest import mock

from telegram.error import BadRequest, Forbidden, RetryAfter

import broadcaster
from light_checker import Reading, StateTracker


NOT_TEST_MODE = mock.Mock(return_value=None)  # тестовий режим вимкнено


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
@mock.patch.object(broadcaster.db, "get_setting", NOT_TEST_MODE)
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
@mock.patch.object(broadcaster.db, "get_setting", NOT_TEST_MODE)
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
        await self.feed(watch, bot, [SHELLY_ERROR, SHELLY_ERROR, LIGHT])  # 180 с без даних — ще не час
        self.assertEqual(bot.sent, [])

    async def test_long_shelly_outage_is_reported_once_and_recovery_too(self):
        bot, watch = FakeBot(), broadcaster.AdminWatch(ADMIN)
        await self.feed(watch, bot, [SHELLY_ERROR] * 6 + [LIGHT])
        self.assertEqual([chat for chat, _ in bot.sent], [ADMIN, ADMIN])
        self.assertIn("Не вдається отримати дані з Shelly", bot.sent[0][1])
        self.assertIn("wrong_auth_key", bot.sent[0][1])
        self.assertEqual(bot.sent[1][1], "✅ Дані з Shelly знову надходять (не було 9 хв 0 сек).")

    async def test_internet_outage_is_left_to_internet_watch(self):
        bot, watch = FakeBot(), broadcaster.AdminWatch(ADMIN)
        await self.feed(watch, bot, [NO_INTERNET] * 6)
        self.assertEqual(bot.sent, [])

    async def test_disabled_without_admin_chat_id(self):
        bot, watch = FakeBot(), broadcaster.AdminWatch(None)
        await self.feed(watch, bot, [SHELLY_ERROR] * 6 + [LIGHT])
        self.assertEqual(bot.sent, [])


class InternetWatchTest(unittest.IsolatedAsyncioTestCase):
    async def feed(self, watch, bot, states, shelly_online=False, start=1000, step=60):
        for i, internet in enumerate(states):
            await watch.observe(bot, internet, shelly_online, start + i * step)

    async def test_short_outage_is_not_reported(self):
        bot, watch = FakeBot(), broadcaster.InternetWatch(ADMIN)
        await self.feed(watch, bot, [False, False, False, True])  # 120 с — ще не час
        self.assertEqual(bot.sent, [])
        self.assertFalse(watch.pending)

    async def test_long_outage_is_reported_once_and_recovery_too(self):
        bot, watch = FakeBot(), broadcaster.InternetWatch(ADMIN)
        await self.feed(watch, bot, [False] * 3)
        self.assertTrue(watch.pending)
        await self.feed(watch, bot, [False] * 3 + [True], start=1180)
        self.assertEqual([chat for chat, _ in bot.sent], [ADMIN, ADMIN])
        self.assertIn("⚠️ Вдома немає інтернету", bot.sent[0][1])
        self.assertNotIn("Shelly на зв'язку", bot.sent[0][1])
        self.assertEqual(bot.sent[1][1], "✅ Інтернет удома відновився (не було 6 хв 0 сек).")

    async def test_note_when_shelly_is_online(self):
        bot, watch = FakeBot(), broadcaster.InternetWatch(ADMIN)
        await self.feed(watch, bot, [False] * 5, shelly_online=True)
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("Shelly на зв'язку", bot.sent[0][1])

    async def test_unknown_is_ignored(self):
        bot, watch = FakeBot(), broadcaster.InternetWatch(ADMIN)
        await self.feed(watch, bot, [None] * 6)
        self.assertEqual(bot.sent, [])
        self.assertFalse(watch.pending)

    async def test_disabled_without_admin_chat_id(self):
        bot, watch = FakeBot(), broadcaster.InternetWatch(None)
        await self.feed(watch, bot, [False] * 6 + [True])
        self.assertEqual(bot.sent, [])
        self.assertFalse(watch.pending)


class RouterChecker:
    """Shelly-читання й перевірка роутера із заданими результатами."""

    def __init__(self, reading, router_online, trusted=True):
        self.reading, self.router_online, self.router_trusted = reading, router_online, trusted
        self.router_host = "home.example"
        self.router_calls = 0

    def check_router(self, max_age=0):
        self.router_calls += 1
        return ("refused" if self.router_online else "silent"), self.router_online

    def get_reading(self):
        return self.reading


@mock.patch.object(broadcaster, "SEND_DELAY", 0)
@mock.patch.object(broadcaster.db, "get_setting", NOT_TEST_MODE)
class RunCheckInternetTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patches = [
            mock.patch.object(broadcaster.db, "get_all_subscribed_users", return_value=[1, 2]),
            mock.patch.object(broadcaster.db, "save_light_state"),
        ]
        self.users, self.save = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)

    async def run_checks(self, checker, times, alert_after=0, tracker=None):
        bot, tracker = FakeBot(), tracker or StateTracker(True, 0)
        admin = broadcaster.AdminWatch(ADMIN, alert_after=alert_after)
        internet = broadcaster.InternetWatch(ADMIN, alert_after=alert_after)
        delays = [await broadcaster.run_check(bot, checker, tracker, admin, internet)
                  for _ in range(times)]
        return bot, tracker, delays

    async def test_no_internet_only_admin_gets_one_message(self):
        bot, tracker, _ = await self.run_checks(RouterChecker(NO_INTERNET, False), 5)
        self.assertEqual([chat for chat, _ in bot.sent], [ADMIN])  # лише адміну, один раз
        self.assertIn("Вдома немає інтернету", bot.sent[0][1])
        self.save.assert_not_called()
        self.assertTrue(tracker.has_light)  # стан світла не змінився

    async def test_router_is_checked_every_time_even_when_shelly_online(self):
        checker = RouterChecker(LIGHT, True)
        bot, _, _ = await self.run_checks(checker, 3)
        self.assertEqual(checker.router_calls, 3)
        self.assertEqual(bot.sent, [])

    async def test_router_silent_while_shelly_online_is_reported_with_note(self):
        bot, _, _ = await self.run_checks(RouterChecker(LIGHT, False), 2)
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("Shelly на зв'язку", bot.sent[0][1])

    async def test_router_that_never_answered_is_not_reported(self):
        bot, _, _ = await self.run_checks(RouterChecker(LIGHT, False, trusted=False), 3)
        self.assertEqual(bot.sent, [])

    async def test_faster_checks_while_internet_outage_is_pending(self):
        _, _, delays = await self.run_checks(RouterChecker(NO_INTERNET, False), 1, alert_after=180)
        self.assertEqual(delays, [broadcaster.CONFIRM_INTERVAL])


@mock.patch.object(broadcaster, "SEND_DELAY", 0)
class TestModeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patches = [
            mock.patch.object(broadcaster.db, "get_setting", return_value="1"),
            mock.patch.object(broadcaster.db, "get_all_subscribed_users", return_value=[1, 2, 3]),
        ]
        self.setting, self.users = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)

    async def test_only_admin_gets_alerts(self):
        bot = FakeBot()
        with mock.patch.object(broadcaster.config, "ADMIN_CHAT_ID", ADMIN):
            sent = await broadcaster.notify_subscribers(bot, "🔴 СВІТЛО ПРОПАЛО!")
        self.assertEqual(sent, 1)
        self.assertEqual(bot.sent, [(ADMIN, broadcaster.TEST_MODE_PREFIX + "🔴 СВІТЛО ПРОПАЛО!")])
        self.users.assert_not_called()
        self.setting.assert_called_once_with("test_mode")

    async def test_nobody_gets_alerts_without_admin(self):
        bot = FakeBot()
        with mock.patch.object(broadcaster.config, "ADMIN_CHAT_ID", None):
            sent = await broadcaster.notify_subscribers(bot, "msg")
        self.assertEqual((sent, bot.sent), (0, []))

    async def test_off_means_everybody(self):
        self.setting.return_value = "0"
        bot = FakeBot()
        with mock.patch.object(broadcaster.config, "ADMIN_CHAT_ID", ADMIN):
            sent = await broadcaster.notify_subscribers(bot, "msg")
        self.assertEqual(sent, 3)
        self.assertEqual([chat for chat, _ in bot.sent], [1, 2, 3])

    def test_set_test_mode(self):
        with mock.patch.object(broadcaster.db, "set_setting") as set_setting:
            broadcaster.set_test_mode(True)
            broadcaster.set_test_mode(False)
        self.assertEqual(set_setting.call_args_list,
                         [mock.call("test_mode", "1"), mock.call("test_mode", "0")])


if __name__ == "__main__":
    unittest.main()
