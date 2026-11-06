import unittest
from datetime import datetime, timezone
from unittest import mock
from zoneinfo import ZoneInfo

import requests

import light_checker
from light_checker import (
    LightChecker,
    Reading,
    ShellyApiError,
    StateTracker,
    format_alert,
    format_duration,
    format_moment,
    format_status_message,
    StateChange,
)

MAC = "48f6eeb6ec00"
NOW = 1_800_000_000


def device(voltage=230.5, freq=50.0, **extra):
    status = {"switch:0": {"id": 0, "voltage": voltage, "freq": freq}}
    status.update(extra)
    return status


def payload(devices):
    return {"isok": True, "data": {"devices_status": devices}}


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class FakeSession:
    """Повертає (або кидає) заздалегідь задані відповіді по черзі."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def post(self, url, data=None, timeout=None):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_checker(*responses, mac=MAC):
    session = FakeSession(*responses)
    return LightChecker(auth_key="key", base_url="https://shelly.test", target_mac=mac,
                        session=session), session


class ParseReadingTest(unittest.TestCase):
    def setUp(self):
        self.checker, _ = make_checker()

    def parse(self, devices):
        return self.checker.parse_reading({"devices_status": devices}, NOW)

    def test_light_present(self):
        r = self.parse({MAC: device(230.5, 49.9)})
        self.assertTrue(r.has_light)
        self.assertEqual(r.voltage, 230.5)
        self.assertEqual(r.frequency, 49.9)
        self.assertTrue(r.online)

    def test_mac_is_case_insensitive(self):
        self.assertTrue(self.parse({MAC.upper(): device()}).has_light)

    def test_low_voltage_means_no_light(self):
        r = self.parse({MAC: device(voltage=40)})
        self.assertIs(r.has_light, False)
        self.assertEqual(r.voltage, 40)

    def test_device_missing_means_offline(self):
        r = self.parse({"other": device()})
        self.assertIs(r.has_light, False)
        self.assertIs(r.online, False)

    def test_no_devices_means_offline(self):
        self.assertIs(self.checker.parse_reading({}, NOW).has_light, False)
        self.assertIs(self.parse({}).has_light, False)

    def test_dev_info_offline(self):
        r = self.parse({MAC: device(_dev_info={"online": False})})
        self.assertIs(r.has_light, False)
        self.assertIs(r.online, False)

    def test_missing_voltage_is_unknown(self):
        r = self.parse({MAC: {"sys": {}}})
        self.assertIsNone(r.has_light)
        self.assertIsNotNone(r.error)

    def test_first_device_when_no_target_mac(self):
        checker, _ = make_checker(mac=None)
        r = checker.parse_reading({"devices_status": {"abc": device(220)}}, NOW)
        self.assertTrue(r.has_light)

    def test_updated_is_parsed_as_utc(self):
        updated = datetime.fromtimestamp(NOW - 300, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        r = self.parse({MAC: device(_updated=updated)})
        self.assertEqual(r.device_time, NOW - 300)

    def test_updated_in_future_is_ignored(self):
        updated = datetime.fromtimestamp(NOW + 3600, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.assertIsNone(self.parse({MAC: device(_updated=updated)}).device_time)


@mock.patch.object(light_checker.time, "sleep")
class FetchTest(unittest.TestCase):
    def test_retries_after_429(self, sleep):
        checker, session = make_checker(FakeResponse(429), FakeResponse(200, payload({MAC: device()})))
        data = checker.fetch_all_status(attempts=3)
        self.assertIn(MAC, data["devices_status"])
        self.assertEqual(session.calls, 2)
        sleep.assert_called_once()

    def test_retries_after_max_req(self, sleep):
        limited = {"isok": False, "errors": {"max_req": "Request limit reached!"}}
        checker, session = make_checker(FakeResponse(200, limited),
                                        FakeResponse(200, payload({MAC: device()})))
        checker.fetch_all_status(attempts=3)
        self.assertEqual(session.calls, 2)

    def test_retries_after_http_500(self, sleep):
        checker, session = make_checker(FakeResponse(500), FakeResponse(200, payload({MAC: device()})))
        checker.fetch_all_status(attempts=3)
        self.assertEqual(session.calls, 2)

    def test_other_api_error_is_not_retried(self, sleep):
        bad = {"isok": False, "errors": {"wrong_auth_key": "bad key"}}
        checker, session = make_checker(FakeResponse(200, bad))
        with self.assertRaises(ShellyApiError):
            checker.fetch_all_status(attempts=3)
        self.assertEqual(session.calls, 1)

    def test_network_error_gives_unknown_reading_not_outage(self, sleep):
        errors = [requests.ConnectionError("boom") for _ in range(3)]
        checker, session = make_checker(*errors)
        r = checker.get_reading(attempts=3)
        self.assertIsNone(r.has_light)
        self.assertIn("boom", r.error)
        self.assertEqual(session.calls, 3)


class CacheTest(unittest.TestCase):
    def test_cached_reading_is_reused(self):
        checker, session = make_checker(FakeResponse(200, payload({MAC: device()})),
                                        FakeResponse(200, payload({MAC: device()})))
        first = checker.get_reading(max_age=30)
        second = checker.get_reading(max_age=30)
        self.assertIs(first, second)
        self.assertEqual(session.calls, 1)
        checker.get_reading(max_age=0)
        self.assertEqual(session.calls, 2)

    def test_unknown_reading_is_not_cached(self):
        bad = {"isok": False, "errors": {"x": "y"}}
        checker, session = make_checker(FakeResponse(200, bad), FakeResponse(200, payload({MAC: device()})))
        self.assertIsNone(checker.get_reading(max_age=30, attempts=1).has_light)
        self.assertTrue(checker.get_reading(max_age=30, attempts=1).has_light)
        self.assertEqual(session.calls, 2)


class StateTrackerTest(unittest.TestCase):
    def test_first_reading_initialises_without_alert(self):
        t = StateTracker()
        change = t.observe(True, 100)
        self.assertTrue(change.initial)
        self.assertEqual((t.has_light, t.since), (True, 100))

    def test_unknown_readings_are_ignored(self):
        t = StateTracker()
        self.assertIsNone(t.observe(None, 100))
        self.assertFalse(t.known)

    def test_change_needs_two_confirmations(self):
        t = StateTracker(True, 1000)
        self.assertIsNone(t.observe(True, 1090))
        self.assertIsNone(t.observe(False, 1180))
        self.assertTrue(t.pending)
        self.assertTrue(t.has_light)
        change = t.observe(False, 1200)
        self.assertEqual(change, StateChange(has_light=False, since=1180, previous_duration=180))
        self.assertFalse(t.pending)
        self.assertEqual((t.has_light, t.since), (False, 1180))

    def test_single_glitch_is_discarded(self):
        t = StateTracker(True, 1000)
        t.observe(False, 1100)
        self.assertIsNone(t.observe(True, 1120))
        self.assertFalse(t.pending)
        self.assertIsNone(t.observe(False, 1210))  # лічильник почався заново
        self.assertEqual(t.has_light, True)

    def test_unknown_between_confirmations_keeps_pending(self):
        t = StateTracker(False, 1000)
        t.observe(True, 1100)
        self.assertIsNone(t.observe(None, 1120))
        change = t.observe(True, 1140)
        self.assertEqual(change.previous_duration, 100)
        self.assertTrue(change.has_light)

    def test_single_confirmation_mode(self):
        t = StateTracker(True, 0, confirmations=1)
        self.assertEqual(t.observe(False, 50).since, 50)


class FormattingTest(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(format_duration(5), "5 сек")
        self.assertEqual(format_duration(125), "2 хв 5 сек")
        self.assertEqual(format_duration(3 * 3600 + 120), "3 год 2 хв")
        self.assertEqual(format_duration(2 * 86400 + 3600), "2 дн 1 год")
        self.assertEqual(format_duration(-10), "0 сек")

    def test_format_moment(self):
        tz = ZoneInfo(light_checker.TIMEZONE)
        now = int(datetime(2026, 11, 6, 12, 0, tzinfo=tz).timestamp())
        self.assertEqual(format_moment(now - 3600, now), "сьогодні о 11:00")
        self.assertEqual(format_moment(now - 86400, now), "вчора о 12:00")
        self.assertEqual(format_moment(now - 3 * 86400, now), "03.11 о 12:00")

    def test_status_light_confirmed(self):
        r = Reading(has_light=True, fetched_at=NOW, voltage=230.46, frequency=50.0)
        text = format_status_message(r, StateTracker(True, NOW - 7200), NOW)
        self.assertIn("✅ СВІТЛО Є", text)
        self.assertIn("230.5 В", text)
        self.assertIn("Світло є вже: 2 год 0 хв", text)
        self.assertIn("(щойно)", text)

    def test_status_no_duration_when_reading_differs_from_confirmed(self):
        r = Reading(has_light=False, fetched_at=NOW, online=False)
        text = format_status_message(r, StateTracker(True, NOW - 7200), NOW)
        self.assertIn("❌ СВІТЛА НЕМАЄ", text)
        self.assertNotIn("Час відключення", text)

    def test_status_outage_duration(self):
        r = Reading(has_light=False, fetched_at=NOW, online=False)
        text = format_status_message(r, StateTracker(False, NOW - 600), NOW)
        self.assertIn("Час відключення: 10 хв 0 сек", text)

    def test_status_unknown(self):
        r = Reading(has_light=None, fetched_at=NOW, error="boom")
        text = format_status_message(r, StateTracker(False, NOW - 600), NOW)
        self.assertIn("Не вдалося отримати дані", text)
        self.assertIn("Останній відомий стан: світла немає", text)
        self.assertNotIn("boom", text)

    def test_alerts(self):
        self.assertEqual(format_alert(StateChange(False, NOW, 100)), "🔴 СВІТЛО ПРОПАЛО!")
        self.assertEqual(format_alert(StateChange(True, NOW, 3700)),
                         "🟢 СВІТЛО ПОВЕРНУЛОСЬ!\n\n⏱️ Час відключення: 1 год 1 хв")
        self.assertEqual(format_alert(StateChange(True, NOW)), "🟢 СВІТЛО ПОВЕРНУЛОСЬ!")


if __name__ == "__main__":
    unittest.main()
