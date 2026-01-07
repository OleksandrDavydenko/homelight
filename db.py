import psycopg2
import os

# Отримуємо URL підключення до бази даних із змінної середовища
DATABASE_URL = os.getenv('DATABASE_URL')

def connect_db():
    """Функція для підключення до бази даних"""
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    return conn

def create_table():
    """Створення таблиці користувачів у базі даних"""
    conn = connect_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
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
    """Додаємо користувача в таблицю"""
    conn = connect_db()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO users (telegram_id, first_name, subscribed)
        VALUES (%s, %s, %s)
        ON CONFLICT (telegram_id) DO NOTHING;
    """, (telegram_id, first_name, subscribed))

    conn.commit()
    cursor.close()
    conn.close()

def update_subscription(telegram_id, subscribed):
    """Оновлюємо підписку користувача"""
    conn = connect_db()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE users
        SET subscribed = %s
        WHERE telegram_id = %s;
    """, (subscribed, telegram_id))

    conn.commit()
    cursor.close()
    conn.close()

def get_user_subscription(telegram_id):
    """Отримуємо підписку користувача"""
    conn = connect_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT subscribed FROM users WHERE telegram_id = %s;
    """, (telegram_id,))
    
    result = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if result:
        return result[0]
    return None
