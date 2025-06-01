import logging
import os
import threading # Для запуска HTTP-сервера в отдельном потоке
from http.server import BaseHTTPRequestHandler, HTTPServer # Для простого HTTP-сервера
from urllib.parse import urlparse # Для разбора пути запроса

# --- НОВЫЕ ИМПОРТЫ ---
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    # --- НОВЫЙ ИМПОРТ ---
    ConversationHandler,
    CallbackQueryHandler # Для обработки нажатий на инлайн-кнопки
)

import gemini_api # Предполагается, что gemini_api.py находится в той же директории

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Получаем API ключи из переменных окружения
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") # Пока не используется активно в ConversationHandler, но понадобится позже

# Порт для Health Check сервера (Render предоставляет его через переменную PORT)
HEALTH_CHECK_PORT = int(os.environ.get('PORT', 8080)) 

# --- НОВЫЕ КОНСТАНТЫ ДЛЯ СОСТОЯНИЙ ДИАЛОГА ---
# Определяем состояния. Мы можем добавлять больше по мере необходимости.
# GET_TEXT_FOR_CORRECTION: Ожидание текста от пользователя.
# CHOOSE_STYLE: Пользователь выбирает стиль из предложенных.
# DESCRIBE_ADDRESSEE: Пользователь описывает адресата для автоопределения стиля.
# POST_PROCESSING_MENU: Пользователю предложено меню для доработки переформулированного текста.
GET_TEXT_FOR_CORRECTION, CHOOSE_STYLE, DESCRIBE_ADDRESSEE, POST_PROCESSING_MENU = range(4)


# --- Код для простого HTTP-сервера для Health Check ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == '/healthz': 
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Not Found")

def run_health_check_server():
    """Запускает HTTP-сервер для health checks в отдельном потоке."""
    try:
        server_address = ('', HEALTH_CHECK_PORT)
        httpd = HTTPServer(server_address, HealthCheckHandler)
        logger.info(f"Запуск Health Check HTTP-сервера на порту {HEALTH_CHECK_PORT}...")
        httpd.serve_forever()
    except Exception as e:
        logger.error(f"Ошибка при запуске или работе Health Check HTTP-сервера: {e}", exc_info=True)
# --- Конец кода для HTTP-сервера ---

