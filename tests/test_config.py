import unittest
from unittest import mock

import config


class ParseChatIdsTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(config.parse_chat_ids(None), ([], []))
        self.assertEqual(config.parse_chat_ids(""), ([], []))
        self.assertEqual(config.parse_chat_ids("123"), ([123], []))
        self.assertEqual(config.parse_chat_ids(" 123 , 456,, ;789 "), ([123, 456, 789], []))
        self.assertEqual(config.parse_chat_ids("123, abc, 4.5"), ([123], ["abc", "4.5"]))


class InternetAlertChatIdsTest(unittest.TestCase):
    def ids(self, admin, extra):
        with mock.patch.object(config, "ADMIN_CHAT_ID", admin), \
                mock.patch.object(config, "INTERNET_ALERT_CHAT_IDS", extra):
            return config.internet_alert_chat_ids()

    def test_owner_and_extra_admins_without_duplicates(self):
        self.assertEqual(self.ids(777, [888, 777, 999]), [777, 888, 999])

    def test_only_owner(self):
        self.assertEqual(self.ids(777, []), [777])

    def test_only_extra_admins(self):
        self.assertEqual(self.ids(None, [888]), [888])

    def test_nobody(self):
        self.assertEqual(self.ids(None, []), [])


class ValidateTest(unittest.TestCase):
    def required(self):
        return mock.patch.object(config, "_REQUIRED", {"BOT_TOKEN": "x"})

    def test_invalid_internet_alert_ids_stop_start(self):
        with self.required(), mock.patch.object(config, "_INVALID_INTERNET_ALERT_IDS", ["abc"]):
            with self.assertRaises(SystemExit) as error:
                config.validate()
        self.assertIn("INTERNET_ALERT_CHAT_IDS", str(error.exception))
        self.assertIn("abc", str(error.exception))

    def test_valid(self):
        with self.required(), mock.patch.object(config, "_INVALID_INTERNET_ALERT_IDS", []):
            config.validate()


if __name__ == "__main__":
    unittest.main()
