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
                subscribed BOOLEAN DEFAULT FALSE,
                username VARCHAR(64)
            );
        """)
        # Для таблиці, створеної попередньою версією без username
        cursor.execute("ALTER TABLE usersLightBot ADD COLUMN IF NOT EXISTS username VARCHAR(64);")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS light_state (
                id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                has_light BOOLEAN NOT NULL,
                since BIGINT NOT NULL,
                offline BOOLEAN NOT NULL DEFAULT FALSE,
                light_since BIGINT
            );
        """)
        # Для таблиці, створеної попередньою версією без цих колонок
        cursor.execute("ALTER TABLE light_state ADD COLUMN IF NOT EXISTS "
                       "offline BOOLEAN NOT NULL DEFAULT FALSE;")
        cursor.execute("ALTER TABLE light_state ADD COLUMN IF NOT EXISTS light_since BIGINT;")
        # Таблиця разового оголошення від 6.11.2026 більше не потрібна
        cursor.execute("DROP TABLE IF EXISTS announcements;")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
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


def remember_user(telegram_id: int, first_name: str, username: str | None) -> None:
    """Запам'ятовує ім'я та @username (без «@») користувача. Підписку не змінює."""
    _run(lambda cursor: cursor.execute("""
        INSERT INTO usersLightBot (telegram_id, first_name, username, subscribed)
        VALUES (%s, %s, %s, FALSE)
        ON CONFLICT (telegram_id) DO UPDATE
            SET first_name = EXCLUDED.first_name, username = EXCLUDED.username;
    """, (telegram_id, (first_name or "")[:100], (username or None) and username[:64])))


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


def get_light_state() -> dict | None:
    """Підтверджений стан світла (див. StateTracker.to_dict) або None, якщо ще не збережено."""
    def query(cursor):
        cursor.execute(
            "SELECT has_light, since, offline, light_since FROM light_state WHERE id = 1;"
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {"has_light": bool(row[0]), "since": int(row[1]),
                "offline": bool(row[2]), "light_since": row[3]}
    return _run(query)


def save_light_state(state: dict) -> None:
    """Зберігає підтверджений стан світла (StateTracker.to_dict)."""
    params = (state["has_light"], state["since"], bool(state.get("offline")), state.get("light_since"))
    _run(lambda cursor: cursor.execute("""
        INSERT INTO light_state (id, has_light, since, offline, light_since)
        VALUES (1, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE
            SET has_light = EXCLUDED.has_light, since = EXCLUDED.since,
                offline = EXCLUDED.offline, light_since = EXCLUDED.light_since;
    """, params))
    logger.info("Стан світла збережено: %s", state)


def get_setting(key: str) -> str | None:
    """Значення налаштування бота або None, якщо його не задано."""
    def query(cursor):
        cursor.execute("SELECT value FROM bot_settings WHERE key = %s;", (key,))
        row = cursor.fetchone()
        return row[0] if row else None
    return _run(query)


def set_setting(key: str, value: str) -> None:
    """Зберігає налаштування бота."""
    _run(lambda cursor: cursor.execute("""
        INSERT INTO bot_settings (key, value) VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
    """, (key, value)))
    logger.info("Налаштування %s = %s", key, value)


def get_recent_users(limit: int = 10, exclude=(), offset: int = 0) -> list[tuple[int, str, str | None]]:
    """Користувачі, які натискали /start: [(telegram_id, first_name, username)], новіші першими.

    offset — скільки пропустити (для гортання сторінками).
    """
    exclude = list(exclude) or [0]

    def query(cursor):
        cursor.execute("""
            SELECT telegram_id, COALESCE(first_name, ''), username FROM usersLightBot
            WHERE NOT (telegram_id = ANY(%s))
            ORDER BY id DESC LIMIT %s OFFSET %s;
        """, (exclude, limit, offset))
        return [(int(row[0]), row[1], row[2]) for row in cursor.fetchall()]
    return _run(query)


def count_users(exclude=()) -> int:
    """Скільки користувачів натискали /start, не рахуючи exclude."""
    exclude = list(exclude) or [0]

    def query(cursor):
        cursor.execute("SELECT COUNT(*) FROM usersLightBot WHERE NOT (telegram_id = ANY(%s));",
                       (exclude,))
        return int(cursor.fetchone()[0])
    return _run(query)


def get_users(ids) -> dict[int, tuple[str, str | None]]:
    """{telegram_id: (first_name, username)} (кого немає в БД — немає й у словнику)."""
    ids = list(ids)
    if not ids:
        return {}

    def query(cursor):
        cursor.execute(
            "SELECT telegram_id, COALESCE(first_name, ''), username FROM usersLightBot "
            "WHERE telegram_id = ANY(%s);",
            (ids,),
        )
        return {int(row[0]): (row[1], row[2]) for row in cursor.fetchall()}
    return _run(query)
