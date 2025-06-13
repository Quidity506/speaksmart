import logging
import os
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    PicklePersistence
)
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown

import gemini_api 
from health_checker import start_health_check_server_in_thread

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Получаем API ключи из переменных окружения
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# --- КОНСТАНТЫ ДЛЯ СОСТОЯНИЙ ДИАЛОГА ---
GET_TEXT_FOR_CORRECTION, CHOOSE_STYLE, DESCRIBE_ADDRESSEE, POST_PROCESSING_MENU = range(4)

# --- ОСНОВНАЯ КЛАВИАТУРА МЕНЮ ---
main_menu_layout = [
    [KeyboardButton("Новый текст")]
]
main_menu_keyboard = ReplyKeyboardMarkup(main_menu_layout, resize_keyboard=True, one_time_keyboard=False)

# --- Функции бота ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Отправляет приветственное сообщение и основную клавиатуру.
    """
    user_name = update.effective_user.first_name
    
    welcome_text = (
        f"Привет, {user_name}! Я SpeakSmartBot.\n"
        "Помогу сделать твой текст лучше. Чтобы начать, нажми кнопку 'Новый текст' в меню ниже 👇"
    )

    await update.message.reply_text(
        text=welcome_text,
        reply_markup=main_menu_keyboard
    )

async def start_new_text_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Запускается по нажатию кнопки "Новый текст" и является точкой входа в диалог.
    """
    context.user_data.clear()
    await update.message.reply_text(
        "Отлично! Теперь, пожалуйста, отправь мне текст, который нужно переформулировать."
    )
    return GET_TEXT_FOR_CORRECTION

async def received_text_for_correction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_message = update.message.text
    logger.info(f"Получен текст для исправления от chat_id {update.effective_chat.id}: '{user_message}'")
    context.user_data['text_to_correct'] = user_message
    context.user_data.pop('chosen_style', None)
    context.user_data.pop('addressee_description', None)
    context.user_data.pop('last_gemini_response', None)

    keyboard = [
        [InlineKeyboardButton("Деловой стиль 📑", callback_data="style_business")],
        [InlineKeyboardButton("Учебный стиль 📚", callback_data="style_academic")],
        [InlineKeyboardButton("Личное общение 👥", callback_data="style_personal")],
        [InlineKeyboardButton("Упрощённый текст ✂️", callback_data="style_simplified")],
        [InlineKeyboardButton("Я сам укажу адресата 🪄", callback_data="style_auto")], 
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Понял тебя! Получил следующий текст (первые 50 символов): \"{user_message[:50]}...\"\n\n"
        "Теперь, пожалуйста, выбери стиль для переформулирования:",
        reply_markup=reply_markup
    )
    return CHOOSE_STYLE

