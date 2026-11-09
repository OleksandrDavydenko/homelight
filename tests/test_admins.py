import unittest
from unittest import mock

import admins


class AdminsStorageTest(unittest.TestCase):
    def test_load(self):
        with mock.patch.object(admins.db, "get_setting", return_value="111, 222,111,bad") as get:
            self.assertEqual(admins.load_ids(), [111, 222])
        get.assert_called_once_with(admins.SETTING_KEY)
        with mock.patch.object(admins.db, "get_setting", return_value=None):
            self.assertEqual(admins.load_ids(), [])

    def test_save(self):
        with mock.patch.object(admins.db, "set_setting") as set_setting:
            admins.save_ids([111, 222, 111])
            admins.save_ids([])
        self.assertEqual(set_setting.call_args_list,
                         [mock.call(admins.SETTING_KEY, "111,222"), mock.call(admins.SETTING_KEY, "")])

    def test_recipients(self):
        with mock.patch.object(admins.config, "ADMIN_CHAT_ID", 777), \
                mock.patch.object(admins.config, "INTERNET_ALERT_CHAT_IDS", [222]):
            self.assertEqual(admins.recipients([111, 222, 777]), [777, 222, 111])
        with mock.patch.object(admins.config, "ADMIN_CHAT_ID", None), \
                mock.patch.object(admins.config, "INTERNET_ALERT_CHAT_IDS", []):
            self.assertEqual(admins.recipients([]), [])


if __name__ == "__main__":
    unittest.main()
