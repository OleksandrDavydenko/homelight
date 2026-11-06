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

    async def send_message(self, chat_id, text):
        errors = self.failures.get(chat_id)
        if errors:
            raise errors.pop(0)
        self.sent.append((chat_id, text))


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
        self.assertIs(self.save.call_args.args[0], False)

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


if __name__ == "__main__":
    unittest.main()
