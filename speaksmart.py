# speaksmart.py
import logging
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

import gemini_api

# Настройка логирования (остается без изменений, очень полезна на сервере)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Получаем API ключи из переменных окружения
# На Render.com (или аналогичной платформе) вы настроите эти переменные.
# Для локального тестирования вы можете установить их в своей системе
# или временно задать значения по умолчанию (но не для продакшена!).
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Проверка, что API ключи загружены
if not TELEGRAM_TOKEN:
    logger.error("Переменная окружения TELEGRAM_TOKEN не найдена!")
    # В реальном приложении здесь можно было бы завершить работу,
    # но для простоты оставим так. Бот не запустится без токена.
if not GEMINI_API_KEY:
    logger.error("Переменная окружения GEMINI_API_KEY не найдена!")


# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет приветственное сообщение при команде /start."""
    await update.message.reply_text(
        "Привет! Я бот для переформулирования сообщений. Отправь мне текст, и я сделаю его более профессиональным."
    )

# Обработчик команды /status
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет сообщение о статусе бота."""
    # Старая проверка прокси здесь больше не нужна.
    # Можно добавить другую информацию, например, время работы или версию.
    await update.message.reply_text("Бот онлайн и готов к работе!")

# Обработчик текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает текстовое сообщение пользователя и переформулирует его с помощью Gemini API."""
    user_message = update.message.text
    chat_id = update.effective_chat.id
    
    if not user_message:
        await update.message.reply_text("Пожалуйста, отправьте непустое сообщение.")
        return

    logger.info(f"Получено сообщение от chat_id {chat_id}: '{user_message}'")
    
    # Отправляем "печатает..." статус для улучшения UX
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    # Формируем запрос к Gemini
    # Этот промпт можно доработать для лучшего качества или разных стилей
    prompt = f"""Переформулируй следующее сообщение, чтобы оно звучало более профессионально и вежливо, сохраняя его первоначальный смысл:

"{user_message}"

Предоставь только переформулированный текст без каких-либо дополнительных комментариев, вступлений или объяснений.
"""
    
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY не настроен. Невозможно обработать запрос.")
        await update.message.reply_text("Ошибка конфигурации сервиса. Пожалуйста, попробуйте позже.")
        return

    # Получаем ответ от Gemini
    try:
        response_text = gemini_api.ask_gemini(prompt, GEMINI_API_KEY)
        await update.message.reply_text(response_text)
        logger.info(f"Ответ от Gemini для chat_id {chat_id}: '{response_text}'")
    except Exception as e:
        logger.error(f"Ошибка при вызове gemini_api.ask_gemini для chat_id {chat_id}: {e}")
        await update.message.reply_text("К сожалению, произошла ошибка при обработке вашего запроса. Попробуйте позже.")


def main():
    """Основная функция для запуска бота."""
    if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
        logger.critical("Один или оба API ключа (TELEGRAM_TOKEN, GEMINI_API_KEY) не установлены в переменных окружения. Бот не может быть запущен.")
        return

    logger.info("Запуск бота...")
    
    # Настройка прокси (gemini_api.setup_proxy()) и проверка IP (gemini_api.check_proxy())
    # здесь больше НЕ НУЖНЫ, так как бот будет работать на сервере 
    # с "чистым" IP-адресом (например, на Render.com).
    
    # Создаем приложение Telegram бота
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Добавляем обработчики команд и сообщений
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Запускаем бота (в режиме опроса)
    logger.info("Бот успешно запущен и работает в режиме опроса.")
    application.run_polling()
    logger.info("Бот остановлен.")

if __name__ == "__main__":
    main()
