import unittest
from unittest import mock

from telegram.error import BadRequest, Forbidden, RetryAfter

import broadcaster
from light_checker import Reading, StateTracker


NOT_TEST_MODE = mock.Mock(return_value=None)  # тестовий режим вимкнено
ALL_SUBSCRIBED = mock.Mock(side_effect=lambda ids: set(ids))  # усі адміни підписані


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
        self.assertTrue(bot.sent[0][1].startswith("🟠 Немає даних від датчика світла\n"))
        self.assertIn("Подробиці — у логах бота", bot.sent[0][1])
        self.assertNotIn("wrong_auth_key", bot.sent[0][1])  # технічні подробиці лише в логах
        self.assertEqual(bot.sent[1][1], "🟢 Дані від датчика світла знову надходять\n"
                                         "Їх не було 9 хв 0 сек.")
        self.assertFalse(any("Shelly" in text for _, text in bot.sent))

    async def test_internet_outage_is_left_to_internet_watch(self):
        bot, watch = FakeBot(), broadcaster.AdminWatch(ADMIN)
        await self.feed(watch, bot, [NO_INTERNET] * 6)
        self.assertEqual(bot.sent, [])

    async def test_disabled_without_admin_chat_id(self):
        bot, watch = FakeBot(), broadcaster.AdminWatch(None)
        await self.feed(watch, bot, [SHELLY_ERROR] * 6 + [LIGHT])
        self.assertEqual(bot.sent, [])


@mock.patch.object(broadcaster.db, "filter_subscribed", ALL_SUBSCRIBED)
class InternetWatchTest(unittest.IsolatedAsyncioTestCase):
    async def feed(self, watch, bot, states, shelly_online=False, start=1000, step=60):
        for i, internet in enumerate(states):
            await watch.observe(bot, internet, shelly_online, start + i * step)

    async def test_short_outage_is_not_reported(self):
        bot, watch = FakeBot(), broadcaster.InternetWatch([ADMIN])
        await self.feed(watch, bot, [False, False, False, True])  # 120 с — ще не час
        self.assertEqual(bot.sent, [])
        self.assertFalse(watch.pending)

    async def test_long_outage_is_reported_once_and_recovery_too(self):
        bot, watch = FakeBot(), broadcaster.InternetWatch([ADMIN])
        await self.feed(watch, bot, [False] * 3)
        self.assertTrue(watch.pending)
        await self.feed(watch, bot, [False] * 3 + [True], start=1180)
        self.assertEqual([chat for chat, _ in bot.sent], [ADMIN, ADMIN])
        self.assertTrue(bot.sent[0][1].startswith("🔴 Вдома немає інтернету\n"))
        self.assertIn("бот не бачить світла й нічого не надсилає підписникам", bot.sent[0][1])
        self.assertNotIn("датчик світла на зв'язку", bot.sent[0][1])
        self.assertEqual(bot.sent[1][1], "🟢 Інтернет удома відновився\nЙого не було 6 хв 0 сек.")

    async def test_note_when_shelly_is_online(self):
        bot, watch = FakeBot(), broadcaster.InternetWatch([ADMIN])
        await self.feed(watch, bot, [False] * 5, shelly_online=True)
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("датчик світла на зв'язку", bot.sent[0][1])
        self.assertNotIn("Shelly", bot.sent[0][1])

    async def test_two_recipients_get_both_messages(self):
        bot, watch = FakeBot(), broadcaster.InternetWatch([ADMIN, 888])
        await self.feed(watch, bot, [False] * 4 + [True])
        self.assertEqual([chat for chat, _ in bot.sent], [ADMIN, 888, ADMIN, 888])

    async def test_failure_for_one_recipient_does_not_stop_others(self):
        bot = FakeBot({ADMIN: [RuntimeError("boom")]})
        watch = broadcaster.InternetWatch([ADMIN, 888])
        await self.feed(watch, bot, [False] * 4)
        self.assertEqual([chat for chat, _ in bot.sent], [888])

    async def test_only_subscribed_admins_get_messages_owner_too(self):
        bot = FakeBot()
        watch = broadcaster.InternetWatch([ADMIN, 888, 999])
        with mock.patch.object(broadcaster.db, "filter_subscribed", return_value={888}) as subscribed:
            await self.feed(watch, bot, [False] * 4 + [True])
        self.assertEqual([chat for chat, _ in bot.sent], [888, 888])  # «🔴», потім «🟢»
        subscribed.assert_called_with([ADMIN, 888, 999])

    async def test_everyone_gets_messages_if_subscription_check_fails(self):
        bot = FakeBot()
        watch = broadcaster.InternetWatch([ADMIN, 888])
        with mock.patch.object(broadcaster.db, "filter_subscribed", side_effect=RuntimeError("db down")):
            await self.feed(watch, bot, [False] * 4)
        self.assertEqual([chat for chat, _ in bot.sent], [ADMIN, 888])

    async def test_unknown_is_ignored(self):
        bot, watch = FakeBot(), broadcaster.InternetWatch([ADMIN])
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
@mock.patch.object(broadcaster.db, "filter_subscribed", ALL_SUBSCRIBED)
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
        internet = broadcaster.InternetWatch([ADMIN], alert_after=alert_after)
        delays = [await broadcaster.run_check(bot, checker, tracker, admin, internet)
                  for _ in range(times)]
        return bot, tracker, delays

    async def test_no_internet_only_admin_gets_one_message(self):
        bot, tracker, _ = await self.run_checks(RouterChecker(NO_INTERNET, False), 5)
        self.assertEqual([chat for chat, _ in bot.sent], [ADMIN])  # лише адміну, один раз
        self.assertIn("🔴 Вдома немає інтернету", bot.sent[0][1])
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
        self.assertIn("датчик світла на зв'язку", bot.sent[0][1])

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