# --- ОБНОВЛЕННАЯ КОМАНДА /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет приветственное сообщение с обзором функций и кнопкой для начала исправления."""
    user_name = update.effective_user.first_name
    welcome_text = (
        f"Привет, {user_name}!\n\n"
        "Я SpeakSmartBot — твой помощник для улучшения текстов. "
        "Я могу помочь тебе переформулировать сообщения, чтобы они звучали более профессионально, вежливо или соответствовали определенному стилю.\n\n"
        "Что я умею:\n"
        "🔹 Принимать твой текст для обработки.\n"
        "🔹 Предлагать выбор стиля (например, деловой, неформальный, упрощенный).\n"
        "🔹 Автоматически определять подходящий стиль, если ты опишешь адресата.\n"
        "🔹 После переформулирования, ты сможешь дополнительно скорректировать тон (например, сделать текст мягче или строже).\n\n"
        "Готов начать?"
    )
    keyboard = [
        [InlineKeyboardButton("Улучшить текст 📝", callback_data="start_correction")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        welcome_text,
        reply_markup=reply_markup
    )

# --- НОВАЯ ФУНКЦИЯ ДЛЯ НАЧАЛА ДИАЛОГА (ENTRY POINT ПОСЛЕ НАЖАТИЯ КНОПКИ) ---
# --- ФУНКЦИЯ ДЛЯ НАЧАЛА ДИАЛОГА (ENTRY POINT ПОСЛЕ НАЖАТИЯ КНОПКИ) - ВАРИАНТ А ---
async def request_text_for_correction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Вызывается после нажатия инлайн-кнопки "Улучшить текст 📝".
    Отправляет новое сообщение с запросом текста и переводит диалог в состояние GET_TEXT_FOR_CORRECTION.
    """
    query = update.callback_query
    # Обязательно отвечаем на callback query, чтобы убрать "часики" на кнопке в интерфейсе Telegram
    await query.answer() 
    
    # Отправляем новое сообщение с запросом текста
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Отлично! Теперь, пожалуйста, отправь мне текст, который нужно переформулировать."
    )
    
    # Опционально: убрать клавиатуру из предыдущего сообщения, чтобы ее нельзя было нажать снова.
    # Если этого не сделать, предыдущее сообщение с кнопкой останется активным.
    # Для полной чистоты лучше убрать:
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        # Может возникнуть ошибка, если сообщение слишком старое или что-то еще,
        # но это не критично для основного флоу. Просто логируем.
        logger.debug(f"Не удалось убрать клавиатуру из предыдущего сообщения: {e}")
        
    # Возвращаем следующее состояние диалога
    return GET_TEXT_FOR_CORRECTION

# --- ОБНОВЛЕННАЯ ФУНКЦИЯ-ОБРАБОТЧИК ДЛЯ ПОЛУЧЕННОГО ТЕКСТА ---
async def received_text_for_correction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Вызывается, когда пользователь отправляет текст, находясь в состоянии GET_TEXT_FOR_CORRECTION.
    Сохраняет текст и предлагает пользователю выбрать стиль.
    Переводит диалог в состояние CHOOSE_STYLE.
    """
    user_message = update.message.text
    chat_id = update.effective_chat.id
    logger.info(f"Получен текст для исправления от chat_id {chat_id}: '{user_message}'")
    
    context.user_data['text_to_correct'] = user_message # Сохраняем текст
    
    # Создаем клавиатуру для выбора стиля
    keyboard = [
        [InlineKeyboardButton("Деловой стиль", callback_data="style_business")],
        [InlineKeyboardButton("Учебный стиль", callback_data="style_academic")],
        [InlineKeyboardButton("Личное общение", callback_data="style_personal")],
        [InlineKeyboardButton("Упрощённый текст", callback_data="style_simplified")],
        [InlineKeyboardButton("Автоопределение стиля", callback_data="style_auto")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Понял тебя! Получил следующий текст (первые 50 символов): \"{user_message[:50]}...\"\n\n"
        "Теперь, пожалуйста, выбери стиль для переформулирования:",
        reply_markup=reply_markup
    )
    
    return CHOOSE_STYLE # Переходим в состояние выбора стиля
# --- НОВАЯ ФУНКЦИЯ-ОБРАБОТЧИК ДЛЯ ВЫБРАННОГО СТИЛЯ (ПОКА ЗАГЛУШКА) ---
# --- ОБНОВЛЕННАЯ ФУНКЦИЯ-ОБРАБОТЧИК ДЛЯ ВЫБРАННОГО СТИЛЯ ---
async def style_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Вызывается после того, как пользователь нажал на кнопку выбора стиля.
    Обрабатывает выбор конкретного стиля или переходит к описанию адресата для автоопределения.
    """
    query = update.callback_query
    await query.answer() # Отвечаем на callback
    
    style_choice = query.data # Это будет callback_data кнопки, например, 'style_business'
    context.user_data['chosen_style'] = style_choice 
    
    logger.info(f"Пользователь {query.from_user.id} выбрал стиль: {style_choice}")

    text_to_correct = context.user_data.get('text_to_correct')

    if not text_to_correct:
        await query.edit_message_text(
            text="Произошла ошибка: не найден исходный текст. Пожалуйста, начни заново: /start"
        )
        return ConversationHandler.END

    # Обработка конкретных стилей
    if style_choice in ["style_business", "style_academic", "style_personal", "style_simplified"]:
        # Готовим сообщение для пользователя, пока Gemini обрабатывает запрос
        await query.edit_message_text(text=f"Ты выбрал стиль: {style_choice}. Минуточку, обрабатываю твой текст...")

        # Определяем, какую инструкцию дать Gemini на основе выбора
        style_prompt_instruction = ""
        if style_choice == "style_business":
            style_prompt_instruction = "в официальном деловом стиле"
        elif style_choice == "style_academic":
            style_prompt_instruction = "в академическом или учебном стиле"
        elif style_choice == "style_personal":
            style_prompt_instruction = "в стиле личного, неформального общения"
        elif style_choice == "style_simplified":
            style_prompt_instruction = "максимально просто и понятно, упростив сложные конструкции"

        # Формируем полный промпт для Gemini
        prompt_for_gemini = (
            f"Переформулируй следующий текст {style_prompt_instruction}, "
            f"сохраняя его первоначальный смысл: \"{text_to_correct}\"\n\n"
            "Предоставь только переформулированный текст без каких-либо дополнительных комментариев, "
            "вступлений или объяснений."
        )
        
        logger.info(f"Промпт для Gemini: {prompt_for_gemini}")

        if not GEMINI_API_KEY:
            logger.error("GEMINI_API_KEY не настроен для style_chosen.")
            # Используем context.bot.send_message так как query.edit_message_text уже был вызван
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Ошибка конфигурации сервиса для обработки стиля. Ключ API не найден."
            )
            return ConversationHandler.END
        
        try:
            # Вызываем Gemini API
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
            response_text = gemini_api.ask_gemini(prompt_for_gemini, GEMINI_API_KEY)
            
            # Отправляем результат (заменяем "Минуточку...")
            # Так как мы уже использовали query.edit_message_text, если хотим снова редактировать то же сообщение,
            # нужно убедиться, что это возможно, или отправить новое. 
            # Для простоты отправим новое, если предыдущее сообщение уже было результатом edit_message_text от этого же query.
            # Однако, более чистый подход - если edit_message_text был выше, то здесь тоже его использовать.
            # Проверим, что query.message существует, прежде чем пытаться его отредактировать.
            if query.message:
                 await context.bot.edit_message_text( # Редактируем сообщение "Минуточку..."
                    text=f"Вот переформулированный текст:\n\n{response_text}",
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id
                )
            else: # Если query.message почему-то None, отправляем новое сообщение
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"Вот переформулированный текст:\n\n{response_text}"
                )
            logger.info(f"Ответ от Gemini (стиль {style_choice}): {response_text}")

        except Exception as e:
            logger.error(f"Ошибка при вызове gemini_api.ask_gemini для стиля {style_choice}: {e}", exc_info=True)
            if query.message:
                await context.bot.edit_message_text(
                    text="К сожалению, произошла ошибка при обработке вашего запроса. Попробуйте позже.",
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id
                )
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="К сожалению, произошла ошибка при обработке вашего запроса. Попробуйте позже."
                )

        return ConversationHandler.END # Завершаем диалог после обработки

    elif style_choice == "style_auto":
        # Этот блок мы реализуем в следующем подшаге
        await query.edit_message_text(
            text="Ты выбрал 'Автоопределение стиля'. "
                 "Этот функционал мы добавим следующим! Пока завершаю диалог."
        )
        return ConversationHandler.END # Пока завершаем
    
    else:
        # На случай, если callback_data какой-то непредвиденный
        await query.edit_message_text(text="Произошла неизвестная ошибка выбора стиля.")
        return ConversationHandler.END
    
# --- НОВАЯ ФУНКЦИЯ ДЛЯ ОТМЕНЫ ДИАЛОГА ---
async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отменяет текущий диалог и очищает user_data."""
    await update.message.reply_text(
        "Действие отменено. Если захочешь начать заново, просто нажми /start."
    )
    # Очищаем любые данные, которые могли быть сохранены в контексте этого диалога
    context.user_data.clear()
    # Завершаем диалог
    return ConversationHandler.END

# Обработчик команды /status (остается без изменений)
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет сообщение о статусе бота."""
    await update.message.reply_text(f"Бот онлайн и готов к работе! Health check сервер (если запущен Render) слушает порт {HEALTH_CHECK_PORT}.")

# Старый обработчик handle_message, который был в твоем предыдущем файле,
# теперь не нужен в таком виде, так как основная логика обработки текста
# будет происходить внутри ConversationHandler в определенных состояниях.
# Если его оставить, он может конфликтовать с ожиданием текста в состоянии GET_TEXT_FOR_CORRECTION.
# Поэтому он здесь закомментирован/удален.

def main() -> None:
    """Основная функция для запуска бота и health check сервера."""
    # Проверка наличия токенов перед запуском
    if not TELEGRAM_TOKEN:
        logger.critical("Переменная окружения TELEGRAM_TOKEN не найдена! Бот не может быть запущен.")
        return
    # GEMINI_API_KEY понадобится на следующих этапах, пока его отсутствие не критично для запуска ConversationHandler
    # if not GEMINI_API_KEY:
    #     logger.critical("Переменная окружения GEMINI_API_KEY не найдена! Бот не может быть запущен.")
    #     return

    logger.info("Запуск основного приложения бота...")
    
    health_thread = threading.Thread(target=run_health_check_server, daemon=True)
    health_thread.start()
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
# --- НАСТРОЙКА ConversationHandler ---
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(request_text_for_correction, pattern='^start_correction$')
        ],
        states={
            GET_TEXT_FOR_CORRECTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_text_for_correction)],
            CHOOSE_STYLE: [
                # Мы ожидаем callback_data, начинающийся с "style_"
                CallbackQueryHandler(style_chosen, pattern='^style_(business|academic|personal|simplified|auto)$')
            ],
            # ... Здесь в будущем будут DESCRIBE_ADDRESSEE, POST_PROCESSING_MENU и т.д. ...
        },
        fallbacks=[
            CommandHandler('cancel', cancel_conversation)
        ],
    )
    
    # Добавляем ConversationHandler в приложение. Он должен быть добавлен ПЕРЕД другими обработчиками,
    # которые могут перехватить те же типы обновлений (например, общий MessageHandler для текста).
    application.add_handler(conv_handler)
    
    # Добавляем обработчики для команд /start и /status.
    # Они будут работать независимо от ConversationHandler.
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    
    logger.info("Бот Telegram успешно настроен и запускается в режиме опроса...")
    try:
        application.run_polling() # Запускаем бота
    except Exception as e:
        logger.critical(f"Критическая ошибка при работе Telegram-бота: {e}", exc_info=True)
    finally:
        logger.info("Бот Telegram остановлен.")

if __name__ == "__main__":
    main()