async def _send_post_processing_menu(update_or_query, context: ContextTypes.DEFAULT_TYPE, response_text: str, message_prefix: str):
    """Вспомогательная функция для отправки меню постобработки с детальным логированием."""
    context.user_data['last_gemini_response'] = response_text

    logger.info(f"--- _send_post_processing_menu ---")
    logger.info(f"ОРИГИНАЛЬНЫЙ message_prefix: [{message_prefix}]")
    logger.info(f"ОРИГИНАЛЬНЫЙ response_text от Gemini: [{response_text}]")

    processed_response_text = response_text.strip().replace('\n', ' ')
    logger.info(f"response_text ПОСЛЕ strip() и replace(): [{processed_response_text}]")

    escaped_response_text = escape_markdown(processed_response_text, version=2)
    logger.info(f"response_text ПОСЛЕ escape_markdown(): [{escaped_response_text}]")

    formatted_response_text = f"`{escaped_response_text}`"
    logger.info(f"formatted_response_text: [{formatted_response_text}]")

    escaped_message_prefix = escape_markdown(message_prefix, version=2)
    logger.info(f"message_prefix ПОСЛЕ escape_markdown(): [{escaped_message_prefix}]")

    post_process_keyboard_inline = [
        [
            InlineKeyboardButton("Мягче", callback_data="adjust_softer"),
            InlineKeyboardButton("Жестче", callback_data="adjust_harder"),
            InlineKeyboardButton("Формальнее", callback_data="adjust_more_formal"),
        ],
        [
            InlineKeyboardButton("Сгенерировать заново", callback_data="regenerate_text"),
        ]
    ]
    reply_markup_inline = InlineKeyboardMarkup(post_process_keyboard_inline)

    message_to_send = f"{escaped_message_prefix}\n\n{formatted_response_text}\n\nКак тебе результат? Можем доработать или нажми 'Новый текст' в меню для следующего запроса."
    logger.info(f"ИТОГОВОЕ message_to_send (первые 300 симв): [{message_to_send[:300]}]")

    try:
        target_message_for_edit = None
        chat_id_for_send = None

        if isinstance(update_or_query, CallbackQuery):
            target_message_for_edit = update_or_query.message
        elif isinstance(update_or_query, Update) and update_or_query.message:
            chat_id_for_send = update_or_query.effective_chat.id
        else:
            logger.error(f"Неожиданный тип объекта в _send_post_processing_menu: {type(update_or_query)}")
            if context.update and context.update.effective_chat:
                 await context.bot.send_message(chat_id=context.update.effective_chat.id, text="Произошла ошибка отображения результата (внутренняя).")
            return

        if target_message_for_edit:
            logger.info(f"Попытка отредактировать сообщение ID: {target_message_for_edit.message_id} в чате ID: {target_message_for_edit.chat_id}")
            await context.bot.edit_message_text(
                text=message_to_send,
                chat_id=target_message_for_edit.chat_id,
                message_id=target_message_for_edit.message_id,
                reply_markup=reply_markup_inline,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        elif chat_id_for_send:
            logger.info(f"Попытка отправить новое сообщение в чат ID: {chat_id_for_send}")
            await context.bot.send_message(
                chat_id=chat_id_for_send,
                text=message_to_send,
                reply_markup=reply_markup_inline,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
             logger.error("Не удалось определить chat_id или message для отправки/редактирования в _send_post_processing_menu")
             if context.update and context.update.effective_chat:
                await context.bot.send_message(chat_id=context.update.effective_chat.id, text="Критическая ошибка отображения результата.")

    except telegram.error.BadRequest as e:
        logger.error(f"!!! BadRequest при отправке/редактировании сообщения в _send_post_processing_menu: {e}", exc_info=True)
        logger.error(f"Проблемный message_to_send (первые 500 симв): [{message_to_send[:500]}]")
        chat_id_to_notify = None
        if isinstance(update_or_query, CallbackQuery): chat_id_to_notify = update_or_query.from_user.id
        elif isinstance(update_or_query, Update) and update_or_query.effective_chat: chat_id_to_notify = update_or_query.effective_chat.id
        if chat_id_to_notify:
            await context.bot.send_message(chat_id=chat_id_to_notify, text=f"Произошла ошибка при отображении меню доработки (Ошибка Markdown: {e.message}). Попробуйте другой текст или действие.")

    except Exception as e:
        logger.error(f"Другая ошибка при отправке/редактировании сообщения в _send_post_processing_menu: {e}", exc_info=True)
        chat_id_to_notify = None
        if isinstance(update_or_query, CallbackQuery): chat_id_to_notify = update_or_query.from_user.id
        elif isinstance(update_or_query, Update) and update_or_query.effective_chat: chat_id_to_notify = update_or_query.effective_chat.id
        if chat_id_to_notify:
            await context.bot.send_message(chat_id=chat_id_to_notify, text="Произошла серьезная ошибка при отображении меню доработки.")

async def style_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    style_choice = query.data
    context.user_data['chosen_style'] = style_choice 
    context.user_data.pop('addressee_description', None) 
    logger.info(f"Пользователь {query.from_user.id} выбрал стиль: {style_choice}")
    text_to_correct = context.user_data.get('text_to_correct')

    if not text_to_correct:
        await query.edit_message_text(text="Произошла ошибка: не найден исходный текст. Пожалуйста, начни заново, нажав 'Новый текст'.")
        return ConversationHandler.END

    if style_choice == "style_auto":
        await query.edit_message_text(
            text="Ты выбрал указать адресата самостоятельно.\n\n"
                 "Чтобы я мог лучше подобрать стиль, пожалуйста, опиши кратко, кому адресовано это сообщение "
                 "(например: 'начальнику', 'близкому другу', 'клиенту', 'учителю', 'в официальную инстанцию')."
        )
        return DESCRIBE_ADDRESSEE
    
    await query.edit_message_text(text=f"Ты выбрал стиль: {style_choice}. Минуточку, обрабатываю твой текст...")
    
    style_prompt_instruction = ""
    # ... (Этот блок кода не менялся, так как он был правильным)
    
    prompt_for_gemini = (
        f"..." # Сокращено для краткости
    )
    logger.info(f"Промпт для Gemini (прямой стиль): {prompt_for_gemini[:500]}...")

    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY не настроен для style_chosen.")
        await query.edit_message_text(text="Ошибка конфигурации сервиса. Попробуйте позже.")
        return ConversationHandler.END
    
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        response_text = gemini_api.ask_gemini(prompt_for_gemini, GEMINI_API_KEY)
        await _send_post_processing_menu(query, context, response_text, "Вот переформулированный текст:")
        return POST_PROCESSING_MENU
    except Exception as e:
        logger.error(f"Ошибка при вызове gemini_api.ask_gemini для стиля {style_choice}: {e}", exc_info=True)
        await query.edit_message_text(
            text="К сожалению, произошла ошибка при обработке вашего запроса. Попробуйте еще раз, нажав 'Новый текст'."
        )
        return ConversationHandler.END

async def addressee_described(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    addressee_description = update.message.text
    logger.info(f"Получено описание адресата от chat_id {update.effective_chat.id}: '{addressee_description}'")
    context.user_data['addressee_description'] = addressee_description
    context.user_data['chosen_style'] = 'style_auto'
    text_to_correct = context.user_data.get('text_to_correct')

    if not text_to_correct:
        await update.message.reply_text("Произошла ошибка: не найден исходный текст. Пожалуйста, начни заново, нажав 'Новый текст'.")
        return ConversationHandler.END

    await update.message.reply_text("Понял тебя! Подбираю стиль и переформулирую текст для твоего адресата. Минуточку...")
    
    # ... (Этот блок кода не менялся) ...
    prompt_for_gemini = f"""...""" # Сокращено для краткости

    logger.info(f"Промпт для Gemini (автоопределение): {prompt_for_gemini[:500]}...")

    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY не настроен для addressee_described.")
        await update.message.reply_text("Ошибка конфигурации сервиса. Попробуйте позже.")
        return ConversationHandler.END
        
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        response_text = gemini_api.ask_gemini(prompt_for_gemini, GEMINI_API_KEY)
        await _send_post_processing_menu(update, context, response_text, f"Вот переформулированный текст (стиль подобран автоматически для '{addressee_description}'):")
        return POST_PROCESSING_MENU
    except Exception as e:
        logger.error(f"Ошибка при вызове gemini_api.ask_gemini для автоопределения: {e}", exc_info=True)
        await update.message.reply_text("К сожалению, произошла ошибка при обработке вашего запроса. Попробуйте позже.")
        return ConversationHandler.END

async def post_processing_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    action_choice = query.data
    logger.info(f"Пользователь {query.from_user.id} выбрал действие: {action_choice}")

    last_response = context.user_data.get('last_gemini_response')
    original_text = context.user_data.get('text_to_correct')
    chosen_style_callback = context.user_data.get('chosen_style')
    addressee_description_if_auto = context.user_data.get('addressee_description')

    prompt_for_gemini = ""
    final_message_prefix = ""

    if action_choice in ["adjust_softer", "adjust_harder", "adjust_more_formal"]:
        if not last_response:
            logger.warning(f"Не найден last_gemini_response для {action_choice}")
            await query.edit_message_text(text="Ошибка: текст для доработки не найден. Начните заново, нажав 'Новый текст'.")
            return ConversationHandler.END
        # ... (Этот блок кода не менялся) ...

    elif action_choice == "regenerate_text":
        if not original_text:
            logger.warning(f"Не найден original_text для regenerate_text")
            await query.edit_message_text(text="Ошибка: исходный текст для повторной генерации не найден. Начните заново, нажав 'Новый текст'.")
            return ConversationHandler.END
        # ... (Этот блок кода не менялся) ...

    else:
        logger.warning(f"Неизвестное действие в post_processing_action: {action_choice}")
        await query.edit_message_text(text=f"Неизвестное действие: {action_choice}. Завершаю диалог.")
        return ConversationHandler.END

    logger.info(f"Промпт для Gemini ({action_choice}): {prompt_for_gemini[:500]}...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    if not GEMINI_API_KEY:
        logger.error(f"GEMINI_API_KEY не настроен для post_processing_action ({action_choice}).")
        await query.edit_message_text(text="Ошибка конфигурации сервиса. Ключ API не найден.")
        return ConversationHandler.END

    try:
        new_response_text = gemini_api.ask_gemini(prompt_for_gemini, GEMINI_API_KEY)
        await _send_post_processing_menu(query, context, new_response_text, final_message_prefix)
        return POST_PROCESSING_MENU
    except Exception as e:
        logger.error(f"Ошибка при вызове gemini_api.ask_gemini в post_processing_action ({action_choice}): {e}", exc_info=True)
        await query.edit_message_text(
            text="К сожалению, произошла ошибка при доработке/генерации текста. Попробуйте еще раз."
        )
        return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершает диалог, очищает данные пользователя."""
    user_name = update.effective_user.first_name
    await update.message.reply_text(
        f"Все действия отменены, {user_name}. Чтобы начать заново, нажми 'Новый текст'.", 
        reply_markup=main_menu_keyboard
    )
    context.user_data.clear()
    return ConversationHandler.END

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Бот онлайн и готов к работе! Health check сервер активен.")

def main() -> None:
    if not TELEGRAM_TOKEN:
        logger.critical("Переменная окружения TELEGRAM_TOKEN не найдена! Бот не может быть запущен.")
        return

    logger.info("Запуск основного приложения бота...")
    start_health_check_server_in_thread()
    
    persistence = PicklePersistence(filepath="bot_persistence")
    
    application = Application.builder().token(TELEGRAM_TOKEN).persistence(persistence).build()
    
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Новый текст$"), start_new_text_entry)],
        states={
            GET_TEXT_FOR_CORRECTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_text_for_correction)],
            CHOOSE_STYLE: [CallbackQueryHandler(style_chosen, pattern='^style_(business|academic|personal|simplified|auto)$')],
            DESCRIBE_ADDRESSEE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addressee_described)],
            POST_PROCESSING_MENU: [ 
                CallbackQueryHandler(post_processing_action, pattern='^(adjust_(softer|harder|more_formal)|regenerate_text)$')
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel_conversation)],
        persistent=True,
        name="main_conversation"
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    # Команда /cancel теперь обрабатывается только внутри ConversationHandler, поэтому отдельный хендлер не нужен
    
    logger.info("Бот Telegram успешно настроен и запускается в режиме опроса...")
    try:
        application.run_polling()
    except Exception as e:
        logger.critical(f"Критическая ошибка при работе Telegram-бота: {e}", exc_info=True)
    finally:
        logger.info("Бот Telegram остановлен.")

if __name__ == "__main__":
    main()