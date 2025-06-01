import logging
import os
# import threading # threading теперь используется внутри health_checker
# from http.server import BaseHTTPRequestHandler, HTTPServer # Эти импорты больше не нужны здесь
# from urllib.parse import urlparse # Этот импорт больше не нужен здесь

# --- ИМПОРТЫ TELEGRAM ---
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
# ReplyKeyboardMarkup и KeyboardButton пока не используем, но оставим импорт на будущее, если понадобится
from telegram import ReplyKeyboardMarkup, KeyboardButton 
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler
)

# --- ИМПОРТЫ НАШИХ МОДУЛЕЙ ---
import gemini_api 
from health_checker import start_health_check_server_in_thread # <-- ИЗМЕНЕНИЕ: Импортируем из модуля

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Получаем API ключи из переменных окружения
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# HEALTH_CHECK_PORT больше не определяется здесь, он должен быть в health_checker.py

# --- КОНСТАНТЫ ДЛЯ СОСТОЯНИЙ ДИАЛОГА ---
GET_TEXT_FOR_CORRECTION, CHOOSE_STYLE, DESCRIBE_ADDRESSEE, POST_PROCESSING_MENU = range(4)

# --- Код для Health Check сервера УДАЛЕН отсюда, так как он в health_checker.py ---

# --- Функции бота ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    keyboard = [[InlineKeyboardButton("Улучшить текст 📝", callback_data="start_correction")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)

async def request_text_for_correction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Отлично! Теперь, пожалуйста, отправь мне текст, который нужно переформулировать."
    )
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        logger.debug(f"Не удалось убрать клавиатуру из предыдущего сообщения: {e}")
    return GET_TEXT_FOR_CORRECTION

async def received_text_for_correction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_message = update.message.text
    logger.info(f"Получен текст для исправления от chat_id {update.effective_chat.id}: '{user_message}'")
    context.user_data['text_to_correct'] = user_message
    keyboard = [
        [InlineKeyboardButton("Деловой стиль", callback_data="style_business")],
        [InlineKeyboardButton("Учебный стиль", callback_data="style_academic")],
        [InlineKeyboardButton("Личное общение", callback_data="style_personal")],
        [InlineKeyboardButton("Упрощённый текст", callback_data="style_simplified")],
        # Оставляем твой вариант кнопки, который был в файле
        [InlineKeyboardButton("Я сам укажу адресата", callback_data="style_auto")], 
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Понял тебя! Получил следующий текст (первые 50 символов): \"{user_message[:50]}...\"\n\n"
        "Теперь, пожалуйста, выбери стиль для переформулирования:",
        reply_markup=reply_markup
    )
    return CHOOSE_STYLE

