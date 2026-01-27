import psycopg2
import os
import logging

# Отримуємо URL підключення до бази даних із змінної середовища
DATABASE_URL = os.getenv('DATABASE_URL')

logger = logging.getLogger(__name__)


def connect_db():
    """Функція для підключення до бази даних. Перевіряє наявність DATABASE_URL."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set in environment")
    return psycopg2.connect(DATABASE_URL, sslmode='require')


def create_table():
    """Створення таблиці користувачів у базі даних"""
    try:
        with connect_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS usersLightBot (
                        id SERIAL PRIMARY KEY,
                        telegram_id BIGINT UNIQUE NOT NULL,
                        first_name VARCHAR(100),
                        subscribed BOOLEAN DEFAULT FALSE
                    );
                """)
            conn.commit()
    except Exception as e:
        logger.exception("Error creating table: %s", e)


def add_user(telegram_id, first_name, subscribed=False):
    """Додаємо користувача в таблицю або оновлюємо його підписку"""
    logger.info("add_user called: telegram_id=%s subscribed=%s", telegram_id, subscribed)
    try:
        with connect_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO usersLightBot (telegram_id, first_name, subscribed)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (telegram_id) DO UPDATE SET subscribed = %s, first_name = %s;
                """, (telegram_id, first_name, subscribed, subscribed, first_name))
            conn.commit()
        logger.info("add_user succeeded: telegram_id=%s subscribed=%s", telegram_id, subscribed)
    except Exception as e:
        logger.exception("add_user failed for telegram_id=%s: %s", telegram_id, e)


def update_subscription(telegram_id, subscribed):
    """Оновлюємо підписку користувача"""
    logger.info("update_subscription called: telegram_id=%s -> subscribed=%s", telegram_id, subscribed)
    try:
        with connect_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE usersLightBot
                    SET subscribed = %s
                    WHERE telegram_id = %s;
                """, (subscribed, telegram_id))
            conn.commit()
        logger.info("update_subscription succeeded: telegram_id=%s -> subscribed=%s", telegram_id, subscribed)
    except Exception as e:
        logger.exception("update_subscription failed for telegram_id=%s: %s", telegram_id, e)


def get_user_subscription(telegram_id):
    """Отримуємо підписку користувача"""
    logger.debug("get_user_subscription called for %s", telegram_id)
    try:
        with connect_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT subscribed FROM usersLightBot WHERE telegram_id = %s;
                """, (telegram_id,))
                result = cursor.fetchone()
        if result:
            logger.debug("get_user_subscription result for %s: %s", telegram_id, result[0])
            return result[0]
        logger.debug("get_user_subscription result for %s: None", telegram_id)
        return None
    except Exception as e:
        logger.exception("get_user_subscription failed for %s: %s", telegram_id, e)
        return None


def get_all_subscribed_users():
    """Повертає список telegram_id всіх користувачів зі значенням subscribed = TRUE"""
    logger.debug("get_all_subscribed_users called")
    try:
        with connect_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT telegram_id FROM usersLightBot WHERE subscribed = TRUE;
                """)
                rows = cursor.fetchall()
        user_ids = [row[0] for row in rows]
        logger.debug("get_all_subscribed_users found %d users", len(user_ids))
        return user_ids
    except Exception as e:
        logger.exception("get_all_subscribed_users failed: %s", e)
        return []
