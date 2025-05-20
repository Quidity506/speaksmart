# speaksmart.py
import logging
import os
import threading # Для запуска HTTP-сервера в отдельном потоке
from http.server import BaseHTTPRequestHandler, HTTPServer # Для простого HTTP-сервера
from urllib.parse import urlparse # Для разбора пути запроса
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Импорт нашего обновленного модуля для работы с Gemini API
import gemini_api # Предполагается, что gemini_api.py находится в той же директории

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Получаем API ключи из переменных окружения
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Порт для Health Check сервера (Render предоставляет его через переменную PORT)
# Используем 8080 по умолчанию, если PORT не задан, но Render должен предоставить переменную PORT.
HEALTH_CHECK_PORT = int(os.environ.get('PORT', 8080)) 

# --- Код для простого HTTP-сервера для Health Check ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urlparse(self.path)
        # Render обычно проверяет путь /healthz или просто корневой путь /
        # Мы будем отвечать на /healthz, как это часто принято
        if parsed_path.path == '/healthz': 
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"OK") # Отправляем простое "OK"
        else:
            # На все остальные пути отвечаем 404
            self.send_response(404)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Not Found")

def run_health_check_server():
    """Запускает HTTP-сервер для health checks в отдельном потоке."""
    try:
        # Слушаем на всех интерфейсах ('') на указанном порту
        server_address = ('', HEALTH_CHECK_PORT)
        httpd = HTTPServer(server_address, HealthCheckHandler)
        logger.info(f"Запуск Health Check HTTP-сервера на порту {HEALTH_CHECK_PORT}...")
        httpd.serve_forever() # Запускаем сервер бесконечно
    except Exception as e:
        logger.error(f"Ошибка при запуске или работе Health Check HTTP-сервера: {e}", exc_info=True)
# --- Конец кода для HTTP-сервера ---

# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет приветственное сообщение при команде /start."""
    await update.message.reply_text(
        "Привет! Я бот для переформулирования сообщений. Отправь мне текст, и я сделаю его более профессиональным."
    )

# Обработчик команды /status
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет сообщение о статусе бота."""
    await update.message.reply_text(f"Бот онлайн и готов к работе! Health check сервер (если запущен Render) слушает порт {HEALTH_CHECK_PORT}.")

# Обработчик текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает текстовое сообщение пользователя и переформулирует его с помощью Gemini API."""
    user_message = update.message.text
    chat_id = update.effective_chat.id
    
    if not user_message: # Проверка на пустое сообщение
        await update.message.reply_text("Пожалуйста, отправьте непустое сообщение.")
        return

    logger.info(f"Получено сообщение от chat_id {chat_id}: '{user_message}'")
    
    # Отправляем "печатает..." статус для улучшения UX
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    # Формируем запрос к Gemini
    prompt = f"""Переформулируй следующее сообщение, чтобы оно звучало более профессионально и вежливо, сохраняя его первоначальный смысл:

"{user_message}"

Предоставь только переформулированный текст без каких-либо дополнительных комментариев, вступлений или объяснений.
"""
    
    if not GEMINI_API_KEY: # Проверка наличия ключа Gemini
        logger.error("GEMINI_API_KEY не настроен. Невозможно обработать запрос.")
        await update.message.reply_text("Ошибка конфигурации сервиса. Пожалуйста, попробуйте позже.")
        return

    # Получаем ответ от Gemini
    try:
        response_text = gemini_api.ask_gemini(prompt, GEMINI_API_KEY)
        await update.message.reply_text(response_text)
        logger.info(f"Ответ от Gemini для chat_id {chat_id}: '{response_text}'")
    except Exception as e:
        logger.error(f"Ошибка при вызове gemini_api.ask_gemini для chat_id {chat_id}: {e}", exc_info=True)
        await update.message.reply_text("К сожалению, произошла ошибка при обработке вашего запроса. Попробуйте позже.")


def main():
    """Основная функция для запуска бота и health check сервера."""
    # Проверка наличия токенов перед запуском
    if not TELEGRAM_TOKEN:
        logger.critical("Переменная окружения TELEGRAM_TOKEN не найдена! Бот не может быть запущен.")
        return
    if not GEMINI_API_KEY:
        logger.critical("Переменная окружения GEMINI_API_KEY не найдена! Бот не может быть запущен.")
        return

    logger.info("Запуск основного приложения бота...")
    
    # Запускаем HTTP-сервер для Health Check в отдельном потоке
    # daemon=True означает, что поток завершится, когда завершится основной поток программы
    health_thread = threading.Thread(target=run_health_check_server, daemon=True)
    health_thread.start()
    
    # Создаем и настраиваем приложение Telegram бота
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Бот Telegram успешно настроен и запускается в режиме опроса...")
    try:
        application.run_polling() # Запускаем бота
    except Exception as e:
        # Логируем критические ошибки, которые могут остановить бота
        logger.critical(f"Критическая ошибка при работе Telegram-бота: {e}", exc_info=True)
    finally:
        # Это сообщение появится, если run_polling() завершится (например, из-за ошибки или остановки)
        logger.info("Бот Telegram остановлен.")

if __name__ == "__main__":
    main()
