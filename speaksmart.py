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
    CallbackQueryHandler
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
    [KeyboardButton("/start"), KeyboardButton("/cancel")]
]
# one_time_keyboard=False означает, что клавиатура будет постоянной, пока ее не сменит другая
# resize_keyboard=True делает кнопки более компактными
main_menu_keyboard = ReplyKeyboardMarkup(main_menu_layout, resize_keyboard=True, one_time_keyboard=False)

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
        "🔹 После переформулирования, ты сможешь дополнительно скорректировать тон (например, сделать текст мягче или строже), сгенерировать вариант заново или начать работу с новым текстом.\n\n"
        "Готов начать?"
    )
    # Инлайн-кнопка для начала основного диалога
    inline_buttons_for_flow = [[InlineKeyboardButton("Улучшить текст 📝", callback_data="start_correction")]]
    inline_markup_for_flow = InlineKeyboardMarkup(inline_buttons_for_flow)

    # Отправляем приветственное сообщение с инлайн-кнопкой
    await update.message.reply_text(
        text=welcome_text,
        reply_markup=inline_markup_for_flow 
    )
    
    # Отправляем сообщение для установки ReplyKeyboardMarkup (основного меню)
    # Это сообщение может быть и другим, например, просто "Главное меню:"
    # Важно, что оно отправляется с main_menu_keyboard
    await update.message.reply_text(
        "Для быстрого доступа к основным командам используйте меню 👇 (оно может быть скрыто под значком 'Меню' рядом с полем ввода).",
        reply_markup=main_menu_keyboard
    )

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
    context.user_data.pop('chosen_style', None)
    context.user_data.pop('addressee_description', None)
    context.user_data.pop('last_gemini_response', None)

    keyboard = [
        [InlineKeyboardButton("Деловой стиль", callback_data="style_business")],
        [InlineKeyboardButton("Учебный стиль", callback_data="style_academic")],
        [InlineKeyboardButton("Личное общение", callback_data="style_personal")],
        [InlineKeyboardButton("Упрощённый текст", callback_data="style_simplified")],
        [InlineKeyboardButton("Я сам укажу адресата", callback_data="style_auto")], 
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
    
    processed_response_text = response_text.strip()
    logger.info(f"response_text ПОСЛЕ strip(): [{processed_response_text}]")
    
    escaped_response_text = escape_markdown(processed_response_text, version=2)
    logger.info(f"response_text ПОСЛЕ escape_markdown(): [{escaped_response_text}]")
    
    # Убираем \n перед последними ```, чтобы не было лишней пустой строки ВНУТРИ блока.
    formatted_response_text = f"```\n{escaped_response_text}```" 
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
            InlineKeyboardButton("Новый текст", callback_data="start_new_text"),
        ]
    ]
    reply_markup_inline = InlineKeyboardMarkup(post_process_keyboard_inline)
    
    message_to_send = f"{escaped_message_prefix}\n\n{formatted_response_text}\n\nКак тебе результат? Можем доработать:"
    logger.info(f"ИТОГОВОЕ message_to_send (первые 300 симв): [{message_to_send[:300]}]")
    
    try:
        target_message_for_edit = None
        chat_id_for_send = None

        if isinstance(update_or_query, CallbackQuery):
            target_message_for_edit = update_or_query.message
            # chat_id_for_send = update_or_query.effective_chat.id # Не используется если target_message_for_edit есть
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
             if context.update and context.update.effective_chat: # Последняя попытка уведомить
                await context.bot.send_message(chat_id=context.update.effective_chat.id, text="Критическая ошибка отображения результата.")


    except telegram.error.BadRequest as e: # Ловим конкретно BadRequest
        logger.error(f"!!! BadRequest при отправке/редактировании сообщения в _send_post_processing_menu: {e}", exc_info=True)
        logger.error(f"Проблемный message_to_send (первые 500 симв): [{message_to_send[:500]}]") # Логируем проблемное сообщение
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
        await query.edit_message_text(text="Произошла ошибка: не найден исходный текст. Пожалуйста, начни заново: /start")
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
    if style_choice == "style_business": 
        style_prompt_instruction = """При переформулировании придерживайся следующих принципов делового стиля:
1.  **Обеспечь четкость и структуру:** Устрани из текста всё лишнее. Сделай высказывание максимально логичным, последовательным и целенаправленным.
2.  **Придай официальный тон:** Используй деловую лексику. Полностью избегай эмоциональности. При необходимости добавь нейтральные вводные конструкции и стандартные деловые формулировки.
3.  **Сфокусируйся на цели сообщения:** Подчеркни ключевую мысль, просьбу или предложение. Адаптируй текст так, чтобы он был уместен для контекста общения с коллегами, руководством или деловыми партнерами."""
    elif style_choice == "style_academic":
        style_prompt_instruction = """При переформулировании придерживайся следующих принципов учебного (академического) стиля:
1.  **Обеспечь логичность и точность:** Улучши последовательность аргументов в тексте. Добавь четкие смысловые связки между частями текста для лучшей структуры.
2.  **Соблюдай нейтральную академичность:** Используй соответствующую научную или учебную терминологию. Убери разговорные элементы и просторечия. Избегай эмоциональной окраски высказываний.
3.  **Выдели ключевые понятия:** Усиливай внимание на терминах, теориях, концепциях или аргументах, которые являются важными для учебной или научной задачи, изложенной в тексте."""
    elif style_choice == "style_personal": 
        style_prompt_instruction = """При переформулировании придерживайся следующих принципов стиля для личного общения:
1.  **Улучши логику и структуру, сохраняя личный характер:** Сделай текст более связным. При необходимости добавь уместные эмоциональные переходы и расставь смысловые акценты, не теряя при этом индивидуальный стиль автора.
2.  **Усиль эмоциональность и живость:** Помоги тексту звучать искренне и «по-человечески». Если это уместно, можно усилить выражение чувств, добавить или скорректировать обращения и использование местоимений, чтобы лучше передать личный тон.
3.  **Сохраняй стиль автора:** Не заменяй характерные для автора индивидуальные выражения. По возможности сохраняй сленг и личные обороты речи, если они присутствуют, фокусируясь на усилении общей подачи и выразительности текста, а не на полной переделке стиля."""
    elif style_choice == "style_simplified": 
        style_prompt_instruction = """При переформулировании придерживайся следующих принципов упрощения текста:
1.  **Обеспечь простую структуру:** Старайся делать предложения короче. Если изначальный текст звучал запутанно, его порядок изложения мыслей может быть перестроен для большей логичности и ясности.
2.  **Используй понятную лексику:** Заменяй сложные термины и узкоспециализированные слова на более простые и общеупотребительные аналоги, при этом полностью сохраняя исходный смысл. Ключевые факты и информация должны быть сохранены.
3.  **Сделай подачу доступной:** Убирай из текста перегруженные грамматические конструкции (например, сложные причастные и деепричастные обороты, чрезмерное количество вводных слов). Текст должен читаться легко, но все важные детали и факты исходного сообщения должны остаться."""
    
    prompt_for_gemini = (
        f"Твоя задача: внимательно и аккуратно переформулировать следующий исходный текст. "
        f"{style_prompt_instruction}"
        f"КРИТИЧЕСКИ ВАЖНО: Первоначальный и полный смысл исходного текста должен быть сохранен АБСОЛЮТНО ТОЧНО, без малейших искажений, потерь ключевой информации или добавления нового смысла. "
        f"Исходный текст для переформулирования: \"{text_to_correct}\"\n\n"
        f"Твой ответ должен содержать ИСКЛЮЧИТЕЛЬНО и ТОЛЬКО переформулированный текст. "
        f"НЕ ДОБАВЛЯЙ никаких приветствий, вступлений, объяснений своих действий, извинений, комментариев, послесловий или каких-либо других фраз, кроме самого переформулированного текста."
    )
    logger.info(f"Промпт для Gemini (прямой стиль): {prompt_for_gemini}")

    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY не настроен для style_chosen.")
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Ошибка конфигурации сервиса для обработки стиля. Ключ API не найден.")
        return ConversationHandler.END
    
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        response_text = gemini_api.ask_gemini(prompt_for_gemini, GEMINI_API_KEY)
        await _send_post_processing_menu(query, context, response_text, "Вот переформулированный текст:")
        logger.info(f"Ответ от Gemini (стиль {style_choice}): {response_text}. Предложено меню постобработки.")
        return POST_PROCESSING_MENU
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
    context.user_data['chosen_style'] = 'style_auto' # Явно сохраняем, что это был авто-стиль
    text_to_correct = context.user_data.get('text_to_correct')

    if not text_to_correct:
        await update.message.reply_text("Произошла ошибка: не найден исходный текст. Пожалуйста, начни заново: /start")
        return ConversationHandler.END

    await update.message.reply_text("Понял тебя! Подбираю стиль и переформулирую текст для твоего адресата. Минуточку...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
# Этот промпт будет использоваться в функции addressee_described, когда пользователь выбирает автоопределение стиля.

    prompt_for_gemini = f"""Твоя задача – переформулировать исходный текст, автоматически подобрав для него наиболее подходящий стиль, учитывая, что сообщение адресовано: '{addressee_description}'.
    Проанализируй исходный текст и описание адресата. Определи, какой из следующих четырех стилей (Деловой, Учебный, Личное общение, Упрощение текста) является наиболее подходящим для данной ситуации.
    После того как ты определишь наиболее подходящий стиль, переформулируй исходный текст, СТРОГО следуя ИСКЛЮЧИТЕЛЬНО инструкциям для ВЫБРАННОГО ТОБОЙ стиля.
    Вот детальные инструкции для каждого стиля:
    ---
    ИНСТРУКЦИИ ДЛЯ ДЕЛОВОГО СТИЛЯ:
    1.  **Обеспечь четкость и структуру:** Устрани из текста всё лишнее. Сделай высказывание максимально логичным, последовательным и целенаправленным.
    2.  **Придай официальный тон:** Используй деловую лексику. Полностью избегай эмоциональности. При необходимости добавь нейтральные вводные конструкции и стандартные деловые формулировки.
    3.  **Сфокусируйся на цели сообщения:** Подчеркни ключевую мысль, просьбу или предложение. Адаптируй текст так, чтобы он был уместен для контекста общения с коллегами, руководством или деловыми партнерами.
    ---
    ИНСТРУКЦИИ ДЛЯ УЧЕБНОГО (АКАДЕМИЧЕСКОГО) СТИЛЯ:
    1.  **Обеспечь логичность и точность:** Улучши последовательность аргументов в тексте. Добавь четкие смысловые связки между частями текста для лучшей структуры.
    2.  **Соблюдай нейтральную академичность:** Используй соответствующую научную или учебную терминологию. Убери разговорные элементы и просторечия. Избегай эмоциональной окраски высказываний.
    3.  **Выдели ключевые понятия:** Усиливай внимание на терминах, теориях, концепциях или аргументах, которые являются важными для учебной или научной задачи, изложенной в тексте.
    ---
    ИНСТРУКЦИИ ДЛЯ СТИЛЯ ЛИЧНОГО ОБЩЕНИЯ:
    1.  **Улучши логику и структуру, сохраняя личный характер:** Сделай текст более связным. При необходимости добавь уместные эмоциональные переходы и расставь смысловые акценты, не теряя при этом индивидуальный стиль автора.
    2.  **Усиль эмоциональность и живость:** Помоги тексту звучать искренне и «по-человечески». Если это уместно, можно усилить выражение чувств, добавить или скорректировать обращения и использование местоимений, чтобы лучше передать личный тон.
    3.  **Сохраняй стиль автора:** Не заменяй характерные для автора индивидуальные выражения. По возможности сохраняй сленг и личные обороты речи, если они присутствуют, фокусируясь на усилении общей подачи и выразительности текста, а не на полной переделке стиля.
    ---
    ИНСТРУКЦИИ ДЛЯ УПРОЩЕНИЯ ТЕКСТА:
    1.  **Обеспечь простую структуру:** Старайся делать предложения короче. Если изначальный текст звучал запутанно, его порядок изложения мыслей может быть перестроен для большей логичности и ясности.
    2.  **Используй понятную лексику:** Заменяй сложные термины и узкоспециализированные слова на более простые и общеупотребительные аналоги, при этом полностью сохраняя исходный смысл. Ключевые факты и информация должны быть сохранены.
    3.  **Сделай подачу доступной:** Убирай из текста перегруженные грамматические конструкции (например, сложные причастные и деепричастные обороты, чрезмерное количество вводных слов). Текст должен читаться легко, но все важные детали и факты исходного сообщения должны остаться.
    ---

    Если ты не можешь с высокой уверенностью определить один из этих четырех стилей на основе описания адресата ('{addressee_description}') и исходного текста, сделай переформулированный текст максимально нейтральным и обезличенным, без явных обращений и излишних эмоций, но при этом понятным, логичным и сохраняющим всю суть исходного сообщения.
    КРИТИЧЕСКИ ВАЖНО: Первоначальный и полный смысл исходного текста должен быть сохранен АБСОЛЮТНО ТОЧНО, без малейших искажений, потерь ключевой информации или добавления нового смысла.
    Исходный текст для переформулирования: "{text_to_correct}"

    Твой ответ должен содержать ИСКЛЮЧИТЕЛЬНО и ТОЛЬКО переформулированный текст.
    НЕ ДОБАВЛЯЙ никаких приветствий, вступлений, объяснений своих действий, извинений, комментариев, послесловий или каких-либо других фраз, кроме самого переформулированного текста."""

    logger.info(f"Промпт для Gemini (автоопределение): {prompt_for_gemini}")

    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY не настроен для addressee_described.")
        await update.message.reply_text("Ошибка конфигурации сервиса для автоопределения стиля. Ключ API не найден.")
        return ConversationHandler.END
        
    try:
        response_text = gemini_api.ask_gemini(prompt_for_gemini, GEMINI_API_KEY)
        await _send_post_processing_menu(update, context, response_text, f"Вот переформулированный текст (стиль подобран автоматически для '{addressee_description}'):")
        logger.info(f"Ответ от Gemini (автоопределение для '{addressee_description}'): {response_text}. Предложено меню постобработки.")
        return POST_PROCESSING_MENU
    except Exception as e:
        logger.error(f"Ошибка при вызове gemini_api.ask_gemini для автоопределения: {e}", exc_info=True)
        await update.message.reply_text("К сожалению, произошла ошибка при обработке вашего запроса с автоопределением стиля. Попробуйте позже.")
        return ConversationHandler.END

async def post_processing_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    action_choice = query.data
    logger.info(f"Пользователь {query.from_user.id} выбрал действие: {action_choice}")

    if action_choice == "start_new_text":
        context.user_data.clear()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Отлично! Теперь, пожалуйста, отправь мне текст, который нужно переформулировать."
        )
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception as e:
            logger.debug(f"Не удалось убрать клавиатуру из сообщения постобработки: {e}")
        return GET_TEXT_FOR_CORRECTION

    last_response = context.user_data.get('last_gemini_response')
    original_text = context.user_data.get('text_to_correct')
    chosen_style_callback = context.user_data.get('chosen_style')
    addressee_description_if_auto = context.user_data.get('addressee_description')

    prompt_for_gemini = ""
    final_message_prefix = ""

    # --- НАЧАЛО БЛОКА ИЗМЕНЕНИЙ ДЛЯ "МЯГЧЕ", "ЖЕСТЧЕ", "ФОРМАЛЬНЕЕ" ---
    if action_choice in ["adjust_softer", "adjust_harder", "adjust_more_formal"]:
        if not last_response:
            logger.warning(f"Не найден last_gemini_response для {action_choice}")
            await query.edit_message_text(text="Ошибка: текст для доработки не найден. Начните заново: /start")
            return ConversationHandler.END

        instruction_verb_for_status_update = ""
        modification_instruction_for_gemini = ""

        if action_choice == "adjust_softer":
            instruction_verb_for_status_update = "смягчение тона"
            final_message_prefix = "Готово! Сделал текст немного мягче:"
            if chosen_style_callback == "style_business":
                modification_instruction_for_gemini = "Сделай следующий текст немного мягче, придав ему больше вежливости и дипломатичности, возможно, выразив поддержку, но сохраняя общий деловой контекст."
            elif chosen_style_callback == "style_academic":
                modification_instruction_for_gemini = "Сделай следующий текст немного мягче, добавив элементы дружелюбия или мотивации, возможно, предложив пояснения, но сохраняя академическую основу."
            elif chosen_style_callback == "style_personal":
                modification_instruction_for_gemini = "Сделай следующий текст более теплым, доверительным и эмоционально близким."
            elif chosen_style_callback == "style_simplified":
                modification_instruction_for_gemini = "Сделай следующий текст мягче, придав ему простой, приятный и более 'живой' разговорный оттенок, но сохраняя ясность."
            elif chosen_style_callback == "style_auto":
                modification_instruction_for_gemini = f"Сделай следующий текст немного мягче по тону, учитывая, что он был автоматически подобран для адресата: '{addressee_description_if_auto if addressee_description_if_auto else 'не указан'}'. Постарайся сохранить общую адекватность стиля для этого адресата."
            else: # Общий случай
                modification_instruction_for_gemini = "Сделай следующий текст немного мягче по тону."

        elif action_choice == "adjust_harder":
            instruction_verb_for_status_update = "увеличение жесткости/настойчивости тона"
            final_message_prefix = "Есть! Текст стал более настойчивым:"
            if chosen_style_callback == "style_business":
                modification_instruction_for_gemini = "Сделай следующий текст более жестким и настойчивым, возможно, добавив строгие указания или упомянув жесткие сроки, сохраняя деловой контекст."
            elif chosen_style_callback == "style_academic":
                modification_instruction_for_gemini = "Сделай следующий текст более жестким, акцентируя внимание на фактах и уменьшая количество размышлений, но сохраняя академическую основу."
            elif chosen_style_callback == "style_personal":
                modification_instruction_for_gemini = "Сделай следующий текст более решительным и прямым."
            elif chosen_style_callback == "style_simplified":
                modification_instruction_for_gemini = "Сделай следующий текст более жестким, обеспечив краткость и ясность без излишних смягчений."
            elif chosen_style_callback == "style_auto":
                modification_instruction_for_gemini = f"Сделай следующий текст немного жестче или более настойчивым по тону, учитывая, что он был автоматически подобран для адресата: '{addressee_description_if_auto if addressee_description_if_auto else 'не указан'}'. Постарайся сохранить общую адекватность стиля для этого адресата."
            else: # Общий случай
                modification_instruction_for_gemini = "Сделай следующий текст немного жестче или более настойчивым по тону."

        elif action_choice == "adjust_more_formal":
            instruction_verb_for_status_update = "увеличение формальности стиля"
            final_message_prefix = "Пожалуйста! Теперь текст более формальный:"
            if chosen_style_callback == "style_business":
                modification_instruction_for_gemini = "Сделай следующий текст еще более формальным, усилив профессионализм и использование специфической терминологии, характерной для делового стиля."
            elif chosen_style_callback == "style_academic":
                modification_instruction_for_gemini = "Сделай следующий текст еще более формальным, усилив его академичность, точность формулировок и структуру, характерную для научного или учебного стиля."
            elif chosen_style_callback == "style_personal":
                modification_instruction_for_gemini = "Сделай следующий текст немного более формальным, придав ему более сдержанный и вежливый тон, но стараясь сохранить личный характер общения."
            elif chosen_style_callback == "style_simplified":
                modification_instruction_for_gemini = "Сделай следующий текст более формальным, убирая излишне разговорные элементы и приближая речь к более правильной и нейтральной, но сохраняя его простоту и доступность."
            elif chosen_style_callback == "style_auto":
                 modification_instruction_for_gemini = f"Сделай следующий текст более формальным, учитывая, что он был автоматически подобран для адресата: '{addressee_description_if_auto if addressee_description_if_auto else 'не указан'}'. Постарайся сохранить общую адекватность стиля для этого адресата."
            else: # Общий случай
                modification_instruction_for_gemini = "Сделай следующий текст еще более формальным."

        await query.edit_message_text(text=f"Применяю '{instruction_verb_for_status_update}'... Минуточку.")

        prompt_for_gemini = (
            f"{modification_instruction_for_gemini} "
            f"КРИТИЧЕСКИ ВАЖНО: Первоначальный и полный смысл текста должен быть сохранен АБСОЛЮТНО ТОЧНО, без малейших искажений или потерь ключевой информации. "
            f"Вот текст для модификации: \"{last_response}\"\n\n"
            f"Твой ответ должен содержать ИСКЛЮЧИТЕЛЬНО и ТОЛЬКО измененный текст. "
            f"НЕ ДОБАВЛЯЙ никаких приветствий, вступлений, объяснений своих действий, извинений, комментариев, послесловий или каких-либо других фраз, кроме самого измененного текста."
        )
    # --- КОНЕЦ БЛОКА ИЗМЕНЕНИЙ ---

    elif action_choice == "regenerate_text": # ЭТОТ БЛОК ОСТАЕТСЯ БЕЗ ИЗМЕНЕНИЙ (КАК В speaksmart_py_v4_targeted_adj_fix)
        if not original_text:
            logger.warning(f"Не найден original_text для regenerate_text")
            await query.edit_message_text(text="Ошибка: исходный текст для повторной генерации не найден. Начните заново: /start")
            return ConversationHandler.END

        await query.edit_message_text(text="Генерирую новый вариант на основе первоначальных данных... Минуточку.")

        if chosen_style_callback == "style_auto" and addressee_description_if_auto:
            style_business_instr = """При переформулировании придерживайся следующих принципов делового стиля:
1.  **Обеспечь четкость и структуру:** Устрани из текста всё лишнее. Сделай высказывание максимально логичным, последовательным и целенаправленным.
2.  **Придай официальный тон:** Используй деловую лексику. Полностью избегай эмоциональности. При необходимости добавь нейтральные вводные конструкции и стандартные деловые формулировки.
3.  **Сфокусируйся на цели сообщения:** Подчеркни ключевую мысль, просьбу или предложение. Адаптируй текст так, чтобы он был уместен для контекста общения с коллегами, руководством или деловыми партнерами."""
            style_academic_instr = """При переформулировании придерживайся следующих принципов учебного (академического) стиля:
1.  **Обеспечь логичность и точность:** Улучши последовательность аргументов в тексте. Добавь четкие смысловые связки между частями текста для лучшей структуры.
2.  **Соблюдай нейтральную академичность:** Используй соответствующую научную или учебную терминологию. Убери разговорные элементы и просторечия. Избегай эмоциональной окраски высказываний.
3.  **Выдели ключевые понятия:** Усиливай внимание на терминах, теориях, концепциях или аргументах, которые являются важными для учебной или научной задачи, изложенной в тексте."""
            style_personal_instr = """При переформулировании придерживайся следующих принципов стиля для личного общения:
1.  **Улучши логику и структуру, сохраняя личный характер:** Сделай текст более связным. При необходимости добавь уместные эмоциональные переходы и расставь смысловые акценты, не теряя при этом индивидуальный стиль автора.
2.  **Усиль эмоциональность и живость:** Помоги тексту звучать искренне и «по-человечески». Если это уместно, можно усилить выражение чувств, добавить или скорректировать обращения и использование местоимений, чтобы лучше передать личный тон.
3.  **Сохраняй стиль автора:** Не заменяй характерные для автора индивидуальные выражения. По возможности сохраняй сленг и личные обороты речи, если они присутствуют, фокусируясь на усилении общей подачи и выразительности текста, а не на полной переделке стиля."""
            style_simplified_instr = """При переформулировании придерживайся следующих принципов упрощения текста:
1.  **Обеспечь простую структуру:** Старайся делать предложения короче. Если изначальный текст звучал запутанно, его порядок изложения мыслей может быть перестроен для большей логичности и ясности.
2.  **Используй понятную лексику:** Заменяй сложные термины и узкоспециализированные слова на более простые и общеупотребительные аналоги, при этом полностью сохраняя исходный смысл. Ключевые факты и информация должны быть сохранены.
3.  **Сделай подачу доступной:** Убирай из текста перегруженные грамматические конструкции (например, сложные причастные и деепричастные обороты, чрезмерное количество вводных слов). Текст должен читаться легко, но все важные детали и факты исходного сообщения должны остаться."""

            prompt_for_gemini = f"""Твоя задача – переформулировать исходный текст, автоматически подобрав для него наиболее подходящий стиль, учитывая, что сообщение адресовано: '{addressee_description_if_auto}'.

Проанализируй исходный текст и описание адресата. Определи, какой из следующих четырех стилей (Деловой, Учебный, Личное общение, Упрощение текста) является наиболее подходящим для данной ситуации.

После того как ты определишь наиболее подходящий стиль, переформулируй исходный текст, СТРОГО следуя ИСКЛЮЧИТЕЛЬНО инструкциям для ВЫБРАННОГО ТОБОЙ стиля.

Вот детальные инструкции для каждого стиля:

---
ИНСТРУКЦИИ ДЛЯ ДЕЛОВОГО СТИЛЯ:
{style_business_instr}
---
ИНСТРУКЦИИ ДЛЯ УЧЕБНОГО (АКАДЕМИЧЕСКОГО) СТИЛЯ:
{style_academic_instr}
---
ИНСТРУКЦИИ ДЛЯ СТИЛЯ ЛИЧНОГО ОБЩЕНИЯ:
{style_personal_instr}
---
ИНСТРУКЦИИ ДЛЯ УПРОЩЕНИЯ ТЕКСТА:
{style_simplified_instr}
---

Если ты не можешь с высокой уверенностью определить один из этих четырех стилей на основе описания адресата ('{addressee_description_if_auto}') и исходного текста, сделай переформулированный текст максимально нейтральным и обезличенным, без явных обращений и излишних эмоций, но при этом понятным, логичным и сохраняющим всю суть исходного сообщения.

КРИТИЧЕСКИ ВАЖНО: Первоначальный и полный смысл исходного текста должен быть сохранен АБСОЛЮТНО ТОЧНО, без малейших искажений, потерь ключевой информации или добавления нового смысла.
Исходный текст для переформулирования: "{original_text}"

Твой ответ должен содержать ИСКЛЮЧИТЕЛЬНО и ТОЛЬКО переформулированный текст.
НЕ ДОБАВЛЯЙ никаких приветствий, вступлений, объяснений своих действий, извинений, комментариев, послесловий или каких-либо других фраз, кроме самого переформулированного текста.
"""
            final_message_prefix = f"Новый вариант (стиль подобран автоматически для '{addressee_description_if_auto}'):"

        elif chosen_style_callback and chosen_style_callback != "style_auto":
            style_specific_instruction = ""
            if chosen_style_callback == "style_business":
                style_specific_instruction = """При переформулировании придерживайся следующих принципов делового стиля:
1.  **Обеспечь четкость и структуру:** Устрани из текста всё лишнее. Сделай высказывание максимально логичным, последовательным и целенаправленным.
2.  **Придай официальный тон:** Используй деловую лексику. Полностью избегай эмоциональности. При необходимости добавь нейтральные вводные конструкции и стандартные деловые формулировки.
3.  **Сфокусируйся на цели сообщения:** Подчеркни ключевую мысль, просьбу или предложение. Адаптируй текст так, чтобы он был уместен для контекста общения с коллегами, руководством или деловыми партнерами."""
            elif chosen_style_callback == "style_academic":
                style_specific_instruction = """При переформулировании придерживайся следующих принципов учебного (академического) стиля:
1.  **Обеспечь логичность и точность:** Улучши последовательность аргументов в тексте. Добавь четкие смысловые связки между частями текста для лучшей структуры.
2.  **Соблюдай нейтральную академичность:** Используй соответствующую научную или учебную терминологию. Убери разговорные элементы и просторечия. Избегай эмоциональной окраски высказываний.
3.  **Выдели ключевые понятия:** Усиливай внимание на терминах, теориях, концепциях или аргументах, которые являются важными для учебной или научной задачи, изложенной в тексте."""
            elif chosen_style_callback == "style_personal":
                style_specific_instruction = """При переформулировании придерживайся следующих принципов стиля для личного общения:
1.  **Улучши логику и структуру, сохраняя личный характер:** Сделай текст более связным. При необходимости добавь уместные эмоциональные переходы и расставь смысловые акценты, не теряя при этом индивидуальный стиль автора.
2.  **Усиль эмоциональность и живость:** Помоги тексту звучать искренне и «по-человечески». Если это уместно, можно усилить выражение чувств, добавить или скорректировать обращения и использование местоимений, чтобы лучше передать личный тон.
3.  **Сохраняй стиль автора:** Не заменяй характерные для автора индивидуальные выражения. По возможности сохраняй сленг и личные обороты речи, если они присутствуют, фокусируясь на усилении общей подачи и выразительности текста, а не на полной переделке стиля."""
            elif chosen_style_callback == "style_simplified":
                style_specific_instruction = """При переформулировании придерживайся следующих принципов упрощения текста:
1.  **Обеспечь простую структуру:** Старайся делать предложения короче. Если изначальный текст звучал запутанно, его порядок изложения мыслей может быть перестроен для большей логичности и ясности.
2.  **Используй понятную лексику:** Заменяй сложные термины и узкоспециализированные слова на более простые и общеупотребительные аналоги, при этом полностью сохраняя исходный смысл. Ключевые факты и информация должны быть сохранены.
3.  **Сделай подачу доступной:** Убирай из текста перегруженные грамматические конструкции (например, сложные причастные и деепричастные обороты, чрезмерное количество вводных слов). Текст должен читаться легко, но все важные детали и факты исходного сообщения должны остаться."""

            if not style_specific_instruction:
                 logger.error(f"Неизвестный chosen_style_callback '{chosen_style_callback}' для regenerate_text.")
                 await query.edit_message_text(text="Ошибка: неизвестный стиль для повторной генерации. Начните заново: /start")
                 return ConversationHandler.END

            prompt_for_gemini = (
                f"Твоя задача: внимательно и аккуратно переформулировать следующий исходный текст. "
                f"{style_specific_instruction} "
                f"КРИТИЧЕСКИ ВАЖНО: Первоначальный и полный смысл исходного текста должен быть сохранен АБСОЛЮТНО ТОЧНО, без малейших искажений, потерь ключевой информации или добавления нового смысла. "
                f"Исходный текст для переформулирования: \"{original_text}\"\n\n"
                f"Твой ответ должен содержать ИСКЛЮЧИТЕЛЬНО и ТОЛЬКО переформулированный текст. "
                f"НЕ ДОБАВЛЯЙ никаких приветствий, вступлений, объяснений своих действий, извинений, комментариев, послесловий или каких-либо других фраз, кроме самого переформулированного текста."
            )
            style_name_map = {
                "style_business": "Деловой стиль", "style_academic": "Учебный стиль",
                "style_personal": "Личное общение", "style_simplified": "Упрощённый текст"
            }
            readable_style_name = style_name_map.get(chosen_style_callback, chosen_style_callback)
            final_message_prefix = f"Новый вариант ({readable_style_name}):"
        else:
            logger.error("Не удалось восстановить первоначальные параметры для regenerate_text (отсутствует chosen_style_callback или addressee_description для auto).")
            await query.edit_message_text(text="Ошибка: не удалось восстановить параметры для повторной генерации. Начните заново: /start")
            return ConversationHandler.END
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
        logger.info(f"Текст доработан/перегенерирован ({action_choice}). Новый текст: {new_response_text[:50]}...")
        return POST_PROCESSING_MENU
    except Exception as e:
        logger.error(f"Ошибка при вызове gemini_api.ask_gemini в post_processing_action ({action_choice}): {e}", exc_info=True)
        await query.edit_message_text(
            text="К сожалению, произошла ошибка при доработке/генерации текста. Попробуйте позже."
        )
        return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text("Действие отменено. Чтобы начать заново, отправь /start.")
        except Exception: 
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Действие отменено. Чтобы начать заново, отправь /start.")
    elif update.message: 
        await update.message.reply_text("Действие отменено. Чтобы начать заново, просто нажми /start.")
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
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(request_text_for_correction, pattern='^start_correction$')],
        states={
            GET_TEXT_FOR_CORRECTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_text_for_correction)],
            CHOOSE_STYLE: [CallbackQueryHandler(style_chosen, pattern='^style_(business|academic|personal|simplified|auto)$')],
            DESCRIBE_ADDRESSEE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addressee_described)],
            POST_PROCESSING_MENU: [ 
                CallbackQueryHandler(post_processing_action, pattern='^(adjust_(softer|harder|more_formal)|regenerate_text|start_new_text)$')
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