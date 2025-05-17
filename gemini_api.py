import requests
import logging

# Настройка базового логирования (опционально, но полезно для отладки на сервере)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_api_headers():
    """
    Возвращает базовые HTTP-заголовки, необходимые для запроса к Gemini API.
    В версии для Render.com нам больше не нужны заголовки для "маскировки" геолокации.
    """
    return {
        'Content-Type': 'application/json',
        # 'User-Agent': 'SpeakSmartBot/1.0 (Telegram Bot)' # Можно добавить User-Agent для идентификации вашего бота
    }

def ask_gemini(prompt: str, api_key: str) -> str:
    """
    Отправляет запрос к Google Gemini API и возвращает текстовый ответ.

    Args:
        prompt: Текстовый промпт для модели.
        api_key: Ваш API-ключ для Gemini API.

    Returns:
        Строка с ответом от модели или сообщение об ошибке.
    """
    # URL для модели gemini-1.5-flash (или другой, если потребуется)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    
    # Тело запроса к API
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    }
                ]
            }
        ]
        # Здесь можно добавить и другие параметры, если потребуется, 
        # например, generationConfig для управления генерацией.
    }
    
    try:
        # Выполняем POST-запрос к API
        # Используем get_api_headers() вместо get_masked_headers()
        response = requests.post(url, headers=get_api_headers(), json=payload, timeout=30) # Добавлен таймаут
        
        # Проверяем статус ответа
        response.raise_for_status() # Вызовет исключение для кодов ошибок 4xx/5xx
        
        result = response.json()
        
        # Извлекаем сгенерированный текст
        if 'candidates' in result and len(result['candidates']) > 0:
            candidate = result['candidates'][0]
            if 'content' in candidate and 'parts' in candidate['content'] and len(candidate['content']['parts']) > 0:
                # Проверяем наличие 'text' в первой части
                if 'text' in candidate['content']['parts'][0]:
                    return candidate['content']['parts'][0]['text']
                else:
                    logging.warning("Ответ API не содержит 'text' в ожидаемом месте: %s", result)
                    return "Получен ответ от API в неожиданном формате (отсутствует текст)."
            # Обработка случая, если ответ заблокирован из-за safetySettings или другого
            elif 'finishReason' in candidate and candidate['finishReason'] == 'SAFETY':
                logging.warning("Запрос был заблокирован настройками безопасности API: %s", result)
                # Можно также проверить candidate.get('safetyRatings')
                return "Ваш запрос не может быть обработан из-за настроек безопасности."

        logging.warning("Ответ API не содержит ожидаемых 'candidates': %s", result)
        return "Не удалось извлечь ответ из данных API."

    except requests.exceptions.RequestException as e:
        # Обработка сетевых ошибок или ошибок HTTP
        logging.error(f"Ошибка при запросе к Gemini API: {e}")
        # В response может быть дополнительная информация, если ошибка HTTP
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_details = e.response.json()
                error_message = error_details.get('error', {}).get('message', e.response.text)
                # Проверка на геоблокировку, хотя на Render это маловероятно
                if "User location is not supported" in error_message:
                     logging.error("Ошибка геолокации API даже на сервере! Проверьте настройки сервера/проекта.")
                     return "Сервис временно недоступен из-за ограничений геолокации. Разработчик уведомлен."
                return f"Ошибка API ({e.response.status_code}): {error_message}"
            except ValueError: # Если ответ не JSON
                return f"Ошибка API ({e.response.status_code}): {e.response.text}"
        return "Произошла ошибка при подключении к сервису переформулирования. Пожалуйста, попробуйте позже."
    except Exception as e:
        # Обработка других непредвиденных ошибок
        logging.error(f"Непредвиденная ошибка в ask_gemini: {e}")
        return "Произошла внутренняя ошибка. Пожалуйста, попробуйте позже."

# Функции setup_proxy() и check_proxy() здесь больше не нужны,
# так как на Render.com мы не используем локальный VPN и SOCKS-прокси.
# Также удалена функция get_masked_headers(), так как маскировка больше не требуется.
# Вместо нее используется get_api_headers().