async def style_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    style_choice = query.data
    context.user_data['chosen_style'] = style_choice
    logger.info(f"Пользователь {query.from_user.id} выбрал стиль: {style_choice}")
    text_to_correct = context.user_data.get('text_to_correct')

    if not text_to_correct:
        await query.edit_message_text(text="Произошла ошибка: не найден исходный текст. Пожалуйста, начни заново: /start")
        return ConversationHandler.END

    if style_choice == "style_auto":
        await query.edit_message_text(
            text="Ты выбрал указать адресата самостоятельно.\n\n" # Изменено для соответствия кнопке
                 "Чтобы я мог лучше подобрать стиль, пожалуйста, опиши кратко, кому адресовано это сообщение "
                 "(например: 'начальнику', 'близкому другу', 'клиенту', 'учителю', 'в официальную инстанцию')."
        )
        return DESCRIBE_ADDRESSEE
    
    await query.edit_message_text(text=f"Ты выбрал стиль: {style_choice}. Минуточку, обрабатываю твой текст...")
    style_prompt_instruction = ""
    if style_choice == "style_business": style_prompt_instruction = "в официальном деловом стиле"
    elif style_choice == "style_academic": style_prompt_instruction = "в академическом или учебном стиле"
    elif style_choice == "style_personal": style_prompt_instruction = "в стиле личного, неформального общения"
    elif style_choice == "style_simplified": style_prompt_instruction = "максимально просто и понятно, упростив сложные конструкции"
    
    prompt_for_gemini = (
        f"Переформулируй следующий текст {style_prompt_instruction}, "
        f"сохраняя его первоначальный смысл: \"{text_to_correct}\"\n\n"
        "Предоставь только переформулированный текст без каких-либо дополнительных комментариев, "
        "вступлений или объяснений."
    )
    logger.info(f"Промпт для Gemini: {prompt_for_gemini}")

    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY не настроен для style_chosen.")
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Ошибка конфигурации сервиса для обработки стиля. Ключ API не найден.")
        return ConversationHandler.END
    
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        response_text = gemini_api.ask_gemini(prompt_for_gemini, GEMINI_API_KEY)
        
        # --- ИЗМЕНЕНИЯ ДЛЯ ПОСТОБРАБОТКИ ---
        context.user_data['last_gemini_response'] = response_text 

        post_process_keyboard_inline = [[
            InlineKeyboardButton("Мягче", callback_data="adjust_softer"),
            InlineKeyboardButton("Жестче", callback_data="adjust_harder"),
            InlineKeyboardButton("Формальнее", callback_data="adjust_more_formal"),
        ]]
        reply_markup_inline = InlineKeyboardMarkup(post_process_keyboard_inline)
        
        message_to_edit = f"Вот переформулированный текст:\n\n{response_text}\n\nКак тебе результат? Можем доработать:"
        
        if query.message:
            await context.bot.edit_message_text(
                text=message_to_edit,
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                reply_markup=reply_markup_inline
            )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=message_to_edit,
                reply_markup=reply_markup_inline
            )
        logger.info(f"Ответ от Gemini (стиль {style_choice}): {response_text}. Предложено меню постобработки.")
        return POST_PROCESSING_MENU # ПЕРЕХОДИМ В НОВОЕ СОСТОЯНИЕ

    except Exception as e:
        logger.error(f"Ошибка при вызове gemini_api.ask_gemini для стиля {style_choice}: {e}", exc_info=True)
        error_message_text = "К сожалению, произошла ошибка при обработке вашего запроса. Попробуйте позже."
        if query.message: await context.bot.edit_message_text(text=error_message_text, chat_id=query.message.chat_id, message_id=query.message.message_id)
        else: await context.bot.send_message(chat_id=update.effective_chat.id, text=error_message_text)
        return ConversationHandler.END

