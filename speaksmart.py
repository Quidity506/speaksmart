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
    [KeyboardButton("Товый текст")]
]
main_menu_keyboard = ReplyKeyboardMarkup(main_menu_layout, resize_keyboard=True, one_time_keyboard=False)

# --- Главная, универсальная инструкция ---
TUNE_INSTRUCTION = """Твоя главная задача — действовать как деликатный корректор, а не как рерайтер.
1.  **ОБЯЗАТЕЛЬНО СОХРАНЯЙ ПРИВЕТСТВИЯ:** Никогда не удаляй и не изменяй слова приветствия, такие как "привет", "здравствуйте", "добрый день" и т.п., если они есть в начале текста.
2.  **МИНИМАЛЬНЫЕ ИЗМЕНЕНИЯ:** Не переписывай предложения полностью. Твоя цель — лишь слегка "причесать" текст. Вноси только самые необходимые, точечные изменения: можешь заменить разговорное слово на более формальное или поменять порядок слов для улучшения структуры.
3.  **СОХРАНЯЙ СУТЬ И ЛЕКСИКУ:** Сохраняй максимум оригинальных слов и конструкций автора. Идея и суть текста должны остаться абсолютно неизменными.
4.  **СОХРАНЯЙ ФОРМАТИРОВАНИЕ:** Сохраняй исходное деление на абзацы и переносы строк. Не объединяй несколько абзацев в один."""


# --- Функции бота ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_name = update.effective_user.first_name

    if context.user_data:
        welcome_text = (
            f"С возвращением, {user_name}!\n\n"
            "Чтобы начать работу, нажми кнопку «Новый текст» в меню ниже."
        )
    else:
        welcome_text = (
            f"Привет, {user_name}! Я SpeakSmartBot.\n"
            "Помогу сделать твой текст лучше. Нажми «Новый текст», чтобы начать 👇"
        )

    await update.message.reply_text(
        text=welcome_text,
        reply_markup=main_menu_keyboard
    )
    return ConversationHandler.END


async def start_new_dialogue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    logger.info(f"Пользователь {update.effective_user.id} начал новый диалог. user_data очищены.")

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


