import os
import requests
import cad_vdb   # Подключаем наше новое RAG-ядро
import cad_system  # Утилиты семантической классификации и защиты от зацикливания


def build_system_instructions() -> str:
    """Возвращает системный промпт для Qwen (Цепочка Рассуждений, Chain-of-Thought).

    Единая точка истины — модуль agent_draftsman.py. Здесь выполняется ленивый импорт
    во избежание циклической зависимости (агент импортирует cad_api на этапе загрузки),
    поэтому инструкция читается ТОЛЬКО в момент фактического вызова.

    Формат ответа модели — два независимых блока:
      - Блок А: текстовые размышления (🤔/🔍/🛠), выводимые в UI-чат обычным текстом;
      - Блок Б: декларативный JSON-паспорт команды внутри тегов ```json."""
    from agent_draftsman import SYSTEM_PROMPT
    return SYSTEM_PROMPT


def request_ollama_text(model_name, chat_history, callback_success, callback_log):
    url = "http://localhost:11434/api/chat"
    
    # ИЗВЛЕКАЕМ СТРОГО РЕЛЕВАНТНЫЙ КУСОК СПЕЦИФИКАЦИИ ДЛЯ ПРОМПТА ПОЛЬЗОВАТЕЛЯ
    # Ищем промпт пользователя, который лежит в самом конце истории чата
    user_query = ""
    for msg in reversed(chat_history):
        if msg.get("role") == "user":
            user_query = msg.get("content", "")
            if "[ИНФОРМАЦИЯ: Файл:" in user_query:
                # Очищаем промпт от налипшего RAG контекста чертежа для точного поиска
                user_query = user_query.split("Запрос: ")[-1]
            break
            
    # Запускаем молниеносный векторно-семантический поиск блоков
    com_reference_data = cad_vdb.get_relevant_blocks(user_query, max_blocks=2)

    system_instructions = build_system_instructions()

    messages = [{"role": "system", "content": system_instructions}] + chat_history
    payload = {"model": model_name, "messages": messages, "stream": False}
    
    try:
        response = requests.post(url, json=payload, timeout=120)
        if response.status_code == 200:
            callback_success(response.json().get("message", {}).get("content", "").strip())
        else:
            callback_log(f"Ошибка Ollama: {response.status_code}", "system")
    except Exception as e:
        callback_log(f"Ошибка связи: {str(e)}", "system")

def request_ollama_vision(user_prompt, img_base64, callback_log, callback_done=None):
    """Отправляет скриншот в локальную Ollama Vision и выводит результат в интерфейс.

    callback_log — вывод текста ответа/ошибки в чат.
    callback_done — колбэк завершения, который ВСЕГДА вызывается в блоке finally
    (и при успешном ответе, и при сетевом сбое), чтобы принудительно сбросить
    таймер вычисления в UI."""
    url = "http://localhost:11434/api/chat"
    payload = {
        "model": "qwen2.5vl:7b",
        "messages": [{"role": "user", "content": user_prompt, "images": [img_base64]}],
        "options": {"num_ctx": 8192},
        "stream": False
    }
    try:
        response = requests.post(url, json=payload, timeout=45)
        if response.status_code == 200:
            ai_text = response.json().get("message", {}).get("content", "Нет ответа")
            callback_log(f"\nИИ-Ассистент (Анализ графики):\n{ai_text}", "ai")
        else:
            callback_log(f"Ошибка модели зрения: {response.status_code}", "system")
    except Exception as e:
        callback_log(f"Ошибка отправки снимка: {str(e)}", "system")
    finally:
        # Гарантированно сбрасываем состояние загрузки UI после завершения запроса
        if callback_done:
            try:
                callback_done()
            except Exception:
                pass
