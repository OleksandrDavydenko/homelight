import unittest

import reserve
from light_checker import Reading
from reserve import (
    BOILER_ON,
    CITY_BACK,
    NOT_RESERVE,
    OFFLINE_CONFIRM,
    RESUMED,
    START_CONFIRM,
    STARTED,
    STOPPED,
    SWITCHED_TO_CITY,
    ReserveEvent,
    ReserveTracker,
    format_reserve_event,
    format_reserve_status,
)

T0 = 1_700_000_000  # місто зникло


def plug_on(boot, now, output=False):
    return Reading(has_light=True, fetched_at=now, voltage=229.0, online=True,
                   boot_time=boot, output=output)


def plug_off(now):
    return Reading(has_light=False, fetched_at=now, online=False)


class ReserveTrackerTest(unittest.TestCase):
    def setUp(self):
        self.tracker = ReserveTracker()

    def observe(self, plug, city, city_since, now, internet=None, city_online=None):
        return self.tracker.observe(plug, city, city_since, internet, now, city_online=city_online)

    def start_reserve(self, boot=T0 + 600, now=T0 + 600 + START_CONFIRM):
        events = self.observe(plug_on(boot, now), False, T0, now)
        self.assertEqual([e.kind for e in events], [STARTED])
        return events[0]

    def test_start_on_reserve(self):
        # Перші хвилини після появи напруги — ще не резерв (модуль міста може виходити на зв'язок)
        self.assertEqual(self.observe(plug_on(T0 + 600, T0 + 700), False, T0, T0 + 700), [])
        self.assertFalse(self.tracker.on)
        event = self.start_reserve()
        self.assertEqual(event.since, T0 + 600)  # момент перемикання — запуск розетки
        self.assertEqual(event.city_since, T0)
        self.assertIs(event.boiler_on, False)
        self.assertTrue(self.tracker.on)
        # Повторне читання — без нових повідомлень
        self.assertEqual(self.observe(plug_on(T0 + 600, T0 + 800), False, T0, T0 + 800), [])

    def test_no_reserve_while_plug_without_power(self):
        self.assertEqual(self.observe(plug_off(T0 + 100), False, T0, T0 + 100), [])
        self.assertFalse(self.tracker.on)

    def test_never_starts_while_city_present(self):
        # Перезапуск бота: місто є, розетка давно увімкнена
        self.assertEqual(self.observe(plug_on(T0 - 86400, T0), True, T0 - 3600, T0), [])
        # Розетка щойно перезавантажилась від міста
        self.assertEqual(self.observe(plug_on(T0 - 10, T0), True, T0 - 3600, T0), [])
        self.assertFalse(self.tracker.on)

    def test_plug_did_not_reboot_during_outage(self):
        # Модуль у щитку зник, а розетка не перезавантажувалась — не резерв
        with self.assertLogs("reserve", "WARNING"):
            self.assertEqual(self.observe(plug_on(T0 - 86400, T0 + 100), False, T0, T0 + 100), [])
        self.assertFalse(self.tracker.on)

    def test_quick_switch_before_bot_noticed_outage(self):
        # Перемкнулися швидше, ніж бот помітив відключення
        self.assertEqual(self.observe(plug_on(T0 - 60, T0 + 30), False, T0, T0 + 30), [])
        events = self.observe(plug_on(T0 - 60, T0 + 130), False, T0, T0 + 130)
        self.assertEqual([e.kind for e in events], [STARTED])
        self.assertEqual(events[0].since, T0 - 60)

    def test_unknown_city_state_or_no_plug_data(self):
        self.assertEqual(self.observe(plug_on(T0 + 600, T0 + 700), None, None, T0 + 700), [])
        self.assertEqual(self.observe(None, False, T0, T0 + 700), [])
        no_voltage = Reading(has_light=None, fetched_at=T0, online=True, boot_time=T0 + 600)
        self.assertEqual(self.observe(no_voltage, False, T0, T0 + 700), [])
        self.assertFalse(self.tracker.on)

    def test_city_back_once_then_switch_to_city(self):
        self.start_reserve()
        city_back = T0 + 7200  # модуль у щитку увімкнувся
        events = self.observe(plug_on(T0 + 600, city_back + 60), True, city_back, city_back + 60)
        self.assertEqual([e.kind for e in events], [CITY_BACK])
        self.assertEqual(self.observe(plug_on(T0 + 600, city_back + 150), True, city_back,
                                      city_back + 150), [])
        self.assertTrue(self.tracker.on)

        switched = city_back + 900
        events = self.observe(plug_on(switched, switched + 40), True, city_back, switched + 40)
        self.assertEqual([e.kind for e in events], [SWITCHED_TO_CITY])
        self.assertEqual((events[0].since, events[0].at), (T0 + 600, switched))
        self.assertEqual(events[0].duration, switched - (T0 + 600))
        self.assertFalse(self.tracker.on)
        # Далі — звичайна робота від міста
        self.assertEqual(self.observe(plug_on(switched, switched + 200), True, city_back,
                                      switched + 200), [])

    def test_switch_back_before_bot_noticed_city(self):
        self.start_reserve()
        city_back, switched = T0 + 7200, T0 + 7230
        # Бот ще не помітив повернення міста, а розетка вже перезавантажилась
        self.assertEqual(self.observe(plug_on(switched, switched + 20), False, T0, switched + 20), [])
        self.assertTrue(self.tracker.on)
        events = self.observe(plug_on(switched, switched + 110), True, city_back, switched + 110)
        self.assertEqual([e.kind for e in events], [SWITCHED_TO_CITY])  # без CITY_BACK
        self.assertEqual(events[0].at, switched)

    def test_reserve_restart_keeps_session(self):
        self.start_reserve()
        # Генератор перезапустили — розетка перезавантажилась, міста досі немає
        self.assertEqual(self.observe(plug_on(T0 + 3000, T0 + 3060), False, T0, T0 + 3060), [])
        self.assertTrue(self.tracker.on)
        self.assertEqual(self.tracker.since, T0 + 600)
        self.assertEqual(self.tracker.plug_boot, T0 + 3000)

    def test_stopped_after_confirm(self):
        self.start_reserve()
        self.observe(plug_on(T0 + 600, T0 + 3000), False, T0, T0 + 3000)
        first_off = T0 + 3090
        self.assertEqual(self.observe(plug_off(first_off), False, T0, first_off), [])
        self.assertEqual(self.observe(plug_off(first_off + 90), False, T0, first_off + 90), [])
        events = self.observe(plug_off(first_off + OFFLINE_CONFIRM), False, T0,
                              first_off + OFFLINE_CONFIRM)
        self.assertEqual([e.kind for e in events], [STOPPED])
        self.assertEqual((events[0].since, events[0].at), (T0 + 600, T0 + 3000))
        self.assertIs(events[0].city_has_light, False)
        self.assertFalse(self.tracker.on)

    def test_short_disconnect_is_ignored(self):
        self.start_reserve()
        self.observe(plug_off(T0 + 800), False, T0, T0 + 800)
        self.assertEqual(self.observe(plug_on(T0 + 600, T0 + 890), False, T0, T0 + 890), [])
        # Лічильник зникнення скинуто
        self.observe(plug_off(T0 + 980), False, T0, T0 + 980)
        self.assertEqual(self.observe(plug_off(T0 + 1100), False, T0, T0 + 1100), [])
        self.assertTrue(self.tracker.on)

    def test_no_internet_means_unknown(self):
        self.start_reserve()
        for now in (T0 + 800, T0 + 1000, T0 + 2000):
            self.assertEqual(self.observe(plug_off(now), False, T0, now, internet=False), [])
        self.assertTrue(self.tracker.on)

    def test_resumed_after_false_stop(self):
        self.start_reserve()
        self.observe(plug_off(T0 + 800), False, T0, T0 + 800)
        self.assertEqual([e.kind for e in self.observe(plug_off(T0 + 1000), False, T0, T0 + 1000)],
                         [STOPPED])
        # Розетка повернулася без перезавантаження — резерв не вимикався
        events = self.observe(plug_on(T0 + 605, T0 + 1500), False, T0, T0 + 1500)
        self.assertEqual([e.kind for e in events], [RESUMED])
        self.assertEqual(events[0].since, T0 + 600)
        self.assertTrue(self.tracker.on)

    def test_resumed_then_city_back(self):
        self.start_reserve()
        self.observe(plug_off(T0 + 800), False, T0, T0 + 800)
        self.observe(plug_off(T0 + 1000), False, T0, T0 + 1000)
        events = self.observe(plug_on(T0 + 600, T0 + 5000), True, T0 + 4000, T0 + 5000)
        self.assertEqual([e.kind for e in events], [RESUMED, CITY_BACK])

    def test_new_session_after_real_stop(self):
        self.start_reserve()
        self.observe(plug_off(T0 + 800), False, T0, T0 + 800)
        self.observe(plug_off(T0 + 1000), False, T0, T0 + 1000)
        # Резерв увімкнули знову — розетка запустилась заново
        events = self.observe(plug_on(T0 + 2000, T0 + 2200), False, T0, T0 + 2200)
        self.assertEqual([e.kind for e in events], [STARTED])
        self.assertEqual(events[0].since, T0 + 2000)
        self.assertIsNone(self.tracker.last_session)

    def test_boiler_on_warned_once(self):
        self.start_reserve()
        events = self.observe(plug_on(T0 + 600, T0 + 800, output=True), False, T0, T0 + 800)
        self.assertEqual([e.kind for e in events], [BOILER_ON])
        self.assertEqual(self.observe(plug_on(T0 + 600, T0 + 900, output=True), False, T0, T0 + 900),
                         [])

    def test_start_with_boiler_on_does_not_warn_twice(self):
        events = self.observe(plug_on(T0 + 600, T0 + 780, output=True), False, T0, T0 + 780)
        self.assertEqual([e.kind for e in events], [STARTED])
        self.assertIs(events[0].boiler_on, True)
        self.assertEqual(self.observe(plug_on(T0 + 600, T0 + 800, output=True), False, T0, T0 + 800),
                         [])

    def test_city_returned_without_switching_is_not_reserve(self):
        # Ви не переходили на резерв: місто повернулося, модуль і розетка ввімкнулися разом
        back = T0 + 3600
        self.assertEqual(self.observe(plug_on(back, back + 30), False, T0, back + 30,
                                      city_online=False), [])  # модуль ще не на зв'язку
        self.assertEqual(self.observe(plug_on(back, back + 120), False, T0, back + 120,
                                      city_online=True), [])   # бот ще не підтвердив місто
        self.assertEqual(self.observe(plug_on(back, back + 240), True, back + 2, back + 240,
                                      city_online=True), [])
        self.assertFalse(self.tracker.on)

    def test_city_online_blocks_start(self):
        self.assertEqual(self.observe(plug_on(T0 + 600, T0 + 1000), False, T0, T0 + 1000,
                                      city_online=True), [])
        self.assertFalse(self.tracker.on)

    def test_module_very_late_after_city_return_is_corrected(self):
        back = T0 + 3600
        self.observe(plug_on(back, back + 30), False, T0, back + 30, city_online=False)
        events = self.observe(plug_on(back, back + 200), False, T0, back + 200, city_online=False)
        self.assertEqual([e.kind for e in events], [STARTED])  # модуль не на зв'язку понад 3 хв
        # Модуль нарешті на зв'язку: увімкнувся разом із розеткою — резерву не було
        events = self.observe(plug_on(back, back + 400), True, back + 3, back + 400, city_online=True)
        self.assertEqual([e.kind for e in events], [NOT_RESERVE])
        self.assertFalse(self.tracker.on)

    def test_real_switch_soon_after_city_return_is_not_cancelled(self):
        # Резерв з T0+600; місто повернулося, і ви одразу перемкнулися на місто
        self.start_reserve()
        back = T0 + 7200
        events = self.observe(plug_on(back + 20, back + 100), True, back, back + 100, city_online=True)
        self.assertEqual([e.kind for e in events], [SWITCHED_TO_CITY])

    def test_state_roundtrip(self):
        self.start_reserve()
        self.observe(plug_on(T0 + 600, T0 + 7300), True, T0 + 7200, T0 + 7300)  # CITY_BACK
        restored = ReserveTracker.from_dict(self.tracker.to_dict())
        self.assertEqual(restored.to_dict(), self.tracker.to_dict())
        # Після перезапуску бота повідомлення не повторюються
        self.assertEqual(restored.observe(plug_on(T0 + 600, T0 + 7400), True, T0 + 7200, None,
                                          T0 + 7400), [])
        self.assertEqual(ReserveTracker.from_dict(None).to_dict(), ReserveTracker().to_dict())


