# app.py - Головний файл
import os
import json
import logging
from datetime import datetime, timedelta
from threading import Thread
import time

import tinytuya
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv

# Завантажуємо змінні середовища
load_dotenv()

# ================= НАЛАШТУВАННЯ =================
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Змінна середовища Heroku
DEVICE_ID = os.getenv("DEVICE_ID", "bf3112f230a24fbeb6xvhp")
DEVICE_IP = os.getenv("DEVICE_IP")  # IP розетки
LOCAL_KEY = os.getenv("LOCAL_KEY")  # Local Key

# Файли для збереження даних (на Heroku файлова система тимчасова)
USERS_FILE = 'subscribers.json'
LOGS_FILE = 'power_logs.json'
# ================================================

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class PowerMonitorBot:
    """Бот для моніторингу світла на Heroku"""
    
    def __init__(self):
        self.application = None
        self.device = None
        self.last_status = None
        self.monitoring_active = False
        self.monitor_thread = None
        
        # Завантажуємо дані
        self.subscribers = self.load_data(USERS_FILE, [])
        self.power_logs = self.load_data(LOGS_FILE, [])
        
        # Налаштовуємо пристрій
        self.setup_device()
    
    def load_data(self, filename, default):
        """Завантажити дані з файлу"""
        try:
            with open(filename, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return default
    
    def save_data(self, filename, data):
        """Зберегти дані у файл"""
        try:
            with open(filename, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Помилка збереження {filename}: {e}")
    
    def setup_device(self):
        """Налаштувати підключення до розетки"""
        if not all([DEVICE_IP, LOCAL_KEY]):
            logger.warning("Не вказано IP або Local Key розетки")
            return
        
        try:
            self.device = tinytuya.OutletDevice(DEVICE_ID, DEVICE_IP, LOCAL_KEY)
            self.device.set_version(3.3)
            logger.info("✅ Розетка налаштована")
        except Exception as e:
            logger.error(f"❌ Помилка налаштування розетки: {e}")
    
    def check_power_status(self):
        """Перевірити статус світла"""
        if not self.device:
            logger.error("Розетка не налаштована")
            return None
        
        try:
            data = self.device.status()
            
            if 'dps' in data:
                # Шукаємо статус перемикача
                for key, value in data['dps'].items():
                    if key == '1' or 'switch' in str(key).lower():
                        return bool(value)
            
            return None
        except Exception as e:
            logger.error(f"Помилка перевірки статусу: {e}")
            return None
    
    async def send_notification_to_all(self, message):
        """Надіслати сповіщення всім підписникам"""
        if not self.application:
            return
        
        for user_id in self.subscribers:
            try:
                await self.application.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode='Markdown'
                )
                logger.info(f"Сповіщення надіслано користувачу {user_id}")
            except Exception as e:
                logger.error(f"Помилка відправки користувачу {user_id}: {e}")
                # Видаляємо користувача, який заблокував бота
                if "blocked" in str(e).lower() or "chat not found" in str(e).lower():
                    self.subscribers.remove(user_id)
                    self.save_data(USERS_FILE, self.subscribers)
    
    def monitor_power(self):
        """Фоновий моніторинг статусу світла"""
        logger.info("🚀 Запуск фонового моніторингу...")
        
        while self.monitoring_active:
            try:
                current_status = self.check_power_status()
                
                if current_status is not None:
                    # Логуємо статус
                    log_entry = {
                        'timestamp': datetime.now().isoformat(),
                        'status': 'ON' if current_status else 'OFF'
                    }
                    self.power_logs.append(log_entry)
                    
                    # Зберігаємо останні 100 записів
                    if len(self.power_logs) > 100:
                        self.power_logs = self.power_logs[-100:]
                    self.save_data(LOGS_FILE, self.power_logs)
                    
                    # Перевіряємо зміну статусу
                    if self.last_status is not None and current_status != self.last_status:
                        timestamp = datetime.now().strftime("%H:%M:%S")
                        
                        if current_status:
                            message = f"⚡ **СВІТЛО З'ЯВИЛОСЯ!**\nЧас: {timestamp}"
                        else:
                            message = f"💡 **СВІТЛО ЗНИКЛО!**\nЧас: {timestamp}"
                        
                        # Надсилаємо сповіщення асинхронно
                        if self.application:
                            import asyncio
                            asyncio.run_coroutine_threadsafe(
                                self.send_notification_to_all(message),
                                self.application.create_task
                            )
                    
                    # Оновлюємо статус
                    self.last_status = current_status
                
                # Чекаємо 30 секунд
                time.sleep(30)
                
            except Exception as e:
                logger.error(f"Помилка моніторингу: {e}")
                time.sleep(60)  # Чекаємо довше при помилці
    
    def start_monitoring(self):
        """Запустити фоновий моніторинг"""
        if not self.monitoring_active:
            self.monitoring_active = True
            self.monitor_thread = Thread(target=self.monitor_power, daemon=True)
            self.monitor_thread.start()
            logger.info("✅ Фоновий моніторинг запущено")
    
    def stop_monitoring(self):
        """Зупинити фоновий моніторинг"""
        self.monitoring_active = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        logger.info("⏹️ Фоновий моніторинг зупинено")
    
    # ================= TELEGRAM КОМАНДИ =================
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start - підписатися на сповіщення"""
        user = update.effective_user
        user_id = user.id
        
        if user_id not in self.subscribers:
            self.subscribers.append(user_id)
            self.save_data(USERS_FILE, self.subscribers)
            
            await update.message.reply_text(
                f"👋 Привіт, {user.first_name}!\n\n"
                "✅ Ви підписались на сповіщення про світло!\n\n"
                "⚡ Ви будете отримувати повідомлення, коли:\n"
                "• З'явиться світло\n"
                "• Зникне світло\n\n"
                "📋 Команди:\n"
                "/status - поточний статус\n"
                "/unsubscribe - відписатись\n"
                "/subscribers - хто підписаний\n"
                "/logs - історія змін\n"
                "/help - довідка"
            )
            
            # Автоматично запускаємо моніторинг при першому підписнику
            if not self.monitoring_active:
                self.start_monitoring()
        else:
            await update.message.reply_text(
                f"👋 З поверненням, {user.first_name}!\n"
                "Ви вже підписані на сповіщення."
            )
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /status - поточний статус"""
        user_id = update.effective_user.id
        
        if user_id not in self.subscribers:
            await update.message.reply_text(
                "❌ Ви не підписані на сповіщення.\n"
                "Напишіть /start щоб підписатись."
            )
            return
        
        msg = await update.message.reply_text("🔍 Перевіряю статус...")
        
        current_status = self.check_power_status()
        
        if current_status is None:
            await msg.edit_text("⚠️ Не вдалося отримати статус")
        elif current_status:
            await msg.edit_text("✅ **СВІТЛО Є!**\nРозетка активна")
        else:
            await msg.edit_text("❌ **СВІТЛА НЕМА!**\nРозетка неактивна")
    
    async def unsubscribe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /unsubscribe - відписатися"""
        user = update.effective_user
        user_id = user.id
        
        if user_id in self.subscribers:
            self.subscribers.remove(user_id)
            self.save_data(USERS_FILE, self.subscribers)
            
            await update.message.reply_text(
                f"👋 {user.first_name}, ви відписались від сповіщень.\n"
                "Напишіть /start щоб підписатись знову."
            )
            
            # Якщо не залишилось підписників, зупиняємо моніторинг
            if not self.subscribers:
                self.stop_monitoring()
        else:
            await update.message.reply_text("ℹ️ Ви не підписані на сповіщення")
    
    async def subscribers_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /subscribers - список підписників"""
        user_id = update.effective_user.id
        
        # Тільки адмін може переглядати список
        admin_id = os.getenv("ADMIN_ID")
        if admin_id and str(user_id) != admin_id:
            await update.message.reply_text("⛔ Ця команда тільки для адміністратора")
            return
        
        if not self.subscribers:
            await update.message.reply_text("📭 Підписників поки немає")
            return
        
        subscribers_list = "👥 **Список підписників:**\n\n"
        
        # Отримуємо імена користувачів
        for sub_id in self.subscribers:
            try:
                chat = await context.bot.get_chat(sub_id)
                name = chat.first_name or "Невідомий"
                subscribers_list += f"• {name} (ID: `{sub_id}`)\n"
            except:
                subscribers_list += f"• Невідомий (ID: `{sub_id}`)\n"
        
        subscribers_list += f"\n📊 **Всього:** {len(self.subscribers)} осіб"
        
        await update.message.reply_text(subscribers_list, parse_mode='Markdown')
    
    async def logs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /logs - історія змін"""
        user_id = update.effective_user.id
        
        if user_id not in self.subscribers:
            await update.message.reply_text("❌ Ви не підписані")
            return
        
        if not self.power_logs:
            await update.message.reply_text("📊 Історія змін поки що порожня")
            return
        
        # Беремо останні 10 записів
        recent_logs = self.power_logs[-10:]
        
        logs_text = "📊 **Останні зміни статусу:**\n\n"
        
        for log in reversed(recent_logs):
            timestamp = datetime.fromisoformat(log['timestamp']).strftime("%H:%M:%S")
            status = "✅ УВІМК." if log['status'] == 'ON' else "❌ ВИМК."
            logs_text += f"{status} - {timestamp}\n"
        
        # Додаємо поточний статус
        current_status = self.check_power_status()
        if current_status is not None:
            status_text = "✅ УВІМКНЕНО" if current_status else "❌ ВИМКНЕНО"
            logs_text += f"\n📈 **Поточний статус:** {status_text}"
        
        await update.message.reply_text(logs_text, parse_mode='Markdown')
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /help - довідка"""
        help_text = (
            "🆘 **Довідка по боту**\n\n"
            "Цей бот сповіщає про наявність світла у квартирі.\n\n"
            "📋 **Команди:**\n"
            "• /start - Підписатись на сповіщення\n"
            "• /status - Поточний статус світла\n"
            "• /unsubscribe - Відписатись\n"
            "• /logs - Історія змін\n"
            "• /help - Ця довідка\n\n"
            "⚡ **Сповіщення:**\n"
            "Ви отримаєте повідомлення при:\n"
            "• З'явленні світла\n"
            "• Зникненні світла\n\n"
            "💡 **Примітка:**\n"
            "Бот працює 24/7 на Heroku сервері."
        )
        
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def setup_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /setup - налаштування (тільки для адміна)"""
        user_id = update.effective_user.id
        admin_id = os.getenv("ADMIN_ID")
        
        if admin_id and str(user_id) != admin_id:
            await update.message.reply_text("⛔ Ця команда тільки для адміністратора")
            return
        
        if len(context.args) < 2:
            await update.message.reply_text(
                "⚙️ **Налаштування розетки:**\n\n"
                "Формат: `/setup IP LOCAL_KEY`\n\n"
                "**Приклад:**\n"
                "`/setup 192.168.1.100 abc123def456`\n\n"
                "🔧 **Дані потрібні один раз:**\n"
                "1. IP розетки в вашій домашній мережі\n"
                "2. Local Key розетки\n\n"
                "💡 **Як знайти:**\n"
                "python -m tinytuya scan\n"
                "python -m tinytuya wizard"
            )
            return
        
        ip = context.args[0]
        local_key = context.args[1]
        
        # Оновлюємо змінні середовища (для поточного сеансу)
        global DEVICE_IP, LOCAL_KEY
        DEVICE_IP = ip
        LOCAL_KEY = local_key
        
        # Налаштовуємо пристрій
        try:
            self.device = tinytuya.OutletDevice(DEVICE_ID, ip, local_key)
            self.device.set_version(3.3)
            
            # Тестуємо підключення
            status = self.device.status()
            
            if status:
                # Зберігаємо у файл (тимчасово на Heroku)
                config = {
                    'device_ip': ip,
                    'local_key': local_key,
                    'setup_time': datetime.now().isoformat()
                }
                self.save_data('device_config.json', config)
                
                await update.message.reply_text(
                    f"✅ Розетка налаштована!\n\n"
                    f"IP: `{ip}`\n"
                    f"Ключ: `{local_key[:10]}...`\n\n"
                    f"📊 Тестова перевірка: {'Успішно' if status else 'Помилка'}"
                )
                
                # Запускаємо моніторинг, якщо є підписники
                if self.subscribers and not self.monitoring_active:
                    self.start_monitoring()
            else:
                await update.message.reply_text("⚠️ Налаштовано, але не вдалося підключитись")
                
        except Exception as e:
            await update.message.reply_text(f"❌ Помилка налаштування: {str(e)}")
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /stats - статистика"""
        user_id = update.effective_user.id
        
        if user_id not in self.subscribers:
            await update.message.reply_text("❌ Ви не підписані")
            return
        
        stats_text = (
            f"📊 **Статистика бота:**\n\n"
            f"👥 Підписників: {len(self.subscribers)}\n"
            f"📈 Записів в історії: {len(self.power_logs)}\n"
            f"🔄 Моніторинг: {'Активний ✅' if self.monitoring_active else 'Неактивний ❌'}\n"
        )
        
        # Аналізуємо останні зміни
        if self.power_logs:
            last_change = datetime.fromisoformat(self.power_logs[-1]['timestamp'])
            time_diff = datetime.now() - last_change
            hours = int(time_diff.total_seconds() / 3600)
            minutes = int((time_diff.total_seconds() % 3600) / 60)
            
            stats_text += f"⏰ Остання зміна: {hours} год {minutes} хв тому\n"
        
        await update.message.reply_text(stats_text, parse_mode='Markdown')
    
    def run(self):
        """Запустити бота"""
        if not BOT_TOKEN:
            logger.error("❌ Не вказано BOT_TOKEN в змінних середовища")
            return
        
        # Створюємо додаток
        self.application = Application.builder().token(BOT_TOKEN).build()
        
        # Додаємо обробники команд
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("unsubscribe", self.unsubscribe_command))
        self.application.add_handler(CommandHandler("subscribers", self.subscribers_command))
        self.application.add_handler(CommandHandler("logs", self.logs_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("setup", self.setup_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        
        # Завантажуємо конфігурацію розетки
        try:
            with open('device_config.json', 'r') as f:
                config = json.load(f)
                global DEVICE_IP, LOCAL_KEY
                DEVICE_IP = config.get('device_ip')
                LOCAL_KEY = config.get('local_key')
                self.setup_device()
        except:
            logger.info("Конфігурація розетки не знайдена")
        
        # Запускаємо моніторинг, якщо є підписники
        if self.subscribers:
            self.start_monitoring()
        
        logger.info("🤖 Бот запущено на Heroku")
        
        # Запускаємо бота
        self.application.run_polling()

# Запуск бота
if __name__ == "__main__":
    bot = PowerMonitorBot()
    bot.run()