async def addressee_described(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    addressee_description = update.message.text
    logger.info(f"Получено описание адресата от chat_id {update.effective_chat.id}: '{addressee_description}'")
    context.user_data['addressee_description'] = addressee_description
    text_to_correct = context.user_data.get('text_to_correct')

    if not text_to_correct:
        await update.message.reply_text("Произошла ошибка: не найден исходный текст. Пожалуйста, начни заново: /start")
        return ConversationHandler.END

    await update.message.reply_text("Понял тебя! Подбираю стиль и переформулирую текст для твоего адресата. Минуточку...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    prompt_for_gemini = (
        f"Переформулируй следующий текст, автоматически подобрав наиболее подходящий стиль "
        f"для следующего адресата: '{addressee_description}'. "
        f"Сохраняй первоначальный смысл текста. Исходный текст: \"{text_to_correct}\"\n\n"
        "Предоставь только переформулированный текст без каких-либо дополнительных комментариев, "
        "вступлений или объяснений."
    )
    logger.info(f"Промпт для Gemini (автоопределение): {prompt_for_gemini}")

    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY не настроен для addressee_described.")
        await update.message.reply_text("Ошибка конфигурации сервиса для автоопределения стиля. Ключ API не найден.")
        return ConversationHandler.END
        
    try:
        response_text = gemini_api.ask_gemini(prompt_for_gemini, GEMINI_API_KEY)
        
        # --- ИЗМЕНЕНИЯ ДЛЯ ПОСТОБРАБОТКИ ---
        context.user_data['last_gemini_response'] = response_text

        post_process_keyboard_inline = [[
            InlineKeyboardButton("Мягче", callback_data="adjust_softer"),
            InlineKeyboardButton("Жестче", callback_data="adjust_harder"),
            InlineKeyboardButton("Формальнее", callback_data="adjust_more_formal"),
        ]]
        reply_markup_inline = InlineKeyboardMarkup(post_process_keyboard_inline)
        
        message_to_send = f"Вот переформулированный текст (стиль подобран автоматически для '{addressee_description}'):\n\n{response_text}\n\nКак тебе результат? Можем доработать:"
        
        await update.message.reply_text(text=message_to_send, reply_markup=reply_markup_inline)
        
        logger.info(f"Ответ от Gemini (автоопределение для '{addressee_description}'): {response_text}. Предложено меню постобработки.")
        return POST_PROCESSING_MENU # ПЕРЕХОДИМ В НОВОЕ СОСТОЯНИЕ

    except Exception as e:
        logger.error(f"Ошибка при вызове gemini_api.ask_gemini для автоопределения: {e}", exc_info=True)
        await update.message.reply_text("К сожалению, произошла ошибка при обработке вашего запроса с автоопределением стиля. Попробуйте позже.")
        return ConversationHandler.END

# --- НОВАЯ ФУНКЦИЯ-ОБРАБОТЧИК ДЛЯ ПОСТОБРАБОТКИ (ПОКА ЗАГЛУШКА) ---
async def post_processing_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Вызывается после того, как пользователь нажал на одну из кнопок постобработки.
    Пока что просто сообщает о выборе и завершает диалог.
    """
    query = update.callback_query
    await query.answer()
    
    action_choice = query.data # например, 'adjust_softer'
    # Мы не сохраняем 'post_process_action' в user_data, так как это одноразовое действие
    
    last_response = context.user_data.get('last_gemini_response', "К сожалению, предыдущий текст не найден.")
    
    logger.info(f"Пользователь {query.from_user.id} выбрал действие постобработки: {action_choice} для текста: \"{last_response[:30]}...\"")
    
    await query.edit_message_text(
        text=f"Ты выбрал действие: {action_choice} для текста: \"{last_response[:50]}...\".\n"
             "Эта функция пока не реализована. Завершаю диалог для теста."
    )
    return ConversationHandler.END # Пока что завершаем диалог

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Действие отменено. Если захочешь начать заново, просто нажми /start.")
    context.user_data.clear()
    return ConversationHandler.END

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # HEALTH_CHECK_PORT теперь определяется в health_checker.py, но для статуса можно его здесь упомянуть
    # или убрать упоминание порта, если это вызывает путаницу.
    # Для простоты, пока оставим так, но в идеале эта информация должна быть консистентна.
    # Можно передавать порт из health_checker или просто сказать, что health_check активен.
    health_check_port_display = context.bot_data.get('health_check_port', 'неизвестном') # Пример, как можно было бы хранить
    await update.message.reply_text(f"Бот онлайн и готов к работе! Health check сервер активен.")


def main() -> None:
    if not TELEGRAM_TOKEN:
        logger.critical("Переменная окружения TELEGRAM_TOKEN не найдена! Бот не может быть запущен.")
        return

    logger.info("Запуск основного приложения бота...")
    
    # --- ИЗМЕНЕНИЕ: Запускаем Health Check из модуля ---
    # health_thread = threading.Thread(target=run_health_check_server, daemon=True) # СТАРЫЙ КОД
    # health_thread.start() # СТАРЫЙ КОД
    start_health_check_server_in_thread() # НОВЫЙ КОД
    # Можно сохранить возвращаемый поток, если он нужен для чего-то, но для daemon=True это обычно не требуется.
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # --- ИЗМЕНЕНИЕ: Добавляем POST_PROCESSING_MENU в ConversationHandler ---
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(request_text_for_correction, pattern='^start_correction$')],
        states={
            GET_TEXT_FOR_CORRECTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_text_for_correction)],
            CHOOSE_STYLE: [CallbackQueryHandler(style_chosen, pattern='^style_(business|academic|personal|simplified|auto)$')],
            DESCRIBE_ADDRESSEE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addressee_described)],
            POST_PROCESSING_MENU: [ # НОВОЕ СОСТОЯНИЕ И ОБРАБОТЧИК
                CallbackQueryHandler(post_processing_action, pattern='^adjust_(softer|harder|more_formal)$')
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel_conversation)],
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    
    logger.info("Бот Telegram успешно настроен и запускается в режиме опроса...")
    try:
        application.run_polling()
    except Exception as e:
        logger.critical(f"Критическая ошибка при работе Telegram-бота: {e}", exc_info=True)
    finally:
        logger.info("Бот Telegram остановлен.")

if __name__ == "__main__":
    main()
