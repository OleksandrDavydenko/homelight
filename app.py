import os
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ================= НАЛАШТУВАННЯ =================
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ================= РЕАЛЬНА ПЕРЕВІРКА СВІТЛА =================

def check_real_light_status():
    """
    РЕАЛЬНА перевірка наявності світла через розетку Tuya
    Потрібно налаштувати параметри:
    1. DEVICE_ID - ID вашої розетки
    2. DEVICE_IP - IP розетки в мережі  
    3. LOCAL_KEY - ключ розетки
    """
    try:
        # === ПАРАМЕТРИ ДЛЯ ПІДКЛЮЧЕННЯ ===
        DEVICE_ID = "bf3112f230a24fbeb6xvhp"  # Ваш Device ID
        DEVICE_IP = os.getenv("DEVICE_IP")     # IP з Heroku config
        LOCAL_KEY = os.getenv("LOCAL_KEY")     # Local Key з Heroku config
        
        if not all([DEVICE_IP, LOCAL_KEY]):
            return None, "Не налаштовано IP або ключ розетки"
        
        # === ПІДКЛЮЧЕННЯ ДО РОЗЕТКИ ===
        import tinytuya
        
        # Створюємо об'єкт пристрою
        device = tinytuya.OutletDevice(DEVICE_ID, DEVICE_IP, LOCAL_KEY)
        device.set_version(3.3)  # Версія протоколу
        
        # Отримуємо статус
        logger.info(f"Перевіряю розетку {DEVICE_ID} на IP {DEVICE_IP}")
        data = device.status()
        
        # Аналізуємо відповідь
        if 'dps' in data:
            # Шукаємо статус перемикача (зазвичай ключ '1' або 'switch')
            for key, value in data['dps'].items():
                logger.info(f"Ключ: {key}, Значення: {value}")
                if key == '1' or 'switch' in str(key).lower():
                    light_on = bool(value)
                    logger.info(f"Світло: {'УВІМКНЕНО' if light_on else 'ВИМКНЕНО'}")
                    return light_on, None
            
            return None, "Не знайдено статус перемикача у відповіді"
        else:
            return None, "Невірна відповідь від розетки"
            
    except ImportError:
        return None, "Бібліотека tinytuya не встановлена. Додайте до requirements.txt"
    except Exception as e:
        logger.error(f"Помилка перевірки: {e}")
        return None, f"Помилка: {str(e)}"

# ================= TELEGRAM БОТ =================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start - показує одну кнопку"""
    user = update.effective_user
    
    # Тільки одна кнопка
    keyboard = [
        [InlineKeyboardButton("🔍 Перевірити чи є світло", callback_data='check_light')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Привіт, {user.first_name}!\n\n"
        "Натисни кнопку нижче, щоб перевірити наявність світла:",
        reply_markup=reply_markup
    )
    
    logger.info(f"Користувач {user.id} запустив бота")

async def check_light_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник кнопки - реальна перевірка світла"""
    query = update.callback_query
    user = query.from_user
    
    # Показуємо "завантаження"
    await query.answer("🔍 Перевіряю наявність світла...")
    
    # РЕАЛЬНА перевірка світла
    light_status, error = check_real_light_status()
    current_time = datetime.now().strftime("%H:%M:%S")
    
    # Формуємо відповідь
    if error:
        response = (
            f"⚠️ **Не вдалося перевірити світло**\n\n"
            f"Час: {current_time}\n"
            f"Помилка: {error}\n\n"
            f"Переконайтесь, що розетка налаштована."
        )
    else:
        if light_status:
            response = (
                f"✅ **СВІТЛО Є!**\n\n"
                f"Час перевірки: {current_time}\n"
                f"Статус: Розетка увімкнена\n"
                f"Користувач: {user.first_name}\n\n"
                f"⚡ Електропостачання в нормі."
            )
        else:
            response = (
                f"❌ **СВІТЛА НЕМА!**\n\n"
                f"Час перевірки: {current_time}\n"
                f"Статус: Розетка вимкнена\n"
                f"Користувач: {user.first_name}\n\n"
                f"💡 Перевірте автоматичні вимикачі."
            )
    
    # Залишаємо ту саму кнопку для повторної перевірки
    keyboard = [
        [InlineKeyboardButton("🔄 Перевірити знову", callback_data='check_light')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Відправляємо результат
    await query.edit_message_text(
        response,
        reply_markup=reply_markup
    )
    
    logger.info(f"Користувач {user.id} перевірив світло. Статус: {light_status}, Помилка: {error}")

# ================= ОСНОВНА ФУНКЦІЯ =================

def main():
    """Запуск бота"""
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не знайдено!")
        raise ValueError("Додайте BOT_TOKEN у змінні середовища Heroku")
    
    try:
        # Створюємо додаток
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Тільки дві команди:
        # 1. /start - показує кнопку
        # 2. Обробник кнопки
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CallbackQueryHandler(check_light_button, pattern='check_light'))
        
        logger.info("✅ Бот запускається...")
        logger.info("📱 Доступні команди: /start")
        logger.info("🔘 Одна кнопка: 'Перевірити чи є світло'")
        
        # Запускаємо
        application.run_polling()
        
    except Exception as e:
        logger.error(f"❌ Помилка запуску бота: {e}")
        raise

if __name__ == "__main__":
    main()
