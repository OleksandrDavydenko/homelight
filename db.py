import psycopg2
import os
import logging

# Отримуємо URL підключення до бази даних із змінної середовища
DATABASE_URL = os.getenv('DATABASE_URL')

logger = logging.getLogger(__name__)

def connect_db():
    """Функція для підключення до бази даних"""
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    return conn

def create_table():
    """Створення таблиці користувачів у базі даних"""
    conn = connect_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usersLightBot (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE NOT NULL,
            first_name VARCHAR(100),
            subscribed BOOLEAN DEFAULT FALSE
        );
    """)
    conn.commit()
    cursor.close()
    conn.close()

def add_user(telegram_id, first_name, subscribed=False):
    """Додаємо користувача в таблицю або оновлюємо його підписку"""
    try:
        logger.info("add_user called: telegram_id=%s subscribed=%s", telegram_id, subscribed)
        conn = connect_db()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO usersLightBot (telegram_id, first_name, subscribed)
            VALUES (%s, %s, %s)
            ON CONFLICT (telegram_id) DO UPDATE SET subscribed = %s, first_name = %s;
        """, (telegram_id, first_name, subscribed, subscribed, first_name))

        conn.commit()
        cursor.close()
        conn.close()
        logger.info("add_user succeeded: telegram_id=%s subscribed=%s", telegram_id, subscribed)
    except Exception as e:
        logger.exception("add_user failed for telegram_id=%s: %s", telegram_id, e)
        try:
            cursor.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

def update_subscription(telegram_id, subscribed):
    """Оновлюємо підписку користувача"""
    try:
        logger.info("update_subscription called: telegram_id=%s -> subscribed=%s", telegram_id, subscribed)
        conn = connect_db()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE usersLightBot
            SET subscribed = %s
            WHERE telegram_id = %s;
        """, (subscribed, telegram_id))

        conn.commit()
        cursor.close()
        conn.close()
        logger.info("update_subscription succeeded: telegram_id=%s -> subscribed=%s", telegram_id, subscribed)
    except Exception as e:
        logger.exception("update_subscription failed for telegram_id=%s: %s", telegram_id, e)
        try:
            cursor.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

def get_user_subscription(telegram_id):
    """Отримуємо підписку користувача"""
    try:
        logger.debug("get_user_subscription called for %s", telegram_id)
        conn = connect_db()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT subscribed FROM usersLightBot WHERE telegram_id = %s;
        """, (telegram_id,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        if result:
            logger.debug("get_user_subscription result for %s: %s", telegram_id, result[0])
            return result[0]
        logger.debug("get_user_subscription result for %s: None", telegram_id)
        return None
    except Exception as e:
        logger.exception("get_user_subscription failed for %s: %s", telegram_id, e)
        try:
            cursor.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        return None
