"""Інтеграційні тести SQL. Потрібна справжня PostgreSQL:

    TEST_DATABASE_URL=postgresql://user@localhost/homelight_test DB_SSLMODE=disable \
        python -m unittest tests.test_db

Тести видаляють таблиці, тому назва бази повинна містити «test».
"""
import contextlib
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import psycopg2

import db

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")


@unittest.skipUnless("test" in TEST_DATABASE_URL, "TEST_DATABASE_URL (з 'test' у назві) не задано")
class DbTest(unittest.TestCase):
    def setUp(self):
        self._patches = [
            mock.patch.object(db, "DATABASE_URL", TEST_DATABASE_URL),
            mock.patch.object(db, "DB_SSLMODE", os.getenv("DB_SSLMODE", "disable")),
            mock.patch.object(db, "_pool", None),
        ]
        for p in self._patches:
            p.start()
        with contextlib.closing(self._connect()) as conn, conn, conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS usersLightBot, light_state, bot_settings;")
        db.init_db()

    def _connect(self):
        return psycopg2.connect(TEST_DATABASE_URL, sslmode=db.DB_SSLMODE)

    def tearDown(self):
        if db._pool is not None:
            db._pool.closeall()
        for p in reversed(self._patches):
            p.stop()

    def test_init_db_is_idempotent(self):
        db.init_db()

    def test_new_user_is_not_subscribed(self):
        db.ensure_user(1, "A")
        self.assertFalse(db.get_user_subscription(1))
        self.assertFalse(db.get_user_subscription(999))

    def test_start_does_not_reset_subscription(self):
        db.set_subscription(1, "A", True)
        db.ensure_user(1, "A2")
        self.assertTrue(db.get_user_subscription(1))
        self.assertEqual(db.get_all_subscribed_users(), [1])

    def test_ensure_user_updates_name(self):
        db.ensure_user(1, "Old")
        db.ensure_user(1, "New")

        def name(cursor):
            cursor.execute("SELECT first_name FROM usersLightBot WHERE telegram_id = 1")
            return cursor.fetchone()[0]
        self.assertEqual(db._run(name), "New")

    def test_subscribe_and_unsubscribe(self):
        db.set_subscription(1, "A", True)
        db.set_subscription(2, "B", True)
        db.set_subscription(1, "A", False)
        self.assertEqual(db.get_all_subscribed_users(), [2])
        db.update_subscription(2, False)
        self.assertEqual(db.get_all_subscribed_users(), [])

    def test_update_subscription_of_unknown_user_is_noop(self):
        db.update_subscription(12345, False)
        self.assertFalse(db.get_user_subscription(12345))

    def test_light_state_roundtrip(self):
        self.assertIsNone(db.get_light_state())
        offline = {"has_light": False, "since": 100, "offline": True, "light_since": 50}
        db.save_light_state(offline)
        self.assertEqual(db.get_light_state(), offline)
        light = {"has_light": True, "since": 200, "offline": False, "light_since": None}
        db.save_light_state(light)
        self.assertEqual(db.get_light_state(), light)

    def test_migrates_light_state_table_from_previous_version(self):
        with contextlib.closing(self._connect()) as conn, conn, conn.cursor() as cur:
            cur.execute("DROP TABLE light_state;")
            cur.execute("""CREATE TABLE light_state (
                id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                has_light BOOLEAN NOT NULL, since BIGINT NOT NULL);""")
            cur.execute("INSERT INTO light_state (id, has_light, since) VALUES (1, TRUE, 77);")
        db.init_db()
        self.assertEqual(db.get_light_state(),
                         {"has_light": True, "since": 77, "offline": False, "light_since": None})

    def test_recent_users_and_names(self):
        for telegram_id, name in ((111, "Олена"), (222, "Петро"), (333, "Іван")):
            db.ensure_user(telegram_id, name)
        db.remember_user(333, "Іван", "ivan_k")
        self.assertEqual(db.get_recent_users(),
                         [(333, "Іван", "ivan_k"), (222, "Петро", None), (111, "Олена", None)])
        self.assertEqual(db.get_recent_users(limit=2), [(333, "Іван", "ivan_k"), (222, "Петро", None)])
        self.assertEqual(db.get_recent_users(exclude=[333, 111]), [(222, "Петро", None)])
        self.assertEqual(db.get_recent_users(limit=2, offset=2), [(111, "Олена", None)])
        self.assertEqual(db.count_users(), 3)
        self.assertEqual(db.count_users(exclude=[111]), 2)
        self.assertEqual(db.get_users([111, 333, 999]), {111: ("Олена", None), 333: ("Іван", "ivan_k")})
        self.assertEqual(db.get_users([]), {})

    def test_filter_subscribed(self):
        db.set_subscription(1, "A", True)
        db.set_subscription(2, "B", False)
        db.set_subscription(3, "C", True)
        self.assertEqual(db.filter_subscribed([1, 2, 3, 4]), {1, 3})
        self.assertEqual(db.filter_subscribed([]), set())

    def test_remember_user_keeps_subscription(self):
        db.set_subscription(1, "A", True)
        db.remember_user(1, "Анна", "anna")
        self.assertTrue(db.get_user_subscription(1))
        self.assertEqual(db.get_users([1]), {1: ("Анна", "anna")})
        db.remember_user(2, "Борис", None)  # новий користувач — без підписки
        self.assertFalse(db.get_user_subscription(2))
        self.assertEqual(db.get_users([2]), {2: ("Борис", None)})

    def test_migrates_users_table_without_username(self):
        with contextlib.closing(self._connect()) as conn, conn, conn.cursor() as cur:
            cur.execute("DROP TABLE usersLightBot;")
            cur.execute("""CREATE TABLE usersLightBot (
                id SERIAL PRIMARY KEY, telegram_id BIGINT UNIQUE NOT NULL,
                first_name VARCHAR(100), subscribed BOOLEAN DEFAULT FALSE);""")
            cur.execute("INSERT INTO usersLightBot (telegram_id, first_name, subscribed) VALUES (5, 'Олег', TRUE);")
        db.init_db()
        self.assertEqual(db.get_users([5]), {5: ("Олег", None)})
        self.assertTrue(db.get_user_subscription(5))

    def test_settings_roundtrip(self):
        self.assertIsNone(db.get_setting("test_mode"))
        db.set_setting("test_mode", "1")
        self.assertEqual(db.get_setting("test_mode"), "1")
        db.set_setting("test_mode", "0")
        self.assertEqual(db.get_setting("test_mode"), "0")

    def test_init_db_drops_old_announcements_table(self):
        with contextlib.closing(self._connect()) as conn, conn, conn.cursor() as cur:
            cur.execute("CREATE TABLE announcements (id TEXT PRIMARY KEY, sent_at BIGINT NOT NULL);")
        db.init_db()
        with contextlib.closing(self._connect()) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('announcements');")
            self.assertIsNone(cur.fetchone()[0])

    def test_more_concurrent_queries_than_pool_size(self):
        db.set_subscription(1, "A", True)
        workers = db.POOL_MAX_CONNECTIONS * 4
        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(lambda _: db.get_user_subscription(1), range(workers * 3)))
        self.assertTrue(all(results))

    def test_reconnects_after_server_drops_connection(self):
        pid = db._run(lambda c: (c.execute("SELECT pg_backend_pid()"), c.fetchone()[0])[1])
        with contextlib.closing(self._connect()) as conn, conn, conn.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
        db.set_subscription(1, "A", True)
        self.assertTrue(db.get_user_subscription(1))


if __name__ == "__main__":
    unittest.main()