class FormatTest(unittest.TestCase):
    NOW = T0 + 10_000

    def text(self, kind, **kwargs):
        defaults = dict(since=T0 + 600, at=self.NOW, city_has_light=False, city_since=T0)
        defaults.update(kwargs)
        return format_reserve_event(ReserveEvent(kind, **defaults), self.NOW)

    def test_started(self):
        text = self.text(STARTED, boiler_on=False)
        self.assertTrue(text.startswith("🔋 Ви перейшли на резерв"))
        self.assertIn("Резерв увімкнено:", text)
        self.assertIn("Світла від міста немає з", text)
        self.assertIn("♨️ Бойлер вимкнено", text)
        self.assertIn("⚠️ Бойлер увімкнено", self.text(STARTED, boiler_on=True))
        self.assertNotIn("Бойлер", self.text(STARTED, boiler_on=None))

    def test_end_messages_have_period(self):
        for kind, title in ((SWITCHED_TO_CITY, "✅ Ви перейшли з резерву на світло від міста"),
                            (STOPPED, "🪫 Резерв вимкнено")):
            text = self.text(kind, at=T0 + 600 + 8400)
            self.assertTrue(text.startswith(title))
            self.assertIn("Увімкнено:", text)
            self.assertIn("Вимкнено:", text)
            self.assertIn("На резерві: 2 год 20 хв", text)
        self.assertIn("Світла від міста досі немає.", self.text(STOPPED))
        self.assertIn("Світло від міста є.", self.text(STOPPED, city_has_light=True))

    def test_other_messages(self):
        self.assertIn("можна перемикатися", self.text(CITY_BACK, city_has_light=True))
        self.assertIn("Резерв не вимикався", self.text(RESUMED))
        self.assertIn("Бойлер увімкнувся", self.text(BOILER_ON))
        self.assertIn("не переходили на резерв", self.text(NOT_RESERVE))

    def test_no_device_names(self):
        for kind in (STARTED, CITY_BACK, SWITCHED_TO_CITY, STOPPED, RESUMED, BOILER_ON, NOT_RESERVE):
            self.assertNotIn("shelly", self.text(kind, boiler_on=True).lower())

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            self.text("???")

    def test_status_line(self):
        self.assertIsNone(format_reserve_status(ReserveTracker(), self.NOW))
        line = format_reserve_status(ReserveTracker(on=True, since=self.NOW - 3900), self.NOW)
        self.assertIn("🔋 Ви на резерві з", line)
        self.assertIn("1 год 5 хв", line)


class ConstantsTest(unittest.TestCase):
    def test_tolerances(self):
        self.assertGreater(reserve.BOOT_TOLERANCE, reserve.SAME_BOOT_TOLERANCE)


if __name__ == "__main__":
    unittest.main()
