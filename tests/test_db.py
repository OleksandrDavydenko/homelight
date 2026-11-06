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
            cur.execute("DROP TABLE IF EXISTS usersLightBot, light_state;")
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
        db.save_light_state(False, 100)
        self.assertEqual(db.get_light_state(), (False, 100))
        db.save_light_state(True, 200)
        self.assertEqual(db.get_light_state(), (True, 200))

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