T0 = 1_700_000_000


def plug_on(boot):
    return Reading(has_light=True, fetched_at=0, voltage=229.0, online=True, boot_time=boot,
                   output=False)


class ReserveWatchTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = mock.patch.object(broadcaster.db, "set_setting")
        self.set_setting = patcher.start()
        self.addCleanup(patcher.stop)

    async def test_only_owner_gets_reserve_messages_and_state_is_saved(self):
        bot, watch = FakeBot(), broadcaster.ReserveWatch(ADMIN)
        city = StateTracker(False, T0)
        await watch.observe(bot, plug_on(T0 + 600), city, True, T0 + 800)
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(bot.sent[0][0], ADMIN)
        self.assertTrue(bot.sent[0][1].startswith("🔋 Ви перейшли на резерв"))
        key, value = self.set_setting.call_args.args
        self.assertEqual(key, broadcaster.RESERVE_STATE_KEY)
        self.assertTrue(broadcaster.json.loads(value)["on"])

    async def test_state_saved_only_when_changed(self):
        bot = FakeBot()
        watch = broadcaster.ReserveWatch(ADMIN)
        city = StateTracker(True, T0)
        for now in (T0 + 100, T0 + 200):
            await watch.observe(bot, plug_on(T0 - 3600), city, True, now)
        self.set_setting.assert_not_called()
        self.assertEqual(bot.sent, [])

    async def test_saving_failure_does_not_stop_message(self):
        self.set_setting.side_effect = RuntimeError("db down")
        bot, watch = FakeBot(), broadcaster.ReserveWatch(ADMIN)
        await watch.observe(bot, plug_on(T0 + 600), StateTracker(False, T0), True, T0 + 800)
        self.assertEqual(len(bot.sent), 1)

    async def test_disabled_without_owner(self):
        bot, watch = FakeBot(), broadcaster.ReserveWatch(None)
        await watch.observe(bot, plug_on(T0 + 600), StateTracker(False, T0), True, T0 + 800)
        self.assertEqual(bot.sent, [])
        self.assertFalse(watch.tracker.on)

    def test_load_state(self):
        for raw, expected in ((None, None), ("", None), ("not json", None), ("[1]", None),
                              ('{"on": true}', {"on": True})):
            with mock.patch.object(broadcaster.db, "get_setting", return_value=raw):
                self.assertEqual(broadcaster.load_reserve_state(), expected, raw)


class PlugChecker:
    def __init__(self, *readings):
        self.readings = list(readings)

    def get_reading(self):
        return self.readings.pop(0)


@mock.patch.object(broadcaster, "SEND_DELAY", 0)
@mock.patch.object(broadcaster.db, "get_setting", NOT_TEST_MODE)
class RunCheckReserveTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patches = [
            mock.patch.object(broadcaster.db, "get_all_subscribed_users", return_value=[42]),
            mock.patch.object(broadcaster.db, "save_light_state"),
            mock.patch.object(broadcaster.db, "set_setting"),
            mock.patch.object(broadcaster.config, "ADMIN_CHAT_ID", ADMIN),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    async def test_reserve_uses_city_state_after_observe(self):
        # Місто зникло (модуль офлайн, роутер відповідає), а розетка вже на резерві
        city_off = Reading(has_light=False, fetched_at=0, online=False, internet=True,
                           plug=plug_on(T0 + 200))
        checker = PlugChecker(city_off, city_off)
        bot, tracker = FakeBot(), StateTracker(True, T0 - 3600)
        watch = broadcaster.ReserveWatch(ADMIN)
        with mock.patch.object(broadcaster.time, "time", side_effect=[T0 + 300, T0 + 400]):
            await broadcaster.run_check(bot, checker, tracker, reserve_watch=watch)
            self.assertEqual(bot.sent, [])  # відключення ще не підтверджене
            await broadcaster.run_check(bot, checker, tracker, reserve_watch=watch)
        self.assertEqual([chat for chat, _ in bot.sent], [42, ADMIN])
        self.assertEqual(bot.sent[0][1], "🔴 СВІТЛО ПРОПАЛО!")
        self.assertTrue(bot.sent[1][1].startswith("🔋 Ви перейшли на резерв"))

    async def test_city_return_is_not_reserve(self):
        # Місто повернулося: модуль на зв'язку, бот ще підтверджує — резерву немає
        city_back = Reading(has_light=True, fetched_at=0, online=True, voltage=230,
                            plug=plug_on(T0 - 400))
        bot, tracker = FakeBot(), StateTracker(False, T0 - 3600)
        watch = broadcaster.ReserveWatch(ADMIN)
        with mock.patch.object(broadcaster.time, "time", side_effect=[T0, T0 + 20]):
            await broadcaster.run_check(bot, PlugChecker(city_back, city_back), tracker,
                                        reserve_watch=watch)
            await broadcaster.run_check(bot, PlugChecker(city_back), tracker, reserve_watch=watch)
        self.assertFalse(watch.tracker.on)
        self.assertEqual([chat for chat, _ in bot.sent], [42])  # лише «світло повернулось»

    async def test_reserve_errors_do_not_break_check(self):
        watch = mock.Mock()
        watch.observe = mock.AsyncMock(side_effect=RuntimeError("boom"))
        reading = Reading(has_light=True, fetched_at=0, online=True, plug=plug_on(T0))
        delay = await broadcaster.run_check(FakeBot(), PlugChecker(reading), StateTracker(True, T0),
                                            reserve_watch=watch)
        self.assertEqual(delay, broadcaster.POLL_INTERVAL)
        self.assertIs(watch.observe.call_args.args[1], reading.plug)


if __name__ == "__main__":
    unittest.main()
