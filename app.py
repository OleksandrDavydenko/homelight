import os
import logging
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Налаштування
BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 5000))

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask app для Heroku
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return jsonify({
        "status": "online",
        "service": "Simple Button Bot",
        "endpoints": ["/", "/health"],
        "message": "🤖 Бот з кнопкою працює на Heroku!"
    })

@flask_app.route('/health')
def health():
    return jsonify({"status": "healthy", "timestamp": "2026-01-06T09:00:00Z"})

# ================ TELEGRAM БОТ ================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start - показує кнопку"""
    user = update.effective_user
    
    # Створюємо клавіатуру з однією кнопкою
    keyboard = [
        [InlineKeyboardButton("👋 Натисни мене!", callback_data='hello_button')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Привіт, {user.first_name}! 👋\n\n"
        "Я бот з однією кнопкою на Heroku!\n\n"
        "👇 Натисни кнопку нижче:",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник натискання кнопки"""
    query = update.callback_query
    user = query.from_user
    
    # Обробляємо натискання кнопки
    if query.data == 'hello_button':
        # Відправляємо повідомлення
        await query.answer()  # Закриваємо "завантаження" на кнопці
        
        # Випадкові повідомлення
        import random
        messages = [
            f"🎉 Вітаю, {user.first_name}! Ти натиснув кнопку!",
            f"👏 Супер, {user.first_name}! Кнопка працює!",
            f"🚀 Ура, {user.first_name}! Ти це зробив!",
            f"💫 Привіт знову, {user.first_name}! Кнопка активована!",
            f"🌟 Чудово, {user.first_name}! Повідомлення отримано!",
            f"🎯 Влучно, {user.first_name}! Ти попав у ціль!",
            f"🌈 Чарівно, {user.first_name}! Магія кнопок працює!",
            f"⚡ Енергійно, {user.first_name}! Ти активував кнопку!",
        ]
        
        # Вибираємо випадкове повідомлення
        message = random.choice(messages)
        
        # Додаємо емодзі реакцію
        reactions = ["😊", "😎", "🤩", "🥳", "🎮", "💥", "✨", "🦄"]
        reaction = random.choice(reactions)
        
        # Надсилаємо повідомлення
        await query.edit_message_text(
            text=f"{message}\n\n"
                 f"🆔 Твій ID: {user.id}\n"
                 f"📊 Кнопка натиснута: 1 раз\n"
                 f"{reaction} Реакція: {reaction}\n\n"
                 "💡 Напиши /start щоб отримати кнопку знову!",
            parse_mode='HTML'
        )
        
        logger.info(f"Користувач {user.id} натиснув кнопку")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    help_text = (
        "🤖 **Бот з однією кнопкою на Heroku**\n\n"
        "🎯 **Що робить бот:**\n"
        "• Показує кнопку 'Привіт'\n"
        "• Відправляє повідомлення при натисканні\n\n"
        "📋 **Команди:**\n"
        "• /start - отримати кнопку\n"
        "• /help - ця довідка\n"
        "• /info - інформація про бота\n\n"
        "🚀 **Технології:**\n"
        "• Python + python-telegram-bot\n"
        "• Heroku для хостингу\n"
        "• Flask веб-сервер"
    )
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /info"""
    info_text = (
        "📊 **Інформація про бота:**\n\n"
        "🔧 **Технічні деталі:**\n"
        "• Версія: 1.0\n"
        "• Хостинг: Heroku\n"
        "• Платформа: Python 3.11\n"
        "• Бібліотека: python-telegram-bot\n\n"
        "🎯 **Мета проекту:**\n"
        "Демонстрація найпростішого Telegram бота\n"
        "з інлайн-кнопкою на платформі Heroku.\n\n"
        "👨‍💻 **Розробник:** Олександр\n"
        "📍 **Статус:** Активний ✅"
    )
    
    await update.message.reply_text(info_text, parse_mode='Markdown')

def run_bot():
    """Запустити Telegram бота"""
    if not BOT_TOKEN:
        logger.error("❌ ПОМИЛКА: Не вказано BOT_TOKEN!")
        logger.error("Додайте змінну середовища BOT_TOKEN на Heroku")
        return
    
    # Створюємо додаток
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Додаємо обробники команд
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("info", info_command))
    
    # Додаємо обробник кнопок
    application.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info("🤖 Бот запускається...")
    logger.info(f"🌐 Веб-сервер на порті: {PORT}")
    
    # Запускаємо бота
    application.run_polling()

# ================ ЗАПУСК ================

if __name__ == "__main__":
    import threading
    
    # Запускаємо Telegram бота в окремому потоці
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    logger.info("✅ Telegram бот запущено в окремому потоці")
    
    # Запускаємо Flask веб-сервер (обов'язково для Heroku)
    flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)