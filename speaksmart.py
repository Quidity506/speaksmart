# speaksmart.py
import logging
import os
# import threading # Больше не нужен здесь напрямую, так как поток создается в health_checker
# from http.server import BaseHTTPRequestHandler, HTTPServer # Больше не нужно здесь
# from urllib.parse import urlparse # Больше не нужно здесь
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Импорт нашего модуля для работы с Gemini API
import gemini_api 
# Импорт нашего нового модуля для health check
import health_checker # <--- НОВЫЙ ИМПОРТ

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Получаем API ключи из переменных окружения
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Порт для Health Check сервера берется из health_checker.py, если нужно его здесь знать
# HEALTH_CHECK_PORT = health_checker.HEALTH_CHECK_PORT 
# Но для команды /status мы можем просто сослаться на то, что он запущен

# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет приветственное сообщение при команде /start."""
    await update.message.reply_text(
        "Привет! Я бот для переформулирования сообщений. Отправь мне текст, и я сделаю его более профессиональным."
    )

# Обработчик команды /status
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет сообщение о статусе бота."""
    # Можно получить порт из health_checker, если он там публичный, или просто сообщить, что health check активен
    await update.message.reply_text(f"Бот онлайн и готов к работе! Health check сервер активен (порт {health_checker.HEALTH_CHECK_PORT}).")


# Обработчик текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает текстовое сообщение пользователя и переформулирует его с помощью Gemini API."""
    user_message = update.message.text
    chat_id = update.effective_chat.id
    
    if not user_message: 
        await update.message.reply_text("Пожалуйста, отправьте непустое сообщение.")
        return

    logger.info(f"Получено сообщение от chat_id {chat_id}: '{user_message}'")
    
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    prompt = f"""Переформулируй следующее сообщение, чтобы оно звучало более профессионально и вежливо, сохраняя его первоначальный смысл:

"{user_message}"

Предоставь только переформулированный текст без каких-либо дополнительных комментариев, вступлений или объяснений.
"""
    
    if not GEMINI_API_KEY: 
        logger.error("GEMINI_API_KEY не настроен. Невозможно обработать запрос.")
        await update.message.reply_text("Ошибка конфигурации сервиса. Пожалуйста, попробуйте позже.")
        return

    try:
        response_text = gemini_api.ask_gemini(prompt, GEMINI_API_KEY)
        await update.message.reply_text(response_text)
        logger.info(f"Ответ от Gemini для chat_id {chat_id}: '{response_text}'")
    except Exception as e:
        logger.error(f"Ошибка при вызове gemini_api.ask_gemini для chat_id {chat_id}: {e}", exc_info=True)
        await update.message.reply_text("К сожалению, произошла ошибка при обработке вашего запроса. Попробуйте позже.")


def main():
    """Основная функция для запуска бота и health check сервера."""
    if not TELEGRAM_TOKEN:
        logger.critical("Переменная окружения TELEGRAM_TOKEN не найдена! Бот не может быть запущен.")
        return
    if not GEMINI_API_KEY:
        logger.critical("Переменная окружения GEMINI_API_KEY не найдена! Бот не может быть запущен.")
        return

    logger.info("Запуск основного приложения бота...")
    
    # Запускаем HTTP-сервер для Health Check из нашего нового модуля
    health_checker.start_health_check_server_in_thread() # <--- ВЫЗОВ ФУНКЦИИ ИЗ НОВОГО МОДУЛЯ
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Бот Telegram успешно настроен и запускается в режиме опроса...")
    try:
        application.run_polling()
    except Exception as e:
        logger.critical(f"Критическая ошибка при работе Telegram-бота: {e}", exc_info=True)
    finally:
        logger.info("Бот Telegram остановлен.")

if __name__ == "__main__":
    main()