# --- ИЗМЕНЕНИЕ: Возвращен «слабый» моноширный стиль ---
async def _send_post_processing_menu(update_or_query, context: ContextTypes.DEFAULT_TYPE, response_text: str, message_prefix: str):
    context.user_data['last_gemini_response'] = response_text

    # Эта строка убирает все переносы строк для корректной работы ` `
    processed_response_text = response_text.strip().replace('\n', ' ')
    escaped_response_text = escape_markdown(processed_response_text, version=2)
    
    # Используем одинарные кавычки для "слабого" моноширного стиля
    formatted_response_text = f"`{escaped_response_text}`"
    
    escaped_message_prefix = escape_markdown(message_prefix, version=2)

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

    message_to_send = f"{escaped_message_prefix}\n\n{formatted_response_text}\n\nКак тебе результат? Можем доработать:"

    try:
        target_message_for_edit = None
        if isinstance(update_or_query, CallbackQuery):
            target_message_for_edit = update_or_query.message

        if target_message_for_edit:
            await context.bot.edit_message_text(
                text=message_to_send,
                chat_id=target_message_for_edit.chat_id,
                message_id=target_message_for_edit.message_id,
                reply_markup=reply_markup_inline,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await context.bot.send_message(
                chat_id=update_or_query.effective_chat.id,
                text=message_to_send,
                reply_markup=reply_markup_inline,
                parse_mode=ParseMode.MARKDOWN_V2
            )
    except Exception as e:
        logger.error(f"Ошибка при отправке/редактировании сообщения в _send_post_processing_menu: {e}", exc_info=True)
        chat_id_to_notify = update_or_query.effective_chat.id
        if chat_id_to_notify:
            await context.bot.send_message(chat_id=chat_id_to_notify, text="Произошла ошибка при отображении меню доработки.")


async def style_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    style_choice = query.data
    context.user_data['chosen_style'] = style_choice
    context.user_data.pop('addressee_description', None)
    logger.info(f"Пользователь {query.from_user.id} выбрал стиль: {style_choice}")
    text_to_correct = context.user_data.get('text_to_correct')

    if not text_to_correct:
        await query.edit_message_text(text="Произошла ошибка: не найден исходный текст. Пожалуйста, начни заново, нажав «Новый текст».")
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
        style_prompt_instruction = """Применяй следующие принципы делового стиля:
1.  **Обеспечь лаконичность:** Если в предложении есть очевидно лишние слова или повторы, которые можно убрать без потери смысла и изменения структуры — сделай это.
2.  **Придай официальный тон:** Заменяй разговорную лексику и жаргон на нейтральные или деловые эквиваленты.
3.  **Сохраняй фокус на цели:** Убедись, что ключевая мысль, просьба или предложение выражены четко и ясно."""
    elif style_choice == "style_academic":
        style_prompt_instruction = """Применяй следующие принципы учебного (академического) стиля:
1.  **Усиль логические связки:** При необходимости замени союзы или добавь вводные слова (например, «следовательно», «однако»), чтобы улучшить логическую последовательность, не меняя порядок предложений.
2.  **Используй точную терминологию:** Если в тексте есть разговорные аналоги научных или учебных терминов, замени их на корректные термины.
3.  **Соблюдай нейтральность:** Убирай эмоционально окрашенные слова, заменяя их на нейтральные синонимы."""
    elif style_choice == "style_personal":
        style_prompt_instruction = """Применяй следующие принципы для личного общения:
1.  **Сделай текст более живым:** Если это уместно, замени нейтральное слово на более эмоциональный синоним, не меняя общий смысл и конструкцию предложения.
2.  **Сохраняй авторский голос:** Не трогай характерные для автора выражения, сленг или личные обороты речи, если они не мешают пониманию.
3.  **Улучши связность:** Если два коротких предложения можно логично объединить в одно без потери авторского стиля, сделай это."""
    elif style_choice == "style_simplified":
        style_prompt_instruction = """Применяй следующие принципы для упрощения текста:
1.  **Используй простую лексику:** Последовательно заменяй сложные или узкоспециализированные слова на их более простые и общеупотребительные аналоги.
2.  **Сокращай, но осторожно:** Только если предложение очень длинное и запутанное, ты можешь аккуратно разделить его на два, стараясь сохранить исходные слова и порядок мысли. Не делай этого без крайней необходимости."""

    prompt_for_gemini = (
        f"Твоя задача: внимательно и аккуратно переформулировать следующий исходный текст. "
        f"{TUNE_INSTRUCTION} "
        f"Вот конкретные принципы, которым нужно следовать для выбранного стиля:\n{style_prompt_instruction}\n"
        f"КРИТИЧЕСКИ ВАЖНО: Первоначальный и полный смысл исходного текста должен быть сохранен АБСОЛЮТНО ТОЧНО, без малейших искажений, потерь ключевой информации или добавления нового смысла. "
        f"Исходный текст для переформулирования: \"{text_to_correct}\"\n\n"
        f"Твой ответ должен содержать ИСКЛЮЧИТЕЛЬНО и ТОЛЬКО переформулированный текст. "
        f"НЕ ДОБАВЛЯЙ никаких приветствий, вступлений, объяснений своих действий, извинений, комментариев, послесловий или каких-либо других фраз, кроме самого переформулированного текста."
    )

    try:
        await context.bot.send_chat_action(chat_id=query.message.chat_id, action="typing")
        response_text = gemini_api.ask_gemini(prompt_for_gemini, GEMINI_API_KEY)
        await _send_post_processing_menu(query, context, response_text, "Вот переформулированный текст:")
        return POST_PROCESSING_MENU
    except Exception as e:
        logger.error(f"Ошибка в style_chosen при вызове Gemini API: {e}", exc_info=True)
        await query.edit_message_text("К сожалению, произошла ошибка при обработке вашего запроса. Попробуйте позже.")
        return ConversationHandler.END


async def addressee_described(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    addressee_description = update.message.text
    context.user_data['addressee_description'] = addressee_description
    context.user_data['chosen_style'] = 'style_auto'
    text_to_correct = context.user_data.get('text_to_correct')

    if not text_to_correct:
        await update.message.reply_text("Произошла ошибка: не найден исходный текст. Пожалуйста, начни заново, нажав «Новый текст».")
        return ConversationHandler.END

    await update.message.reply_text("Понял тебя! Подбираю стиль и переформулирую текст для твоего адресата. Минуточку...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    style_business_instr = """Применяй следующие принципы делового стиля:
1.  **Обеспечь лаконичность:** Если в предложении есть очевидно лишние слова или повторы, которые можно убрать без потери смысла и изменения структуры — сделай это.
2.  **Придай официальный тон:** Заменяй разговорную лексику и жаргон на нейтральные или деловые эквиваленты.
3.  **Сохраняй фокус на цели:** Убедись, что ключевая мысль, просьба или предложение выражены четко и ясно."""
    style_academic_instr = """Применяй следующие принципы учебного (академического) стиля:
1.  **Усиль логические связки:** При необходимости замени союзы или добавь вводные слова (например, «следовательно», «однако»), чтобы улучшить логическую последовательность, не меняя порядок предложений.
2.  **Используй точную терминологию:** Если в тексте есть разговорные аналоги научных или учебных терминов, замени их на корректные термины.
3.  **Соблюдай нейтральность:** Убирай эмоционально окрашенные слова, заменяя их на нейтральные синонимы."""
    style_personal_instr = """Применяй следующие принципы для личного общения:
1.  **Сделай текст более живым:** Если это уместно, замени нейтральное слово на более эмоциональный синоним, не меняя общий смысл и конструкцию предложения.
2.  **Сохраняй авторский голос:** Не трогай характерные для автора выражения, сленг или личные обороты речи, если они не мешают пониманию.
3.  **Улучши связность:** Если два коротких предложения можно логично объединить в одно без потери авторского стиля, сделай это."""
    style_simplified_instr = """Применяй следующие принципы для упрощения текста:
1.  **Используй простую лексику:** Последовательно заменяй сложные или узкоспециализированные слова на их более простые и общеупотребительные аналоги.
2.  **Сокращай, но осторожно:** Только если предложение очень длинное и запутанное, ты можешь аккуратно разделить его на два, стараясь сохранить исходные слова и порядок мысли. Не делай этого без крайней необходимости."""

    prompt_for_gemini = f"""Твоя задача – переформулировать исходный текст, автоматически подобрав для него наиболее подходящий стиль, учитывая, что сообщение адресовано: '{addressee_description}'.
{TUNE_INSTRUCTION}
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
Если ты не можешь с высокой уверенностью определить один из этих четырех стилей на основе описания адресата ('{addressee_description}') и исходного текста, сделай переформулированный текст максимально нейтральным и обезличенным, без явных обращений и излишних эмоций, но при этом понятным, логичным и сохраняющим всю суть исходного сообщения.
КРИТИЧЕСКИ ВАЖНО: Первоначальный и полный смысл исходного текста должен быть сохранен АБСОЛЮТНО ТОЧНО, без малейших искажений, потерь ключевой информации или добавления нового смысла.
Исходный текст для переформулирования: "{text_to_correct}"

Твой ответ должен содержать ИСКЛЮЧИТЕЛЬНО и ТОЛЬКО переформулированный текст.
НЕ ДОБАВЛЯЙ никаких приветствий, вступлений, объяснений своих действий, извинений, комментариев, послесловий или каких-либо других фраз, кроме самого переформулированного текста."""

    try:
        response_text = gemini_api.ask_gemini(prompt_for_gemini, GEMINI_API_KEY)
        await _send_post_processing_menu(update, context, response_text, f"Вот переформулированный текст (стиль подобран автоматически для '{addressee_description}'):")
        return POST_PROCESSING_MENU
    except Exception as e:
        logger.error(f"Ошибка в addressee_described при вызове Gemini API: {e}", exc_info=True)
        await update.message.reply_text("К сожалению, произошла ошибка при обработке вашего запроса с автоопределением стиля. Попробуйте позже.")
        return ConversationHandler.END


async def post_processing_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    action_choice = query.data

    last_response = context.user_data.get('last_gemini_response')
    original_text = context.user_data.get('text_to_correct')
    chosen_style_callback = context.user_data.get('chosen_style')
    addressee_description_if_auto = context.user_data.get('addressee_description')

    prompt_for_gemini = ""
    final_message_prefix = ""

    if action_choice in ["adjust_softer", "adjust_harder", "adjust_more_formal"]:
        if not last_response:
            await query.edit_message_text(text="Ошибка: текст для доработки не найден. Начните заново, нажав «Новый текст».")
            return ConversationHandler.END

        instruction_verb_for_status_update = ""
        modification_instruction_for_gemini = ""

        if action_choice == "adjust_softer":
            instruction_verb_for_status_update = "смягчение тона"
            final_message_prefix = "Готово! Сделал текст немного мягче:"
            modification_instruction_for_gemini = "Сделай следующий текст немного мягче по тону, заменяя отдельные слова на более вежливые или дипломатичные синонимы, но не меняя структуру предложений."
        elif action_choice == "adjust_harder":
            instruction_verb_for_status_update = "увеличение жесткости/настойчивости тона"
            final_message_prefix = "Есть! Текст стал более настойчивым:"
            modification_instruction_for_gemini = "Сделай следующий текст немного жестче или более настойчивым по тону, заменяя отдельные слова на более сильные или прямые синонимы, но не меняя структуру предложений."
        elif action_choice == "adjust_more_formal":
            instruction_verb_for_status_update = "увеличение формальности стиля"
            final_message_prefix = "Пожалуйста! Теперь текст более формальный:"
            if chosen_style_callback == "style_academic":
                modification_instruction_for_gemini = "Сделай следующий текст немного более формальным, но избегай излишней строгости. Можно заменить некоторые нейтральные слова на более академические аналоги или улучшить связность предложений. Цель — отполированный учебный текст, а не сухой официальный документ."
            else:
                modification_instruction_for_gemini = "Сделай следующий текст еще более формальным, заменяя разговорные или нейтральные слова на их более официальные эквиваленты, но не меняя структуру предложений."

        await query.edit_message_text(text=f"Применяю '{instruction_verb_for_status_update}'... Минуточку.")

        prompt_for_gemini = (
            f"Твоя задача — изменить тон предоставленного текста согласно инструкции, при этом строго следуя общим правилам. "
            f"{TUNE_INSTRUCTION}\n"
            f"Инструкция по изменению тона: {modification_instruction_for_gemini}\n"
            f"КРИТИЧЕСКИ ВАЖНО: Первоначальный и полный смысл текста должен быть сохранен АБСОЛЮТНО ТОЧНО, без малейших искажений или потерь ключевой информации. "
            f"Вот текст для модификации: \"{last_response}\"\n\n"
            f"Твой ответ должен содержать ИСКЛЮЧИТЕЛЬНО и ТОЛЬКО измененный текст. "
            f"НЕ ДОБАВЛЯЙ никаких приветствий, вступлений, объяснений своих действий, извинений, комментариев, послесловий или каких-либо других фраз, кроме самого переформулированного текста."
        )
    elif action_choice == "regenerate_text":
        if not original_text:
            await query.edit_message_text(text="Ошибка: исходный текст для повторной генерации не найден. Начните заново, нажав «Новый текст».")
            return ConversationHandler.END

        await query.edit_message_text(text="Генерирую новый вариант на основе первоначальных данных... Минуточку.")

        if chosen_style_callback == "style_auto" and addressee_description_if_auto:
            style_business_instr = """Применяй следующие принципы делового стиля:
1.  **Обеспечь лаконичность:** Если в предложении есть очевидно лишние слова или повторы, которые можно убрать без потери смысла и изменения структуры — сделай это.
2.  **Придай официальный тон:** Заменяй разговорную лексику и жаргон на нейтральные или деловые эквиваленты.
3.  **Сохраняй фокус на цели:** Убедись, что ключевая мысль, просьба или предложение выражены четко и ясно."""
            style_academic_instr = """Применяй следующие принципы учебного (академического) стиля:
1.  **Усиль логические связки:** При необходимости замени союзы или добавь вводные слова (например, «следовательно», «однако»), чтобы улучшить логическую последовательность, не меняя порядок предложений.
2.  **Используй точную терминологию:** Если в тексте есть разговорные аналоги научных или учебных терминов, замени их на корректные термины.
3.  **Соблюдай нейтральность:** Убирай эмоционально окрашенные слова, заменяя их на нейтральные синонимы."""
            style_personal_instr = """Применяй следующие принципы для личного общения:
1.  **Сделай текст более живым:** Если это уместно, замени нейтральное слово на более эмоциональный синоним, не меняя общий смысл и конструкцию предложения.
2.  **Сохраняй авторский голос:** Не трогай характерные для автора выражения, сленг или личные обороты речи, если они не мешают пониманию.
3.  **Улучши связность:** Если два коротких предложения можно логично объединить в одно без потери авторского стиля, сделай это."""
            style_simplified_instr = """Применяй следующие принципы для упрощения текста:
1.  **Используй простую лексику:** Последовательно заменяй сложные или узкоспециализированные слова на их более простые и общеупотребительные аналоги.
2.  **Сокращай, но осторожно:** Только если предложение очень длинное и запутанное, ты можешь аккуратно разделить его на два, стараясь сохранить исходные слова и порядок мысли. Не делай этого без крайней необходимости."""

            prompt_for_gemini = f"""Твоя задача – переформулировать исходный текст, автоматически подобрав для него наиболее подходящий стиль, учитывая, что сообщение адресовано: '{addressee_description_if_auto}'.
{TUNE_INSTRUCTION}
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
НЕ ДОБАВЛЯЙ никаких приветствий, вступлений, объяснений своих действий, извинений, комментариев, послесловий или каких-либо других фраз, кроме самого переформулированного текста."""
            final_message_prefix = f"Новый вариант (стиль подобран автоматически для '{addressee_description_if_auto}'):"

        elif chosen_style_callback and chosen_style_callback != "style_auto":
            style_specific_instruction = ""
            if chosen_style_callback == "style_business":
                style_specific_instruction = """Применяй следующие принципы делового стиля:
1.  **Обеспечь лаконичность:** Если в предложении есть очевидно лишние слова или повторы, которые можно убрать без потери смысла и изменения структуры — сделай это.
2.  **Придай официальный тон:** Заменяй разговорную лексику и жаргон на нейтральные или деловые эквиваленты.
3.  **Сохраняй фокус на цели:** Убедись, что ключевая мысль, просьба или предложение выражены четко и ясно."""
            elif chosen_style_callback == "style_academic":
                style_specific_instruction = """Применяй следующие принципы учебного (академического) стиля:
1.  **Усиль логические связки:** При необходимости замени союзы или добавь вводные слова (например, «следовательно», «однако»), чтобы улучшить логическую последовательность, не меняя порядок предложений.
2.  **Используй точную терминологию:** Если в тексте есть разговорные аналоги научных или учебных терминов, замени их на корректные термины.
3.  **Соблюдай нейтральность:** Убирай эмоционально окрашенные слова, заменяя их на нейтральные синонимы."""
            elif chosen_style_callback == "style_personal":
                style_specific_instruction = """Применяй следующие принципы для личного общения:
1.  **Сделай текст более живым:** Если это уместно, замени нейтральное слово на более эмоциональный синоним, не меняя общий смысл и конструкцию предложения.
2.  **Сохраняй авторский голос:** Не трогай характерные для автора выражения, сленг или личные обороты речи, если они не мешают пониманию.
3.  **Улучши связность:** Если два коротких предложения можно логично объединить в одно без потери авторского стиля, сделай это."""
            elif chosen_style_callback == "style_simplified":
                style_specific_instruction = """Применяй следующие принципы для упрощения текста:
1.  **Используй простую лексику:** Последовательно заменяй сложные или узкоспециализированные слова на их более простые и общеупотребительные аналоги.
2.  **Сокращай, но осторожно:** Только если предложение очень длинное и запутанное, ты можешь аккуратно разделить его на два, стараясь сохранить исходные слова и порядок мысли. Не делай этого без крайней необходимости."""

            if not style_specific_instruction:
                 await query.edit_message_text(text="Ошибка: неизвестный стиль для повторной генерации. Начните заново, нажав «Новый текст».")
                 return ConversationHandler.END

            prompt_for_gemini = (
                f"Твоя задача: внимательно и аккуратно переформулировать следующий исходный текст. "
                f"{TUNE_INSTRUCTION} "
                f"Вот конкретные принципы, которым нужно следовать для выбранного стиля:\n{style_specific_instruction}\n"
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
            await query.edit_message_text(text="Ошибка: не удалось восстановить параметры для повторной генерации. Начните заново, нажав «Новый текст».")
            return ConversationHandler.END
    else:
        await query.edit_message_text(text=f"Неизвестное действие: {action_choice}. Завершаю диалог.")
        return ConversationHandler.END

    await context.bot.send_chat_action(chat_id=query.message.chat_id, action="typing")

    try:
        new_response_text = gemini_api.ask_gemini(prompt_for_gemini, GEMINI_API_KEY)
        await _send_post_processing_menu(query, context, new_response_text, final_message_prefix)
        return POST_PROCESSING_MENU
    except Exception as e:
        logger.error(f"Ошибка в post_processing_action при вызове Gemini API: {e}", exc_info=True)
        await query.edit_message_text(
            text="К сожалению, произошла ошибка при доработке/генерации текста. Попробуйте позже."
        )
        return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Действие отменено. Чтобы начать заново, нажми «Новый текст».")
    elif update.message:
        await update.message.reply_text("Действие отменено. Чтобы начать заново, нажми «Новый текст».",
                                      reply_markup=main_menu_keyboard)
    context.user_data.clear()
    logger.info(f"Пользователь {update.effective_user.id} отменил диалог.")
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
        entry_points=[MessageHandler(filters.Text(["Новый текст"]), start_new_dialogue)],
        states={
            GET_TEXT_FOR_CORRECTION: [
                MessageHandler(filters.Text(["Новый текст"]), start_new_dialogue),
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_text_for_correction)
            ],
            CHOOSE_STYLE: [
                MessageHandler(filters.Text(["Новый текст"]), start_new_dialogue),
                CallbackQueryHandler(style_chosen, pattern='^style_(business|academic|personal|simplified|auto)$')
            ],
            DESCRIBE_ADDRESSEE: [
                MessageHandler(filters.Text(["Новый текст"]), start_new_dialogue),
                MessageHandler(filters.TEXT & ~filters.COMMAND, addressee_described)
            ],
            POST_PROCESSING_MENU: [
                MessageHandler(filters.Text(["Новый текст"]), start_new_dialogue),
                CallbackQueryHandler(post_processing_action, pattern='^(adjust_(softer|harder|more_formal)|regenerate_text)$')
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel_conversation)],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))

    logger.info("Бот Telegram успешно настроен и запускается в режиме опроса...")
    application.run_polling()


if __name__ == "__main__":
    main()