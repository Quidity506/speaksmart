import logging
import os
# import threading # threading теперь используется внутри health_checker
# from http.server import BaseHTTPRequestHandler, HTTPServer # Эти импорты больше не нужны здесь
# from urllib.parse import urlparse # Этот импорт больше не нужен здесь

# --- ИМПОРТЫ TELEGRAM ---
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
# ReplyKeyboardMarkup и KeyboardButton пока не используем активно, но импорт может остаться
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
from health_checker import start_health_check_server_in_thread # Импортируем из модуля

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
    context.user_data['last_gemini_response'] = response_text 
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
    message_to_send = f"{message_prefix}\n\n{response_text}\n\nКак тебе результат? Можем доработать:"
    
    try:
        if isinstance(update_or_query, CallbackQuery):
            if update_or_query.message:
                await context.bot.edit_message_text(
                    text=message_to_send,
                    chat_id=update_or_query.message.chat_id,
                    message_id=update_or_query.message.message_id,
                    reply_markup=reply_markup_inline
                )
            else:
                logger.error("CallbackQuery не содержит message объекта для редактирования.")
                await context.bot.send_message(chat_id=update_or_query.from_user.id, text=message_to_send, reply_markup=reply_markup_inline)
        elif isinstance(update_or_query, Update) and update_or_query.message:
            await update_or_query.message.reply_text(text=message_to_send, reply_markup=reply_markup_inline)
        else:
            logger.error(f"Неожиданный тип объекта или отсутствует чат в _send_post_processing_menu: {type(update_or_query)}")
            if context.update and context.update.effective_chat:
                 await context.bot.send_message(chat_id=context.update.effective_chat.id, text="Произошла ошибка отображения результата.")
    except Exception as e:
        logger.error(f"Ошибка при отправке/редактировании сообщения в _send_post_processing_menu: {e}", exc_info=True)
        chat_id_to_notify = None
        if isinstance(update_or_query, CallbackQuery): chat_id_to_notify = update_or_query.from_user.id
        elif isinstance(update_or_query, Update) and update_or_query.effective_chat: chat_id_to_notify = update_or_query.effective_chat.id
        if chat_id_to_notify: await context.bot.send_message(chat_id=chat_id_to_notify, text="Произошла ошибка при отображении меню доработки.")

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
        style_prompt_instruction = """придерживаясь следующих принципов делового стиля:
1. Тон: официальный, сухой, безэмоциональный.
2. Структура и содержание: обеспечь чёткую структуру и логику изложения, избегай лишних слов и 'воды'.
3. Лексика: используй нейтральную лексику, при необходимости применяй уместные речевые клише (например, «согласно договорённости», «просим предоставить», «доводим до вашего сведения»).
4. Исключить: просторечия, жаргон, двусмысленность, субъективные оценочные суждения и любую излишнюю эмоциональность."""
    elif style_choice == "style_academic": 
        style_prompt_instruction = """придерживаясь следующих принципов учебного стиля:
1. Тон: вежливый, уважительный, нейтральный, но доброжелательный, с элементами формальности (например, используй формулы вежливости типа «Добрый день», «Прошу уточнить», «Благодарю за ответ»).
2. Ясность и логика: обеспечь чёткую и логичную формулировку мыслей, акцентируй внимание на ясности изложения (например, используй обороты «в связи с этим, предлагаю…», «считаю необходимым уточнить…»).
3. Содержание: минимум 'воды' и максимум смысла.
4. Исключить: жаргон, фамильярность, излишнюю эмоциональность и неуместные сокращения.
5. Отличие от делового стиля: текст должен быть мягче и менее строгим, сохраняя при этом человеческое звучание и уважение."""
    elif style_choice == "style_personal": 
        style_prompt_instruction = """придерживаясь следующих принципов стиля для личного общения:
1. Цель: создать душевное и точное сообщение, подходящее для близкого человека (друга, партнера, члена семьи).
2. Эмоции: помоги деликатно и по делу выразить эмоции, как позитивные (например, любовь, благодарность), так и сложные (например, обида, разочарование).
3. Структура: если сообщение длинное, помоги сохранить логику и структуру, чтобы мысль была понятна.
4. Стиль речи: речь должна быть естественной, но при этом более точной, чистой и уверенной, чем обычная разговорная. "Мягкие" формулировки и уместные по контексту эмодзи допустимы.
5. Контекст использования: текст может быть предназначен для поздравления, признания, спокойного объяснения в споре, выражения своей позиции без желания обидеть или, наоборот, для твердого ее выражения."""
    elif style_choice == "style_simplified": 
        style_prompt_instruction = """следуя принципам упрощения текста:
1. Цель: превратить исходный текст в понятное, короткое сообщение, убрав излишнюю сложность и перегруженность.
2. Конструкции: избавься от тяжелых и нагроможденных грамматических конструкций (например, замени "учитывая изложенное, можно заключить" на более простую фразу).
3. Словарь: используй простые слова, избегай специальной или сложной лексики, если это возможно без потери смысла.
4. Структура: стремись к коротким предложениям и понятному порядку изложения мыслей.
5. Результат: текст должен стать лаконичным, человечным, легким для понимания, при этом сохраняя всю ключевую суть исходного сообщения."""
    
    prompt_for_gemini = (
        f"Твоя задача: внимательно и аккуратно переформулировать следующий исходный текст. "
        f"При переформулировании {style_prompt_instruction}. "
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
    
    prompt_for_gemini = (
        f"Твоя задача – переформулировать исходный текст, автоматически подобрав для него наиболее подходящий стиль, "
        f"учитывая, что сообщение адресовано: '{addressee_description}'.\n"
        f"Ориентируйся на следующие возможные стили и их ключевые характеристики:\n"
        f"1. Деловой стиль: официальный, сухой, безэмоциональный тон, чёткая структура, логика, без лишних слов, возможны клише.\n"
        f"2. Учебный стиль: вежливый, уважительный, нейтрально-доброжелательный, с элементами формальности, чёткая и логичная формулировка мыслей, минимум 'воды', без жаргона и излишних эмоций.\n"
        f"3. Стиль личного общения: душевный, помогает деликатно выразить эмоции, естественный, но точный и уверенный, допустимы уместные эмодзи.\n"
        f"4. Упрощенный текст: понятный, короткий, без сложных конструкций, лаконичный, сохраняющий суть.\n\n"
        f"Проанализируй исходный текст и описание адресата, выбери наилучший из этих стилей (или их уместную комбинацию, если это необходимо) и переформулируй текст в соответствии с ним. "
        f"КРИТИЧЕСКИ ВАЖНО: Первоначальный и полный смысл исходного текста должен быть сохранен АБСОЛЮТНО ТОЧНО, без малейших искажений, потерь ключевой информации или добавления нового смысла.\n\n" 
        f"Исходный текст: \"{text_to_correct}\"\n\n"
        f"Твой ответ должен содержать ИСКЛЮЧИТЕЛЬНО и ТОЛЬКО переформулированный текст. "
        f"НЕ ДОБАВЛЯЙ никаких приветствий, вступлений, объяснений своих действий, извинений, комментариев, послесловий или каких-либо других фраз, кроме самого переформулированного текста."
    )
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
        await query.edit_message_text(text="Отлично! Теперь, пожалуйста, отправь мне текст, который нужно переформулировать.")
        return GET_TEXT_FOR_CORRECTION

    last_response = context.user_data.get('last_gemini_response')
    original_text = context.user_data.get('text_to_correct')
    chosen_style_callback = context.user_data.get('chosen_style') 
    addressee_description_if_auto = context.user_data.get('addressee_description')

    prompt_for_gemini = ""
    final_message_prefix = "" 

    if action_choice in ["adjust_softer", "adjust_harder", "adjust_more_formal"]:
        if not last_response:
            logger.warning(f"Не найден last_gemini_response для {action_choice}")
            await query.edit_message_text(text="Ошибка: текст для доработки не найден. Начните заново: /start")
            return ConversationHandler.END

        instruction_verb = ""
        modification_instruction = ""
        if action_choice == "adjust_softer":
            instruction_verb = "смягчение тона"
            modification_instruction = "Сделай следующий текст немного мягче по тону"
        elif action_choice == "adjust_harder":
            instruction_verb = "увеличение жесткости/настойчивости тона"
            modification_instruction = "Сделай следующий текст немного жестче или более настойчивым по тону"
        elif action_choice == "adjust_more_formal":
            instruction_verb = "увеличение формальности стиля"
            modification_instruction = "Сделай следующий текст еще более формальным"
        
        await query.edit_message_text(text=f"Применяю '{instruction_verb}'... Минуточку.")
        prompt_for_gemini = (
            f"{modification_instruction}, КРИТИЧЕСКИ ВАЖНО: Первоначальный и полный смысл текста должен быть сохранен АБСОЛЮТНО ТОЧНО, без малейших искажений или потерь ключевой информации. "
            f"Вот текст для модификации: \"{last_response}\"\n\n"
            f"Твой ответ должен содержать ИСКЛЮЧИТЕЛЬНО и ТОЛЬКО измененный текст. "
            f"НЕ ДОБАВЛЯЙ никаких приветствий, вступлений, объяснений своих действий, извинений, комментариев, послесловий или каких-либо других фраз, кроме самого измененного текста."
        )
        final_message_prefix = f"Текст после '{instruction_verb}':"

    elif action_choice == "regenerate_text":
        if not original_text:
            logger.warning(f"Не найден original_text для regenerate_text")
            await query.edit_message_text(text="Ошибка: исходный текст для повторной генерации не найден. Начните заново: /start")
            return ConversationHandler.END

        await query.edit_message_text(text="Генерирую новый вариант на основе первоначальных данных... Минуточку.")
        
        if chosen_style_callback == "style_auto" and addressee_description_if_auto:
            # Используем уже обновленный промпт для автоопределения
            style_details_for_prompt = (
                f"Твоя задача – переформулировать исходный текст, автоматически подобрав для него наиболее подходящий стиль, "
                f"учитывая, что сообщение адресовано: '{addressee_description_if_auto}'.\n"
                f"Ориентируйся на следующие возможные стили и их ключевые характеристики:\n"
                f"1. Деловой стиль: официальный, сухой, безэмоциональный тон, чёткая структура, логика, без лишних слов, возможны клише.\n"
                f"2. Учебный стиль: вежливый, уважительный, нейтрально-доброжелательный, с элементами формальности, чёткая и логичная формулировка мыслей, минимум 'воды', без жаргона и излишних эмоций.\n"
                f"3. Стиль личного общения: душевный, помогает деликатно выразить эмоции, естественный, но точный и уверенный, допустимы уместные эмодзи.\n"
                f"4. Упрощенный текст: понятный, короткий, без сложных конструкций, лаконичный, сохраняющий суть.\n\n"
                f"Проанализируй исходный текст и описание адресата, выбери наилучший из этих стилей (или их уместную комбинацию, если это необходимо) и переформулируй текст в соответствии с ним."
            )
            final_message_prefix = f"Новый вариант (стиль подобран автоматически для '{addressee_description_if_auto}'):"
        
        elif chosen_style_callback and chosen_style_callback != "style_auto":
            style_specific_instruction = ""
            if chosen_style_callback == "style_business": 
                style_specific_instruction = """придерживаясь следующих принципов делового стиля:
1. Тон: официальный, сухой, безэмоциональный.
2. Структура и содержание: обеспечь чёткую структуру и логику изложения, избегай лишних слов и 'воды'.
3. Лексика: используй нейтральную лексику, при необходимости применяй уместные речевые клише (например, «согласно договорённости», «просим предоставить», «доводим до вашего сведения»).
4. Исключить: просторечия, жаргон, двусмысленность, субъективные оценочные суждения и любую излишнюю эмоциональность."""
            elif chosen_style_callback == "style_academic": 
                style_specific_instruction = """придерживаясь следующих принципов учебного стиля:
1. Тон: вежливый, уважительный, нейтральный, но доброжелательный, с элементами формальности (например, используй формулы вежливости типа «Добрый день», «Прошу уточнить», «Благодарю за ответ»).
2. Ясность и логика: обеспечь чёткую и логичную формулировку мыслей, акцентируй внимание на ясности изложения (например, используй обороты «в связи с этим, предлагаю…», «считаю необходимым уточнить…»).
3. Содержание: минимум 'воды' и максимум смысла.
4. Исключить: жаргон, фамильярность, излишнюю эмоциональность и неуместные сокращения.
5. Отличие от делового стиля: текст должен быть мягче и менее строгим, сохраняя при этом человеческое звучание и уважение."""
            elif chosen_style_callback == "style_personal": 
                style_specific_instruction = """придерживаясь следующих принципов стиля для личного общения:
1. Цель: создать душевное и точное сообщение, подходящее для близкого человека (друга, партнера, члена семьи).
2. Эмоции: помоги деликатно и по делу выразить эмоции, как позитивные (например, любовь, благодарность), так и сложные (например, обида, разочарование).
3. Структура: если сообщение длинное, помоги сохранить логику и структуру, чтобы мысль была понятна.
4. Стиль речи: речь должна быть естественной, но при этом более точной, чистой и уверенной, чем обычная разговорная. "Мягкие" формулировки и уместные по контексту эмодзи допустимы.
5. Контекст использования: текст может быть предназначен для поздравления, признания, спокойного объяснения в споре, выражения своей позиции без желания обидеть или, наоборот, для твердого ее выражения."""
            elif chosen_style_callback == "style_simplified": 
                style_specific_instruction = """следуя принципам упрощения текста:
1. Цель: превратить исходный текст в понятное, короткое сообщение, убрав излишнюю сложность и перегруженность.
2. Конструкции: избавься от тяжелых и нагроможденных грамматических конструкций (например, замени "учитывая изложенное, можно заключить" на более простую фразу).
3. Словарь: используй простые слова, избегай специальной или сложной лексики, если это возможно без потери смысла.
4. Структура: стремись к коротким предложениям и понятному порядку изложения мыслей.
5. Результат: текст должен стать лаконичным, человечным, легким для понимания, при этом сохраняя всю ключевую суть исходного сообщения."""
            
            style_details_for_prompt = f"При переформулировании {style_specific_instruction}."
            final_message_prefix = f"Новый вариант (стиль {chosen_style_callback}):"
        else: 
            logger.error("Не удалось восстановить первоначальные параметры для regenerate_text.")
            await query.edit_message_text(text="Ошибка: не удалось восстановить параметры для повторной генерации. Начните заново: /start")
            return ConversationHandler.END

        # Общая часть промпта для регенерации и прямых стилей
        prompt_for_gemini = (
            f"Твоя задача: внимательно и аккуратно переформулировать следующий исходный текст. "
            f"{style_details_for_prompt} " # style_details_for_prompt уже содержит нужную инструкцию
            f"КРИТИЧЕСКИ ВАЖНО: Первоначальный и полный смысл исходного текста должен быть сохранен АБСОЛЮТНО ТОЧНО, без малейших искажений, потерь ключевой информации или добавления нового смысла. "
            f"Исходный текст для переформулирования: \"{original_text}\"\n\n"
            f"Твой ответ должен содержать ИСКЛЮЧИТЕЛЬНО и ТОЛЬКО переформулированный текст. "
            f"НЕ ДОБАВЛЯЙ никаких приветствий, вступлений, объяснений своих действий, извинений, комментариев, послесловий или каких-либо других фраз, кроме самого переформулированного текста."
        )
    else: # Неизвестное действие
        logger.warning(f"Неизвестное действие в post_processing_action: {action_choice}")
        await query.edit_message_text(text=f"Неизвестное действие: {action_choice}. Завершаю диалог.")
        return ConversationHandler.END

    # --- Общий блок вызова Gemini для adjust_* и regenerate_text ---
    logger.info(f"Промпт для Gemini ({action_choice}): {prompt_for_gemini[:500]}...") # Логируем только часть длинного промпта
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    if not GEMINI_API_KEY:
        logger.error(f"GEMINI_API_KEY не настроен для post_processing_action ({action_choice}).")
        await context.bot.edit_message_text(text="Ошибка конфигурации сервиса. Ключ API не найден.", chat_id=query.message.chat_id, message_id=query.message.message_id)
        return ConversationHandler.END
    
    try:
        new_response_text = gemini_api.ask_gemini(prompt_for_gemini, GEMINI_API_KEY)
        await _send_post_processing_menu(query, context, new_response_text, final_message_prefix)
        logger.info(f"Текст доработан/перегенерирован ({action_choice}). Новый текст: {new_response_text[:50]}...")
        return POST_PROCESSING_MENU

    except Exception as e:
        logger.error(f"Ошибка при вызове gemini_api.ask_gemini в post_processing_action ({action_choice}): {e}", exc_info=True)
        await context.bot.edit_message_text(
            text="К сожалению, произошла ошибка при доработке/генерации текста. Попробуйте позже.",
            chat_id=query.message.chat_id,
            message_id=query.message.message_id
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