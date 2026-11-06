"""Робота з PostgreSQL: користувачі та підтверджений стан світла.

Функції синхронні (psycopg2), тому з асинхронного коду їх викликають через
asyncio.to_thread. Помилки не ковтаються: їх обробляє той, хто викликає.
"""
import contextlib
import logging
import os
import threading

import psycopg2
import psycopg2.pool

from config import DATABASE_URL

logger = logging.getLogger(__name__)

DB_SSLMODE = os.getenv("DB_SSLMODE", "require")
POOL_MAX_CONNECTIONS = 5

_CONNECTION_ERRORS = (psycopg2.OperationalError, psycopg2.InterfaceError)

_pool = None
_pool_lock = threading.Lock()
# ThreadedConnectionPool не чекає на вільне з'єднання, а одразу кидає PoolError,
# тому обмежуємо кількість одночасних запитів семафором — зайві чекають у черзі
_slots = threading.BoundedSemaphore(POOL_MAX_CONNECTIONS)


def _get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            if not DATABASE_URL:
                raise RuntimeError("DATABASE_URL is not set in environment")
            _pool = psycopg2.pool.ThreadedConnectionPool(
                1, POOL_MAX_CONNECTIONS, DATABASE_URL, sslmode=DB_SSLMODE
            )
        return _pool


@contextlib.contextmanager
def _connection():
    """Бере з'єднання з пулу, комітить або відкочує транзакцію й повертає його."""
    pool = _get_pool()
    with _slots:
        conn = pool.getconn()
        broken = False
        try:
            yield conn
            conn.commit()
        except _CONNECTION_ERRORS:
            broken = True
            raise
        except BaseException:
            try:
                conn.rollback()
            except _CONNECTION_ERRORS:
                broken = True
            raise
        finally:
            pool.putconn(conn, close=broken or bool(conn.closed))


def _run(fn):
    """Виконує fn(cursor) в окремій транзакції.

    Якщо з'єднання з пулу виявилося розірваним (сервер закриває неактивні
    з'єднання), запит повторюється один раз на новому з'єднанні. Усі запити
    модуля ідемпотентні, тож повтор безпечний.
    """
    for attempt in (1, 2):
        try:
            with _connection() as conn, conn.cursor() as cursor:
                return fn(cursor)
        except _CONNECTION_ERRORS:
            if attempt == 2:
                raise
            logger.warning("З'єднання з БД розірване, повторюю запит")


def init_db() -> None:
    """Створює таблиці, якщо їх ще немає."""
    def query(cursor):
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usersLightBot (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                first_name VARCHAR(100),
                subscribed BOOLEAN DEFAULT FALSE
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS light_state (
                id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                has_light BOOLEAN NOT NULL,
                since BIGINT NOT NULL
            );
        """)
    _run(query)
    logger.info("Таблиці БД готові")


def ensure_user(telegram_id: int, first_name: str) -> None:
    """Додає користувача, якщо його немає. Підписку існуючого користувача не змінює."""
    _run(lambda cursor: cursor.execute("""
        INSERT INTO usersLightBot (telegram_id, first_name, subscribed)
        VALUES (%s, %s, FALSE)
        ON CONFLICT (telegram_id) DO UPDATE SET first_name = EXCLUDED.first_name;
    """, (telegram_id, first_name[:100])))


def set_subscription(telegram_id: int, first_name: str, subscribed: bool) -> None:
    """Додає користувача (якщо треба) і встановлює підписку."""
    _run(lambda cursor: cursor.execute("""
        INSERT INTO usersLightBot (telegram_id, first_name, subscribed)
        VALUES (%s, %s, %s)
        ON CONFLICT (telegram_id) DO UPDATE
            SET subscribed = EXCLUDED.subscribed, first_name = EXCLUDED.first_name;
    """, (telegram_id, first_name[:100], subscribed)))
    logger.info("Підписка %s -> %s", telegram_id, subscribed)


def update_subscription(telegram_id: int, subscribed: bool) -> None:
    """Змінює підписку існуючого користувача (наприклад, коли він заблокував бота)."""
    _run(lambda cursor: cursor.execute(
        "UPDATE usersLightBot SET subscribed = %s WHERE telegram_id = %s;",
        (subscribed, telegram_id),
    ))
    logger.info("Підписка %s -> %s", telegram_id, subscribed)


def get_user_subscription(telegram_id: int) -> bool:
    """True, якщо користувач підписаний; False, якщо ні або його немає в БД."""
    def query(cursor):
        cursor.execute(
            "SELECT subscribed FROM usersLightBot WHERE telegram_id = %s;",
            (telegram_id,),
        )
        row = cursor.fetchone()
        return bool(row and row[0])
    return _run(query)


def get_all_subscribed_users() -> list[int]:
    """telegram_id всіх підписаних користувачів."""
    def query(cursor):
        cursor.execute("SELECT telegram_id FROM usersLightBot WHERE subscribed = TRUE;")
        return [row[0] for row in cursor.fetchall()]
    return _run(query)


def get_light_state() -> tuple[bool, int] | None:
    """Підтверджений стан світла: (has_light, since) або None, якщо ще не збережено."""
    def query(cursor):
        cursor.execute("SELECT has_light, since FROM light_state WHERE id = 1;")
        row = cursor.fetchone()
        return (bool(row[0]), int(row[1])) if row else None
    return _run(query)


def save_light_state(has_light: bool, since: int) -> None:
    """Зберігає підтверджений стан світла."""
    _run(lambda cursor: cursor.execute("""
        INSERT INTO light_state (id, has_light, since) VALUES (1, %s, %s)
        ON CONFLICT (id) DO UPDATE
            SET has_light = EXCLUDED.has_light, since = EXCLUDED.since;
    """, (has_light, since)))
    logger.info("Стан світла збережено: has_light=%s since=%s", has_light, since)
