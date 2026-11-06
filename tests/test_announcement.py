import time
import unittest
from datetime import datetime, timezone
from unittest import mock

import announcement
import broadcaster
from tests.test_broadcaster import FakeBot


class AnnouncementConfigTest(unittest.TestCase):
    def test_scheduled_for_today_17_00_kyiv(self):
        # 17:00 за Києвом у листопаді (EET, UTC+2) — це 15:00 UTC
        self.assertEqual(datetime.fromtimestamp(announcement.SEND_AT, timezone.utc),
                         datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc))
        self.assertGreater(announcement.DEADLINE, announcement.SEND_AT)

    def test_text(self):
        self.assertEqual(
            announcement.TEXT,
            "🛠 Бот тимчасово не працює\n\n"
            "Як ви могли помітити, бот зараз не надсилає сповіщень про світло. "
            "Його необхідно доопрацювати, щоб сповіщення були точнішими й без хибних тривог.\n\n"
            "Про відновлення роботи повідомлю окремо.",
        )


@mock.patch.object(broadcaster, "SEND_DELAY", 0)
class AnnouncementJobTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patches = {
            "sent": mock.patch.object(broadcaster.db, "is_announcement_sent", return_value=False),
            "claim": mock.patch.object(broadcaster.db, "claim_announcement", return_value=True),
            "users": mock.patch.object(broadcaster.db, "get_all_subscribed_users", return_value=[1, 2]),
        }
        self.mocks = {name: p.start() for name, p in patches.items()}
        for p in patches.values():
            self.addCleanup(p.stop)
        self.bot = FakeBot()

    async def run_job(self, send_in=0.0, deadline_in=3600.0):
        now = time.time()
        return await broadcaster.announcement_job(self.bot, "test", now + send_in, now + deadline_in, "Привіт")

    async def test_sends_once_at_scheduled_time(self):
        started = time.monotonic()
        self.assertTrue(await self.run_job(send_in=0.2))
        self.assertGreaterEqual(time.monotonic() - started, 0.19)
        self.assertEqual(self.bot.sent, [(1, "Привіт"), (2, "Привіт")])
        self.mocks["claim"].assert_called_once()

    async def test_sends_immediately_if_worker_started_late(self):
        self.assertTrue(await self.run_job(send_in=-600))
        self.assertEqual(len(self.bot.sent), 2)

    async def test_skips_if_already_sent(self):
        self.mocks["sent"].return_value = True
        self.assertFalse(await self.run_job())
        self.assertEqual(self.bot.sent, [])
        self.mocks["claim"].assert_not_called()

    async def test_skips_after_deadline(self):
        self.assertFalse(await self.run_job(send_in=-7200, deadline_in=-60))
        self.assertEqual(self.bot.sent, [])
        self.mocks["claim"].assert_not_called()

    async def test_skips_if_claim_lost(self):
        self.mocks["claim"].return_value = False
        self.assertFalse(await self.run_job())
        self.assertEqual(self.bot.sent, [])

    async def test_db_error_does_not_crash(self):
        self.mocks["sent"].side_effect = RuntimeError("db down")
        self.assertFalse(await self.run_job())


if __name__ == "__main__":
    unittest.main()
