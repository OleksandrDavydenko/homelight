import os
import logging
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv

# Завантажуємо змінні середовища
load_dotenv()

# ================= НАЛАШТУВАННЯ =================
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Отримайте від @BotFather
PORT = int(os.getenv("PORT", 5000))
# ================================================

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Створюємо Flask додаток для Heroku
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return jsonify({
        "status": "online",
        "service": "Simple Telegram Bot",
        "message": "Бот працює на Heroku! 🤖"
    })

@flask_app.route('/health')
def health():
    return jsonify({"status": "healthy"})

# ================= TELEGRAM КОМАНДИ =================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start - привітання"""
    user = update.effective_user
    
    # Персональне привітання
    welcome_message = (
        f"👋 Привіт, {user.first_name}!\n\n"
        
        "Я - простий тестовий бот на Heroku! 🚀\n\n"
        
        "📋 **Що я вмію:**\n"
        "• Привітатись з тобою\n"
        "• Показати твоє ім'я\n"
        "• Показати твій ID\n\n"
        
        "🆔 **Твої дані:**\n"
        f"• Ім'я: {user.first_name}\n"
        f"• Прізвище: {user.last_name or 'Не вказано'}\n"
        f"• Username: @{user.username or 'Не вказано'}\n"
        f"• ID: {user.id}\n\n"
        
        "💡 **Команди:**\n"
        "/start - це повідомлення\n"
        "/help - довідка\n"
        "/info - інформація про бота\n\n"
        
        "🎉 **Бот працює на Heroku 24/7!**"
    )
    
    await update.message.reply_text(welcome_message, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    help_text = (
        "🆘 **Довідка по боту**\n\n"
        "Це простий тестовий бот для демонстрації роботи на Heroku.\n\n"
        "📋 **Доступні команди:**\n"
        "• /start - Привітання та інформація\n"
        "• /help - Ця довідка\n"
        "• /info - Про бота\n"
        "• /ping - Перевірка роботи\n\n"
        "⚙️ **Технології:**\n"
        "• Python + python-telegram-bot\n"
        "• Heroku для хостингу\n"
        "• Flask для веб-сервера\n\n"
        "👨‍💻 **Розробник:** Олександр"
    )
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /info"""
    info_text = (
        "🤖 **Інформація про бота**\n\n"
        
        "🔧 **Технічні деталі:**\n"
        "• Версія: 1.0\n"
        "• Платформа: Heroku\n"
        "• Мова: Python 3.11\n"
        "• Бібліотека: python-telegram-bot\n\n"
        
        "🎯 **Мета:**\n"
        "Демонстрація роботи Telegram бота на хмарній платформі Heroku.\n\n"
        
        "🚀 **Переваги Heroku:**\n"
        "• Бот працює 24/7 без перерв\n"
        "• Не потрібен власний сервер\n"
        "• Автоматичне масштабування\n"
        "• Просте розгортання\n\n"
        
        "📞 **Контакти:**\n"
        "Це демонстраційний бот для навчання."
    )
    
    await update.message.reply_text(info_text, parse_mode='Markdown')

async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /ping - перевірка роботи"""
    await update.message.reply_text(
        "🏓 **Pong!**\n\n"
        "✅ Бот працює нормально!\n"
        "🌐 Сервер: Heroku\n"
        "⚡ Статус: Активний"
    )

async def echo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ехо-відповідь на текстові повідомлення"""
    user_text = update.message.text
    
    if user_text.startswith('/'):
        return  # Ігноруємо команди
    
    user = update.effective_user
    response = (
        f"📝 **Ти написав:** {user_text}\n\n"
        f"👤 **Від:** {user.first_name}\n"
        f"🆔 **ID:** {user.id}\n\n"
        "💡 Напиши /help для списку команд"
    )
    
    await update.message.reply_text(response, parse_mode='Markdown')

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
    application.add_handler(CommandHandler("ping", ping_command))
    
    # Додаємо обробник для всіх текстових повідомлень
    from telegram.ext import MessageHandler, filters
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_command))
    
    logger.info("🤖 Бот запускається...")
    logger.info(f"🌐 Веб-сервер на порті: {PORT}")
    
    # Запускаємо бота
    application.run_polling()

# Головна функція
def main():
    """Запуск всього додатка"""
    import threading
    
    # Запускаємо Telegram бота в окремому потоці
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    logger.info("✅ Telegram бот запущено в окремому потоці")
    
    # Запускаємо Flask веб-сервер (обов'язково для Heroku)
    flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

if __name__ == "__main__":
    main()