# -*- coding: utf-8 -*-
"""main_router.py — Главный маршрутизатор (оркестратор) мультиагентной системы.

Данный модуль — финальный управляющий слой всей системы. Он принимает текстовый
запрос пользователя из UI, определяет намерение (Intent), подмешивает визуальный
контекст «Ножниц», выбирает жёстко закреплённую локальную модель Ollama и
вызывает нужного агента через Function Calling.

Основные обязанности:
    1. AGENTS_CONFIG — жёсткая привязка моделей Ollama к ролям агентов.
    2. route_request() — маршрутизация по ключевым словам + интеграция «Ножниц».
    3. Вызов локальной Ollama на http://localhost:11434/api/chat.
    4. Безопасное исполнение выбранной функции агента (Function Calling) без
       exec()/eval() — только статичный диспетчер по именам функций.
    5. Возврат результата в графический интерфейс.

Ключевые принципы:
    - Никакого exec()/eval() — функции вызываются по имени из реестра.
    - Все ошибки связи с Ollama возвращаются на русском языке.
    - Весь код обёрнут в try-except и остаётся модульным.

Все комментарии и строки документации написаны на русском языке.
"""

from __future__ import annotations

import json  # разбор JSON-ответов модели (Function Calling)
import re    # разбор содержимого JSON-блоков в ответе модели
import threading  # запуск маршрутизации запроса в отдельном потоке

import requests  # HTTP-клиент для запросов к локальной Ollama

# Глобальная онтология и умный стек памяти проекта (тезаурус терминов AutoCAD,
# разрешение множественных анафор: группы объектов, циклы, уточнение).
from core_ontology import ONTOLOGY_STATE, mentions_group_anaphora, mentions_singular_anaphora

# Принудительное обновление графического экрана AutoCAD после команд черчения
# и манипуляции свойствами (команда doc.Update()).
from core_core import update_screen


# ========================================================================
# ЖЁСТКАЯ КОНФИГУРАЦИЯ АГЕНТОВ И МОДЕЛЕЙ OLLAMA
# ========================================================================
# Каждая роль строго привязана к своей модели — никакого размытия векторов.
# Ключи словаря: draftsman (Чертёжник), analyst (Аналитик), operator (Оператор),
# reference (Справочник).

AGENTS_CONFIG = {
    "draftsman": {
        "name": "Чертёжник",
        "model": "qwen2.5-coder:14b-instruct-q8_0",
        "system_prompt": (
            "Ты — ИИ-ассистент чертёжник. Твоя цель — вызывать геометрические "
            "инструменты на основе запроса. Передавай только JSON с параметрами."
        ),
        # Ключевые слова Чертёжника — СТРОГО глаголы-повелители (создание и построение
        # геометрии). Имена существительные («круг», «линия») отсутствуют, поэтому
        # простое упоминание фигуры НЕ вызывает ложное переключение на Чертёжника.
        "keywords": ["черти", "начерти", "начертите", "рисуй", "нарисуй", "нарисуйте",
                     "построй", "постройте", "создай", "создайте", "размести",
                     "разместите", "вставь", "вставьте", "штрихуй", "заштрихуй",
                     "выдави", "вращай", "скругли", "срежь", "подпиши"],
    },
    "analyst": {
        "name": "Аналитик",
        "model": "deepseek-r1:14b",
        "system_prompt": (
            "Ты — ИИ-аналитик. Твоя задача — инспектировать чертеж, запрашивать "
            "списки слоев, листов и считать объекты. Не пытайся ничего чертить.\n"
            "КРИТИЧЕСКОЕ ПРАВИЛО: Ответ должен быть строго на РУССКОМ языке. "
            "Категорически запрещено использовать английский язык или китайские "
            "иероглифы в итоговом текстовом ответе.\n"
            "ПРАВИЛО КОНТЕКСТА: Если пользователь спрашивает «какие у него свойства?» "
            "или использует слова «его», «этого», «у него», ты обязан проанализировать "
            "историю сообщений выше, чтобы понять, о каком объекте идет речь.\n"
            "- Если последним обсуждаемым объектом был ЧЕРТЕЖ (файл), ты должен вызвать "
            "функцию чтения свойств документа (слои, листы, имя файла).\n"
            "- Если последним объектом была конкретная ГЕОМЕТРИЧЕСКАЯ ДЕТАЛЬ (круг, "
            "линия), извлеки её Handle из истории чата и вызови функцию получения "
            "свойств объекта по его дескриптору (цвет, слой, геометрические параметры).\n"
            "Ты не имеешь права переспрашивать пользователя. Твоя задача — извлечь "
            "контекст, вызвать нужную Python-функцию из agent_analyst.py и выдать "
            "структурированные свойства на русском языке.\n"
            "ПРАВИЛО НАИМЕНОВАНИЯ ГЕОМЕТРИИ: Если ты распознал на чертеже фигуру, "
            "построенную как квадрат (прямоугольник с равными сторонами / замкнутая "
            "полилиния из четырёх углов), называй её строго «Квадрат», а окружность — "
            "строго «Круг». Никогда не называй квадрат «прямоугольником» или «полилинией»."
        ),
        # Ключевые слова Аналитика — инспекция, вычисления и сбор метаданных
        # (повелительные глаголы и вопросительные слова).
        "keywords": ["проверь", "проверьте", "найди", "найдите", "посчитай",
                     "посчитайте", "сколько", "какие", "пересекаются", "размерь",
                     "проставь размер", "измерь", "вычисли", "извлеки",
                     "проанализируй", "что на скриншоте", "что видишь"],
    },
    "operator": {
        "name": "Оператор",
        "model": "qwen2.5-coder:14b-instruct-q8_0",
        "system_prompt": (
            "Ты — ИИ-оператор. Ты переключаешь вкладки (Модель/Лист), активируешь "
            "чертежи и меняешь цвета слоев и объектов."
        ),
        # Ключевые слова Оператора — СТРОГО глаголы-повелители трансформации,
        # навигации, слоёв и работы с файлами.
        "keywords": [
            # Модификация объектов.
            "перемести", "сдвинь", "копируй", "скопируй", "поверни", "разверни",
            "масштабируй", "отмасштабируй", "отрази", "отзеркаль", "зеркалируй",
            "сотри", "удали", "обрежь", "удлини", "разорви", "расчлени", "взорви",
            "разбей", "соедини", "выровняй", "смести", "растяни", "скрой", "изолируй",
            # Навигация и слои.
            "покажи", "зумируй", "центрируй", "регенерируй", "обнови", "переключи",
            "включи", "выключи", "заблокируй", "разблокируй", "заморозь", "назначь",
            "перекрась", "перенеси на слой",
            # Системные операции и файлы.
            "сохрани", "экспортируй", "печатай", "распечатай", "очисти", "запурджи",
            "импортируй", "закрой",
        ],
    },
    "reference": {
        "name": "Справочник",
        "model": "mistral-small:22b",
        "system_prompt": (
            "Ты — ИИ-справочник по AutoCAD. Используй встроенную базу знаний "
            "cad_reference.md для ответов на технические вопросы пользователя. "
            "Давай развернутые ответы на русском."
        ),
        # Ключевые слова Справочника — теория и техническая помощь.
        "keywords": ["как сделать", "почему", "расскажи про", "справка",
                     "инструкция", "объясни"],
    },
}

# Базовый URL локального API Ollama.
OLLAMA_URL = "http://localhost:11434/api/chat"

# Сообщение об ошибке при недоступной модели Ollama (на русском языке).
OLLAMA_ERROR_MESSAGE = (
    "Локальная модель Ollama не отвечает. Убедитесь, что она запущена."
)

# Текущий визуальный контекст «Ножниц», зафиксированный пользователем в UI.
# Принудительно подмешивается в системный промпт ВСЕХ агентов (Аналитика,
# Чертёжника, Оператора и Справочника), чтобы модель НИКОГДА не отвечала
# «Я не могу просмотреть визуальный контекст».
_CURRENT_SCISSORS_CONTEXT = None

# ЖЁСТКИЕ ТРИГГЕРЫ ДЕЙСТВИЙ ЧЕРЧЕНИЯ. При обнаружении любого из них в запросе
# маршрутизатор ОБЯЗАН автоматически переключить выполнение на Чертёжника
# (agent_draftsman.py) и синхронизировать текст верхней Canvas-капсулы в GUI.
# Пользователь НЕ должен переключать агентов вручную — система решает сама.
#
# ВАЖНО: триггеры состоят ТОЛЬКО из глаголов-повелителей. Имена существительные
# («круг», «линия», «окружность», «диаметр») исключены, чтобы простое упоминание
# фигуры в вопросе или споре НЕ ложно переключало маршрутизатор на Чертёжника.
_DRAW_TRIGGERS = (
    # Глаголы Чертёжника (создание и построение геометрии).
    "черти", "начерти", "начертите", "рисуй", "нарисуй", "нарисуйте",
    "построй", "постройте", "создай", "создайте", "размести", "разместите",
    "вставь", "вставьте", "штрихуй", "заштрихуй", "выдави", "вращай",
    "скругли", "срежь", "подпиши",
    # Дополнительные повелительные глаголы черчения. Глагол «проставь» намеренно
    # исключён, т.к. фраза «проставь размер» относится к Аналитику и не должна
    # ложно переключать маршрутизатор на Чертёжника.
    "укажи", "укажите", "поставь", "поставьте", "отметь", "отметьте",
    "впиши", "впишите", "опиши", "опишите", "обведи", "обведите",
)


# ========================================================================
# РЕЕСТР ДОСТУПНЫХ ИНСТРУМЕНТОВ (Function Calling)
# ========================================================================
# Словарь связывает имя функции (которое модель вернёт в JSON) с реальной
# Python-функцией. Это безопасный диспетчер: вызываем только то, что явно
# зарегистрировано, без exec()/eval().

def _register_tools():
    """Динамически собирает реестр инструментов из модулей агентов.

    Импортирует функции из agent_draftsman.py, agent_analyst.py и
    agent_operator.py и регистрирует их по имени в общем словаре TOOLS.
    Импорт выполняется лениво, чтобы избежать циклических зависимостей.

    Возвращает:
        dict — словарь {имя_функции: вызываемая функция}.
    """
    tools = {}

    # Инструменты Чертёжника (построение геометрии, Блоки 1-3 спецификации).
    from agent_draftsman import (draw_circle, draw_line, draw_polyline,
                                 draw_rectangle, draw_text, draw_mtext,
                                 insert_block, draw_ellipse, fit_object_in_circle,
                                 fit_circle_to_object)
    tools.update({
        "draw_circle": draw_circle,
        "draw_line": draw_line,
        "draw_polyline": draw_polyline,
        "draw_rectangle": draw_rectangle,
        "draw_text": draw_text,
        "draw_mtext": draw_mtext,
        "insert_block": insert_block,
        "draw_ellipse": draw_ellipse,
        "fit_object_in_circle": fit_object_in_circle,
        "fit_circle_to_object": fit_circle_to_object,
    })

    # Инструменты Аналитика (чтение и инспекция данных, Блок 4 спецификации).
    from agent_analyst import (get_open_drawings, get_layers_list, get_sheets_list,
                               analyze_objects_summary, get_object_properties_by_handle,
                               get_selected_or_all_objects, check_lines_intersections,
                               get_intersection_points)
    tools.update({
        "get_open_drawings": get_open_drawings,
        "get_layers_list": get_layers_list,
        "get_sheets_list": get_sheets_list,
        "analyze_objects_summary": analyze_objects_summary,
        "get_object_properties_by_handle": get_object_properties_by_handle,
        "get_selected_or_all_objects": get_selected_or_all_objects,
        "check_lines_intersections": check_lines_intersections,
        "get_intersection_points": get_intersection_points,
    })

    # Инструменты Оператора (навигация и управление свойствами, Блок 5).
    from agent_operator import (switch_to_model, switch_to_sheet, switch_to_drawing,
                                change_object_color, move_object_to_layer,
                                change_layer_color)
    tools.update({
        "switch_to_model": switch_to_model,
        "switch_to_sheet": switch_to_sheet,
        "switch_to_drawing": switch_to_drawing,
        "change_object_color": change_object_color,
        "move_object_to_layer": move_object_to_layer,
        "change_layer_color": change_layer_color,
    })

    return tools


# Глобальный реестр инструментов (заполняется лениво при первом использовании).
TOOLS = {}


def _get_tools():
    """Возвращает реестр инструментов, заполняя его при первом обращении.

    Возвращает:
        dict — актуальный реестр {имя_функции: вызываемая функция}.
    """
    global TOOLS
    if not TOOLS:
        TOOLS = _register_tools()
    return TOOLS


# ========================================================================
# МАРШРУТИЗАЦИЯ НАМЕРЕНИЙ
# ========================================================================

# Соответствие лаконичных названий агентов (из UI) ключам конфигурации.
AGENT_LABEL_TO_KEY = {
    "Ассистент-Чертёжник": "draftsman",
    "Ассистент-Аналитик": "analyst",
    "Ассистент-Оператор": "operator",
    "Ассистент-Справочник": "reference",
}

# Обратный маппинг: ключ агента -> полное название для синхронизации Canvas-капсулы.
AGENT_KEY_TO_LABEL = {
    "draftsman": "Ассистент-Чертёжник",
    "analyst": "Ассистент-Аналитик",
    "operator": "Ассистент-Оператор",
    "reference": "Ассистент-Справочник",
}


def _resolve_preferred_agent(agent_label) -> str:
    """Преобразует название агента из UI в ключ конфигурации AGENTS_CONFIG.

    Если переданная строка не совпадает ни с одним известным названием агента,
    возвращается None, и маршрутизация продолжается по ключевым словам запроса.

    Аргументы:
        agent_label: лаконичное название агента (например, «Ассистент-Аналитик»).

    Возвращает:
        str | None — ключ агента ('draftsman', 'analyst', ...) либо None.
    """
    if not agent_label:
        return None
    label = str(agent_label).strip()
    # Прямое совпадение по полному имени.
    if label in AGENT_LABEL_TO_KEY:
        return AGENT_LABEL_TO_KEY[label]
    # Дополнительная проверка без учёта регистра и пробелов.
    normalized = label.lower().replace(" ", "")
    for known_label, key in AGENT_LABEL_TO_KEY.items():
        if known_label.lower().replace(" ", "") == normalized:
            return key
    return None


def _detect_intent(user_prompt: str):
    """Жёсткий классификатор намерений (Intent Classifier) по ключевым словам.

    Анализирует запрос независимо от выбранного в UI агента и возвращает ключ
    того агента, чьи ключевые слова встретились в тексте. Если совпадений нет —
    возвращается None, что позволяет маршрутизатору применить fallback.

    ЖЁСТКИЙ ПРИОРИТЕТ ЧЕРЧЕНИЯ: если пользователь написал команду действия
    («укажи», «начерти», «поставь», «круг», «линия»), выполнение АВТОМАТИЧЕСКИ
    переключается на Чертёжника — независимо от того, какой агент выбран в UI.
    Canvas-капсула синхронизируется позднее через ui.sync_agent(real_key).

    Аргументы:
        user_prompt: текст запроса пользователя.

    Возвращает:
        str | None — ключ агента ('draftsman', 'analyst', 'operator',
        'reference') либо None, если намерение не распознано.
    """
    prompt_lower = user_prompt.lower()
    # 1. Жёсткая автоматическая маршрутизация на Чертёжника по триггерам действия.
    for trigger in _DRAW_TRIGGERS:
        if trigger in prompt_lower:
            return "draftsman"
    # 2. Классический подбор агента по ключевым словам его конфигурации.
    for agent_key, agent_cfg in AGENTS_CONFIG.items():
        for keyword in agent_cfg["keywords"]:
            if keyword in prompt_lower:
                return agent_key
    # Намерение не распознано — вернём None, решение о fallback принимает вызывающий.
    return None


def _is_refusal(text: str) -> bool:
    """Определяет, отказалась ли модель отвечать на запрос.

    Проверяет текст ответа на типичные фразы отказа (например, когда Чертёжник
    не может предоставить название/аналитические данные). В этом случае
    маршрутизатор делегирует запрос Ассистенту-Аналитику.

    Аргументы:
        text: текст ответа модели.

    Возвращает:
        bool — True, если модель отказалась отвечать.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    low = text.lower()
    refusal_markers = (
        "я не могу", "не могу предоставить", "не могу ответить",
        "не могу определить", "не могу сказать", "не знаю название",
        "не знаю имя", "отказаться", "не могу назвать", "не в моих функциях",
        "не подходит для этой задачи", "я чертёжник",
    )
    return any(marker in low for marker in refusal_markers)


def _merge_scissors_context(user_prompt: str, scissors_context) -> str:
    """Подмешивает визуальный контекст «Ножниц» в запрос пользователя.

    Если передан scissors_context (текстовые координаты или описание выделенной
    области экрана из UI), строка добавляется к запросу как опорная визуальная
    подсказка для модели.

    Аргументы:
        user_prompt: исходный запрос пользователя.
        scissors_context: текст/координаты выделенной области или None.

    Возвращает:
        str — итоговый запрос с учётом контекста «Ножниц».
    """
    if scissors_context and str(scissors_context).strip():
        hint = str(scissors_context).strip()
        return f"{user_prompt}\n[Визуальный контекст «Ножниц»]: {hint}"
    return user_prompt


def _try_parse_tool_call(text) -> dict:
    """Пытается извлечь JSON-вызов функции из текстового ответа модели.

    Модель (например, qwen2.5-coder) иногда выдаёт вызов функции не в поле
    tool_calls, а в виде чистого JSON-блока в тексте. Функция пытается распарсить
    такой блок и вернуть словарь-вызов, чтобы оркестратор мог выполнить реальную
    Python-функцию, а не выводить сырой JSON в чат.

    Поддерживаемые форматы:
        {"name": "draw_circle", "arguments": {...}}
        {"function": {"name": "draw_circle", "arguments": "{...}"}}

    Аргументы:
        text: текстовый ответ модели.

    Возвращает:
        dict | None — нормализованный словарь вызова функции либо None.
    """
    import re
    if not isinstance(text, str) or not text.strip():
        return None
    # Кандидаты на JSON: весь текст целиком и первый блок в фигурных скобках.
    candidates = [text.strip()]
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        candidates.append(m.group(0).strip())
    for candidate in candidates:
        # Отбрасываем JSON-массивы и пустые объекты.
        if not (candidate.startswith("{") and candidate.endswith("}")):
            continue
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        # Нормализуем формат {"function": {...}}.
        fc = data.get("function", data)
        if isinstance(fc, dict) and fc.get("name"):
            return fc
        if data.get("name"):
            return data
    return None


# ========================================================================
# ЖЁСТКИЙ ПАРСЕР ЧИСЛОВЫХ АРГУМЕНТОВ ИЗ ТЕКСТА ЗАПРОСА
# ========================================================================
# Модель Ollama иногда возвращает Function Calling с пустыми/None координатами
# и радиусом. Чтобы параметры не улетали как None, ниже описан надёжный парсер,
# который извлекает числа прямо из текста запроса пользователя и подставляет
# их в недостающие аргументы геометрических функций.

# Регулярное выражение для поиска чисел (целых и дробных, с запятой или точкой).
_NUMBER_PATTERN = re.compile(r"-?\d+(?:[.,]\d+)?")


def _to_float(value):
    """Безопасно преобразует значение в float (запятая-разделитель -> точка).

    Аргументы:
        value: произвольное значение из аргументов модели.

    Возвращает:
        float | None — число либо None, если преобразовать не удалось.
    """
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace(",", ".")
        return float(text) if text else None
    except Exception:
        return None


def _extract_numbers(text):
    """Извлекает из текста все числа в порядке их появления.

    Аргументы:
        text: исходная строка запроса.

    Возвращает:
        list[float] — список найденных чисел.
    """
    return [float(m.replace(",", ".")) for m in _NUMBER_PATTERN.findall(text)]


def _parse_radius(text):
    """Достаёт радиус по русским ключевым словам («радиус», «радиусом»).

    Аргументы:
        text: текст запроса пользователя.

    Возвращает:
        float | None — радиус либо None, если в тексте его нет.
    """
    m = re.search(
        r"радиус[а-я]*\s*[:=]?\s*(-?\d+(?:[.,]\d+)?)", text, re.IGNORECASE)
    if m:
        return _to_float(m.group(1))
    return None


def _parse_point(text):
    """Достаёт пару координат точки из явного упоминания в тексте.

    Распознаёт конструкции вида «в точке 0,0», «точке (12; 5)»,
    «в координатах 3 7» и аналогичные.

    Аргументы:
        text: текст запроса пользователя.

    Возвращает:
        tuple(float, float) | None — координаты либо None.
    """
    m = re.search(
        r"(?:в точке|точке|в координатах|по координатам|в координату|координаты)"
        r"\s*\(?\s*(-?\d+(?:[.,]\d+)?)\s*[,;]\s*(-?\d+(?:[.,]\d+)?)\s*\)?",
        text, re.IGNORECASE)
    if m:
        return _to_float(m.group(1)), _to_float(m.group(2))
    return None


# Соответствие имени функции её числовым параметрам (для точечного заполнения).
_GEOMETRY_NUMERIC_KEYS = {
    "draw_circle": ["x", "y", "radius"],
    "draw_line": ["x1", "y1", "x2", "y2"],
    "draw_rectangle": ["x1", "y1", "x2", "y2"],
    "draw_text": ["x", "y"],
    "draw_mtext": ["x", "y"],
    "insert_block": ["x", "y"],
    "draw_ellipse": ["center_x", "center_y", "major_x", "major_y", "radius_ratio"],
}


def _fill_geometry_args(name, args, prompt):
    """Заполняет недостающие числовые аргументы из текста запроса.

    Если модель вернула Function Calling с пустыми/None координатами и радиусом,
    жёсткий парсер извлекает числа прямо из текста запроса пользователя и
    подставляет их в аргументы функции. Координаты и радиус никогда не уходят
    в агента как None.

    Аргументы:
        name: имя вызываемой функции.
        args: словарь аргументов от модели.
        prompt: исходный текст запроса пользователя.

    Возвращает:
        dict — обогащённый словарь аргументов.
    """
    if not isinstance(args, dict):
        args = {}
    if not isinstance(prompt, str) or not prompt.strip():
        return args

    # Приводим уже переданные строковые числа к типу float.
    for key in list(args.keys()):
        converted = _to_float(args[key])
        if converted is not None:
            args[key] = converted

    numeric_keys = _GEOMETRY_NUMERIC_KEYS.get(name, [])

    def _is_empty(key):
        """Возвращает True, если аргумент пуст (None, '' или отсутствует)."""
        return args.get(key) in (None, "")

    # 1. Радиус — ищем по ключевому слову «радиус/радиусом».
    if "radius" in numeric_keys and _is_empty("radius"):
        r = _parse_radius(prompt)
        if r is not None:
            args["radius"] = r

    # 2. Точка/центр — ищем по явному упоминанию координат в тексте.
    pt = _parse_point(prompt)
    if pt is not None:
        xy_keys = ("center_x", "center_y") if name == "draw_ellipse" else ("x", "y")
        for idx, key in enumerate(xy_keys):
            if key in numeric_keys and _is_empty(key):
                args[key] = pt[idx]

    # 3. Общий fallback: оставшиеся пустые аргументы заполняем первыми
    #    свободными числами из текста по порядку их появления.
    numbers = _extract_numbers(prompt)
    cursor = 0
    for key in numeric_keys:
        # Соотношение радиусов эллипса вслепую не подставляем — рискуем
        # испортить пропорции фигуры без явного указания в тексте.
        if key == "radius_ratio":
            continue
        if _is_empty(key):
            while cursor < len(numbers):
                args[key] = numbers[cursor]
                cursor += 1
                break

    return args


# ========================================================================
# ЖЁСТКАЯ ОЧИСТКА АРГУМЕНТОВ ОТ СТРОКОВЫХ МАРКЕРОВ КООРДИНАТ
# ========================================================================
# Локальные модели иногда возвращают в JSON вызова функции одиночные символы
# 'x', 'y' или пустые строки вместо реальных чисел. Если такой мусор уйдёт в
# математические функции или в методы AutoCAD — возникнет ошибка
# "could not convert string to float". Ниже всё это жёстко вычищается.

# Имена ключей, хранящих числовые координаты/размеры. Для них строковый мусор
# категорически недопустим и заменяется безопасным нулём.
_COORD_KEYS = {
    "x", "y", "z", "x1", "y1", "x2", "y2",
    "center_x", "center_y", "major_x", "major_y",
    "radius", "radius_ratio", "cx", "cy",
}

# Имена ключей, хранящих списки вершин полилиний / массивов точек.
_POINTS_LIST_KEYS = {"points_list", "vertices_list"}


def _to_float_or_zero(value):
    """Преобразует значение в float либо безопасно возвращает 0.0.

    Аргументы:
        value: произвольное значение (число, строка, None, буква-маркер).

    Возвращает:
        float — число либо 0.0, если преобразовать значение невозможно.
    """
    converted = _to_float(value)
    return converted if converted is not None else 0.0


def _clean_points_list(value):
    """Приводит список вершин полилинии к чистому списку чисел.

    Каждый элемент (число или вложенная пара [x, y]) принудительно приводится
    к float; непереводимые значения заменяются на 0.0. Пустой/некорректный вход
    превращается в пустой список, чтобы не сломать контракт функции агента.

    Аргументы:
        value: список вершин (плоский либо список пар/кортежей).

    Возвращает:
        list — очищенный список чисел либо списков чисел.
    """
    if not isinstance(value, (list, tuple)):
        return []
    cleaned = []
    for item in value:
        if isinstance(item, (list, tuple)):
            # Вложенная точка — чистим каждую координату.
            cleaned.append([_to_float_or_zero(coord) for coord in item])
        else:
            cleaned.append(_to_float_or_zero(item))
    return cleaned


def _sanitize_geometry_args(name, args):
    """Жёстко очищает словарь аргументов от строковых маркеров координат.

    Вызывается перед передачей параметров в функции agent_draftsman/agent_analyst.
    Ключи координат (x, y, radius, center_x и т.п.) очищаются через _to_float,
    а при невозможности конвертации подставляется дефолт 0.0. Списки вершин
    (points_list, vertices_list) чистятся поэлементно. Таким образом сырой текст
    ('x', 'y', '') физически не может попасть в математические методы и САПР.

    Аргументы:
        name: имя вызываемой функции (не используется, но сохранено для будущих
              специфичных правил по конкретным инструментам).
        args: исходный словарь аргументов от LLM-модели.

    Возвращает:
        dict — очищенный словарь аргументов (гарантированно без строковых маркеров
        координат). Если вход не является словарём, возвращается пустой словарь.
    """
    if not isinstance(args, dict):
        return {}

    cleaned = {}
    for key, value in args.items():
        if key in _COORD_KEYS:
            # Координаты/радиус: строковый мусор превращаем в безопасный ноль.
            cleaned[key] = _to_float_or_zero(value)
        elif key in _POINTS_LIST_KEYS:
            # Списки вершин: чистим каждую точку/координату.
            cleaned[key] = _clean_points_list(value)
        else:
            # Прочие ключи (object_handle, layer_name, color, drawing_name и т.п.)
            # оставляем нетронутыми, чтобы не портить нечисловые параметры.
            cleaned[key] = value
    return cleaned


def _notify_status(status_callback, stage):
    """Безопасно вызывает колбэк обновления статуса процесса в UI.

    Аргументы:
        status_callback: вызываемый объект UI (или None).
        stage: строка этапа ('thinking', 'building', 'analyzing').
    """
    if status_callback:
        try:
            status_callback(stage)
        except Exception:
            pass


# ========================================================================
# МЕХАНИЗМ РАЗРЕШЕНИЯ АНАФОРЫ (STATE MANAGER)
# ========================================================================
# Словарь отслеживания контекста диалога. Позволяет агентам безошибочно понимать
# местоимения («он», «его», «эту деталь», «тут») на основе истории действий.
# Ключи:
#   active_document    — имя чертежа, с которым работал Аналитик;
#   last_object_handle — Handle последнего объекта, построенного Чертёжником;
#   last_layer_name    — имя слоя, цвет которого менял Оператор.
CURRENT_CONTEXT_STATE = {
    "active_document": None,
    "last_object_handle": None,
    "last_layer_name": None,
}

# Множество функций построения геометрии, возвращающих Handle созданного объекта.
_DRAW_FUNCTIONS = {
    "draw_circle", "draw_line", "draw_polyline", "draw_rectangle",
    "draw_text", "draw_mtext", "insert_block",
    "draw_ellipse", "fit_object_in_circle", "fit_circle_to_object",
}

# Множество функций, после которых ОБЯЗАТЕЛЬНО нужно обновить экран AutoCAD
# (команда doc.Update()), чтобы пользователь сразу видел результат черчения
# или изменения свойств на графическом экране.
_SCREEN_REFRESH_FUNCTIONS = _DRAW_FUNCTIONS | {
    "change_object_color", "change_layer_color", "move_object_to_layer",
    "switch_to_model", "switch_to_sheet", "switch_to_drawing",
}


def _build_anaphora_hint():
    """Формирует системную подсказку-подстраховку на основе словаря состояния.

    Подмешивается в системный промпт Аналитика и Чертёжника, чтобы локальная
    модель знала, что под местоимениями «он», «его», «этот» подразумевается
    последний обсуждаемый объект и чертёж из истории действий.

    Возвращает:
        str — строка-подсказка либо пустая строка, если контекст пуст.
    """
    # Handle берём из классического словаря либо из стека онтологии (резерв).
    handle = CURRENT_CONTEXT_STATE.get("last_object_handle") or ONTOLOGY_STATE.latest_handle()
    document = CURRENT_CONTEXT_STATE.get("active_document")
    # Если история пуста, подсказка не нужна (не засоряем промпт).
    if not handle and not document:
        return ""
    return (
        "[Справочник анафоры для ИИ]: В текущий момент времени под местоимениями "
        "'он', 'его', 'этот' подразумевается: объект с Handle: "
        f"{handle or 'не указан'} и файл чертежа: {document or 'не указан'}. "
        "Если пользователь спрашивает про свойства или модификацию 'его', "
        "используй эти значения для вызова соответствующих Python-функций!"
    )


def _build_scissors_system_note() -> str:
    """Формирует системную заметку с визуальным контекстом «Ножниц».

    Заметка встраивается в системный промпт КАЖДОГО агента через _ask_ollama,
    чтобы модель знала о выделенной пользователем области экрана и никогда не
    отвечала «Я не могу просмотреть визуальный контекст».

    Возвращает:
        str — строка-заметка либо пустая строка, если контекст не зафиксирован.
    """
    ctx = _CURRENT_SCISSORS_CONTEXT
    if not ctx or not str(ctx).strip():
        return ""
    return (
        "[Визуальный контекст «Ножниц» (ОБЯЗАТЕЛЬНО УЧИТЫВАЙ): пользователь "
        "выделил рамкой область экрана, в которой находятся объекты: "
        f"{str(ctx).strip()}. НЕ заявляй, что не видишь визуальный контекст — "
        "анализируй фигуры по этому описанию и используй их в ответе. Если на "
        "экране квадрат, называй его строго «Квадрат», окружность — «Круг»."
    )


def _extract_active_document(data):
    """Извлекает имя активного чертежа из списка открытых документов.

    Аргументы:
        data: список строк вида «имя.dwg (активный)».

    Возвращает:
        str | None — имя активного документа либо None, если его нет.
    """
    if isinstance(data, list):
        for item in data:
            text = str(item)
            if "активн" in text.lower():
                return text.replace("(активный)", "").strip()
    return None


def _update_context_state(name, args, result):
    """Автоматически наполняет словарь контекста (State Manager).

    Отслеживает последний чертёж (Аналитик), Handle последнего построенного
    объекта (Чертёжник) и имя последнего изменённого слоя (Оператор), чтобы
    агенты могли разрешать местоимения из истории действий.

    Аргументы:
        name: имя вызванной функции.
        args: словарь аргументов вызова.
        result: результат выполнения функции.
    """
    if not isinstance(result, dict) or result.get("status") != "success":
        return
    # Чертёжник успешно построил объект — запоминаем его Handle в классическом
    # словаре и в умном стеке онтологии (для разрешения множественных анафор).
    if name in _DRAW_FUNCTIONS and result.get("handle"):
        handle = str(result["handle"])
        CURRENT_CONTEXT_STATE["last_object_handle"] = handle
        ONTOLOGY_STATE.push_object(handle, obj_type=name, agent="draftsman")
    # Аналитик работал с конкретным чертежом по имени — фиксируем его.
    if name in ("get_layers_list", "get_sheets_list") and args.get("drawing_name"):
        CURRENT_CONTEXT_STATE["active_document"] = str(args["drawing_name"])
    # Оператор изменил цвет слоя — запоминаем имя слоя.
    if name == "change_layer_color" and args.get("layer_name"):
        CURRENT_CONTEXT_STATE["last_layer_name"] = str(args["layer_name"])
    # Получен список открытых чертежей — извлекаем активный документ.
    if name == "get_open_drawings":
        active_doc = _extract_active_document(result.get("data"))
        if active_doc:
            CURRENT_CONTEXT_STATE["active_document"] = active_doc
    # Аналитик распознал конкретную геометрию (круг/квадрат) — запоминаем факт
    # в GlobalStateManager и умном стеке онтологии, чтобы последующие команды
    # Чертёжника («впиши круг в квадрат») работали без потери нити разговора.
    if name == "get_object_properties_by_handle" and isinstance(result.get("data"), dict):
        odata = result["data"]
        handle = str(odata.get("handle", "")).strip()
        obj_type_low = str(odata.get("object_name", "") or "").lower()
        if handle:
            if "circle" in obj_type_low:
                ONTOLOGY_STATE.push_object(handle, obj_type="Круг", agent="analyst")
            elif "polyline" in obj_type_low or "lwpolyline" in obj_type_low:
                # Полилиния из четырёх вершин воспринимается пользователем как квадрат.
                ONTOLOGY_STATE.push_object(handle, obj_type="Квадрат", agent="analyst")
            else:
                ONTOLOGY_STATE.push_object(handle, obj_type=obj_type_low or "объект",
                                           agent="analyst")


# Множество функций, работающих с одиночным Handle объекта (для сценария Цикла).
_HANDLE_TOOL_FUNCTIONS = {"fit_object_in_circle", "fit_circle_to_object", "change_object_color"}

# Функции, требующие РЕАЛЬНЫЙ Handle объекта (передаётся в HandleToObject/IntersectWith).
# Если модель вернула строковую заглушку ('<selected-object-handle>', 'selected'
# и т.п.) или пустое значение, маршрутизатор принудительно подставляет живой
# дескриптор из выделения пользователя либо из стека онтологии — минуя LLM.
_HANDLE_FUNCTIONS_NEEDING_REAL = _HANDLE_TOOL_FUNCTIONS | {
    "get_object_properties_by_handle",
}


def _resolve_anaphora_in_args(name, args, user_prompt):
    """Интеллектуально разрешает множественные анафоры перед вызовом функции.

    Сценарий Уточнения: если пользователь использует одиночное местоимение
    («удали его», «его свойства») и в стеке онтологии лежат несколько разнородных
    объектов, созданных недавно, выполнение функции ЗАПРЕЩАЕТСЯ. Маршрутизатор
    возвращает вежливый уточняющий запрос на русском языке вместо выполнения.

    Аргументы:
        name: имя вызываемой функции.
        args: словарь аргументов.
        user_prompt: исходный текст запроса пользователя.

    Возвращает:
        tuple (args, block_message): при блокировке args=None, а block_message
        содержит уточняющий вопрос; иначе block_message равен None.
    """
    # Двусмысленность контекста одиночной анафоры — блокируем выполнение.
    if mentions_singular_anaphora(user_prompt) and ONTOLOGY_STATE.is_ambiguous():
        return None, ONTOLOGY_STATE.clarification_message()
    return args, None


def _execute_cycle(target, name, base_args, handles, user_prompt):
    """Последовательно выполняет функцию для каждого Handle активной группы.

    Сценарий Цикла («в каждом», «для всех»): для каждого Handle из массива
    аргумент object_handle подменяется, функция вызывается итерационно, а
    результаты агрегируются в единый словарь. Стек памяти обновляется по каждой
    итерации.

    Аргументы:
        target: вызываемая Python-функция агента.
        name: имя вызываемой функции.
        base_args: базовые аргументы вызова.
        handles: список Handle, по которым нужно итерироваться.
        user_prompt: исходный текст запроса (для обновления контекста).

    Возвращает:
        dict — агрегированный результат либо сообщение об ошибке.
    """
    results = []
    errors = []
    for handle in handles:
        item_args = dict(base_args)
        item_args["object_handle"] = handle
        try:
            res = target(**item_args)
            _update_context_state(name, item_args, res)
            if isinstance(res, dict) and res.get("status") == "success":
                results.append(res.get("message") or f"Объект {handle} обработан.")
            else:
                errors.append(f"Handle {handle}: {res}")
        except Exception as e:
            errors.append(f"Handle {handle}: {e}")
    if results:
        summary = "Обработано объектов: " + " | ".join(results)
        if errors:
            summary += "\nОшибки: " + "; ".join(errors)
        return {"status": "success", "data": "", "message": summary}
    return {"status": "error",
            "message": "Не удалось обработать объекты: " + "; ".join(errors)}


def route_request(user_prompt: str, scissors_context=None, preferred_agent: str = None,
                  status_callback=None):
    """Маршрутизирует запрос пользователя к нужному агенту и выполняет его.

    Последовательность действий:
        1. Подмешивает контекст «Ножниц» (если передан).
        2. Определяет намерение (Intent) по ключевым словам, но при наличии
           явного выбора агента в UI (preferred_agent) отдаёт ему приоритет.
        3. Выбирает модель из AGENTS_CONFIG.
        4. Отправляет системный промпт + список доступных функций в Ollama.
        5. Получает Function Calling (JSON с именем функции и аргументами).
        6. Безопасно выполняет функцию через реестр TOOLS.
        7. Возвращает структурированный результат.

    Аргументы:
        user_prompt: текст запроса пользователя.
        scissors_context: опциональный визуальный контекст «Ножниц».
        preferred_agent: лаконичное имя агента, выбранного в UI
            (например, «Ассистент-Чертёжник»), либо None.

    Возвращает:
        dict — {"status": "success", "agent": "...", "data": ...} либо
               {"status": "error", "message": "..."}.
    """
    try:
        # Глобальная привязка визуального контекста «Ножниц»: фиксируем его для
        # принудительной инъекции в системный промпт ВСЕХ агентов внутри _ask_ollama.
        global _CURRENT_SCISSORS_CONTEXT
        _CURRENT_SCISSORS_CONTEXT = scissors_context

        # 1. Интеграция «Ножниц»: подмешиваем визуальную подсказку.
        enriched_prompt = _merge_scissors_context(user_prompt, scissors_context)

        # 2. ДИНАМИЧЕСКАЯ МАРШРУТИЗАЦИЯ. Жёсткий классификатор намерений
        #    анализирует запрос независимо от того, какой агент выбран в UI.
        #    Выбор в выпадающем списке используется только как fallback, если
        #    намерение по ключевым словам не распознано.
        intent = _detect_intent(enriched_prompt)
        if intent is None:
            intent = _resolve_preferred_agent(preferred_agent) or "reference"
        agent_cfg = AGENTS_CONFIG[intent]
        system_logs = []

        # 2.1. Динамическая индикация процесса для UI: в зависимости от намерения
        # показываем, что именно делает ассистент (размышляет / строит / анализирует).
        if intent == "reference":
            _notify_status(status_callback, "thinking")
        elif intent == "analyst":
            _notify_status(status_callback, "analyzing")
        else:
            _notify_status(status_callback, "building")

        # ПЕРЕХВАТ «ТОЧЕК ПЕРЕСЕЧЕНИЯ»: выполняем напрямую, минуя парсинг пикселей
        # со скриншота «Ножниц» (проблема масштабирования 2К/4К) и вызов Ollama.
        # Берём Handles последних объектов из GlobalStateManager, получаем истинные
        # CAD-координаты от метода IntersectWith и ставим круги-маркеры.
        intersection_result = _try_handle_intersections(enriched_prompt)
        if intersection_result is not None:
            return {
                "status": "success",
                "agent": intent,
                "agent_name": agent_cfg["name"],
                "data": intersection_result,
                "system_logs": system_logs,
            }

        # 3. Для Справочника результат — просто сгенерированный моделью текст.
        if intent == "reference":
            answer = _ask_ollama(
                model=agent_cfg["model"],
                system_prompt=agent_cfg["system_prompt"],
                user_message=enriched_prompt,
                tools=None,
                agent_key=intent,
            )
            return {
                "status": "success",
                "agent": intent,
                "agent_name": agent_cfg["name"],
                "data": answer,
                "system_logs": system_logs,
            }

        # 4. Для остальных агентов запрашиваем Function Calling с описанием функций.
        function_call = _ask_ollama(
            model=agent_cfg["model"],
            system_prompt=agent_cfg["system_prompt"],
            user_message=enriched_prompt,
            tools=_describe_tools(intent),
            agent_key=intent,
        )

        # 5. Если модель ответила текстом, а не вызвала функцию:
        if not isinstance(function_call, dict):
            text_answer = (function_call if isinstance(function_call, str)
                           and function_call.strip()
                           else "Модель не вернула инструментальный вызов.")
            # ПЕРЕХВАТ JSON-ВЫЗОВА ФУНКЦИИ. Модель (например, qwen2.5-coder)
            # могла вернуть чистый JSON-блок вместо структурированного tool_calls.
            # Пытаемся распарсить его и выполнить реальную Python-функцию,
            # чтобы сырой JSON не попадал в окно истории чата.
            parsed_call = _try_parse_tool_call(text_answer)
            if parsed_call is not None:
                executed = _execute_function_call(parsed_call, enriched_prompt)
                if executed is None:
                    executed = {"status": "info", "data": "",
                                "message": "Операция выполнена успешно (результат пуст)."}
                return {
                    "status": "success",
                    "agent": intent,
                    "agent_name": agent_cfg["name"],
                    "data": executed,
                    "system_logs": system_logs,
                }
            # РЕЗЕРВНЫЙ СЦЕНАРИЙ (Fallback). Если модель не вернула ни функции,
            # ни распознаваемого JSON, но в тексте явно есть команда черчения
            # (например, «начерти круг радиусом 80»), принудительно выполняем её,
            # чтобы вместо сырого JSON в чат ушло реальное действие.
            guessed_name, guessed_args = _guess_draw_tool_from_prompt(
                enriched_prompt, _get_tools())
            if guessed_name is not None:
                executed = _execute_function_call(
                    {"name": guessed_name, "arguments": guessed_args}, enriched_prompt)
                if executed is None:
                    executed = {"status": "info", "data": "",
                                "message": "Операция выполнена успешно (результат пуст)."}
                return {
                    "status": "success",
                    "agent": intent,
                    "agent_name": agent_cfg["name"],
                    "data": executed,
                    "system_logs": system_logs,
                }
            # ВЕЖЛИВОЕ ДЕЛЕГИРОВАНИЕ. Чертёжник (или другой агент) отказался
            # отвечать — автоматически перенаправляем запрос Аналитику.
            if intent != "analyst" and _is_refusal(text_answer):
                system_logs.append(
                    "[Система]: Запрос перенаправлен Ассистенту-Аналитику "
                    "(агент не смог ответить).")
                analyst_cfg = AGENTS_CONFIG["analyst"]
                fc2 = _ask_ollama(
                    model=analyst_cfg["model"],
                    system_prompt=analyst_cfg["system_prompt"],
                    user_message=enriched_prompt,
                    tools=_describe_tools("analyst"),
                    agent_key="analyst",
                )
                if isinstance(fc2, dict):
                    delegated = _execute_function_call(fc2, enriched_prompt)
                else:
                    fc2_text = (fc2 if isinstance(fc2, str) and fc2.strip() else "")
                    # И у Аналитика перехватываем JSON-вызов функции из текста.
                    parsed2 = _try_parse_tool_call(fc2_text)
                    if parsed2 is not None:
                        delegated = _execute_function_call(parsed2, enriched_prompt)
                    else:
                        delegated = fc2_text or "Аналитик не вернул результат."
                if delegated is None:
                    delegated = {"status": "info", "data": "",
                                 "message": "Операция выполнена успешно (результат пуст)."}
                return {
                    "status": "success",
                    "agent": "analyst",
                    "agent_name": analyst_cfg["name"],
                    "data": delegated,
                    "system_logs": system_logs,
                }
            # Обычный текстовый ответ (например, у Аналитика на DeepSeek-R1).
            return {
                "status": "success",
                "agent": intent,
                "agent_name": agent_cfg["name"],
                "data": text_answer,
                "system_logs": system_logs,
            }

        # 6. Выполняем выбранную функцию через реестр TOOLS (без exec/eval).
        result = _execute_function_call(function_call, enriched_prompt)
        # Защита от значения None: никогда не показываем пользователю «None».
        if result is None:
            result = {"status": "info", "data": "",
                      "message": "Операция выполнена успешно (результат пуст)."}
        return {
            "status": "success",
            "agent": intent,
            "agent_name": agent_cfg["name"],
            "data": result,
            "system_logs": system_logs,
        }
    except requests.exceptions.ConnectionError:
        return {"status": "error", "message": OLLAMA_ERROR_MESSAGE}
    except Exception as e:
        return {"status": "error", "message": f"Ошибка обработки запроса: {e}"}


# ========================================================================
# ОПИСАНИЕ ФУНКЦИЙ ДЛЯ OLLAMA (Function Calling)
# ========================================================================

def _describe_tools(agent_key: str) -> list:
    """Формирует список доступных функций для конкретного агента.

    Каждая функция описывается JSON-схемой (имя и параметры), которую модель
    использует для генерации Function Calling. Для каждого агента возвращается
    только его собственный набор инструментов.

    Аргументы:
        agent_key: ключ агента ('draftsman', 'analyst' или 'operator').

    Возвращает:
        list — список словарей-описаний функций для передачи в Ollama.
    """
    descriptions = {
        "draftsman": [
            {
                "type": "function",
                "function": {
                    "name": "draw_circle",
                    "description": "Построить круг с заданным центром и радиусом.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "radius": {"type": "number"},
                            "color": {"type": "integer", "default": 7},
                        },
                        "required": ["x", "y", "radius"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "draw_polyline",
                    "description": "Построить полилинию по списку точек.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "points_list": {"type": "array"},
                            "is_closed": {"type": "boolean", "default": False},
                        },
                        "required": ["points_list"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "draw_rectangle",
                    "description": "Построить прямоугольник по двум противоположным углам.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "x1": {"type": "number"},
                            "y1": {"type": "number"},
                            "x2": {"type": "number"},
                            "y2": {"type": "number"},
                        },
                        "required": ["x1", "y1", "x2", "y2"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "draw_line",
                    "description": "Построить отрезок по двум конечным точкам.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "x1": {"type": "number"},
                            "y1": {"type": "number"},
                            "x2": {"type": "number"},
                            "y2": {"type": "number"},
                        },
                        "required": ["x1", "y1", "x2", "y2"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "draw_text",
                    "description": "Вставить однострочный текст в заданной точке.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "height": {"type": "number", "default": 2.5},
                        },
                        "required": ["text", "x", "y"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "draw_mtext",
                    "description": "Вставить многострочный текст в заданной точке.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "width": {"type": "number", "default": 100.0},
                        },
                        "required": ["text", "x", "y"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "insert_block",
                    "description": "Вставить блок по имени в заданной точке.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "block_name": {"type": "string"},
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "scale": {"type": "number", "default": 1.0},
                            "rotation_rad": {"type": "number", "default": 0.0},
                        },
                        "required": ["block_name", "x", "y"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "draw_ellipse",
                    "description": "Построить эллипс по центру и большой оси.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "center_x": {"type": "number"},
                            "center_y": {"type": "number"},
                            "major_x": {"type": "number"},
                            "major_y": {"type": "number"},
                            "radius_ratio": {"type": "number"},
                        },
                        "required": ["center_x", "center_y", "major_x", "major_y", "radius_ratio"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fit_object_in_circle",
                    "description": "Описать существующий объект в окружность по Handle.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "object_handle": {"type": "string"},
                            "padding": {"type": "number", "default": 10.0},
                        },
                        "required": ["object_handle"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fit_circle_to_object",
                    "description": "Вписать (inscribed) или описать (described) окружность вокруг объекта по Handle.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "object_handle": {"type": "string"},
                            "mode": {"type": "string", "enum": ["described", "inscribed"], "default": "described"},
                        },
                        "required": ["object_handle"],
                    },
                },
            },
        ],
        "analyst": [
            {
                "type": "function",
                "function": {
                    "name": "get_open_drawings",
                    "description": "Получить список открытых чертежей.",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_layers_list",
                    "description": "Получить список слоёв (необязательно для конкретного чертежа).",
                    "parameters": {
                        "type": "object",
                        "properties": {"drawing_name": {"type": "string", "default": None}},
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_sheets_list",
                    "description": "Получить список листов Layouts (исключая Model).",
                    "parameters": {
                        "type": "object",
                        "properties": {"drawing_name": {"type": "string", "default": None}},
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "analyze_objects_summary",
                    "description": "Собрать сводную статистику по типам объектов в модели.",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_object_properties_by_handle",
                    "description": "Прочитать свойства объекта по его Handle (тип, слой, цвет, геометрия).",
                    "parameters": {
                        "type": "object",
                        "properties": {"handle": {"type": "string"}},
                        "required": ["handle"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_selected_or_all_objects",
                    "description": "Получить Handle выделенных объектов либо всех объектов модели.",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "check_lines_intersections",
                    "description": "Найти точки пересечения отрезков AcDbLine.",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_intersection_points",
                    "description": "Найти истинные CAD-координаты точек пересечения двух объектов по их Handle (IntersectWith).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "handle1": {"type": "string"},
                            "handle2": {"type": "string"},
                        },
                        "required": ["handle1", "handle2"],
                    },
                },
            },
        ],
        "operator": [
            {
                "type": "function",
                "function": {
                    "name": "switch_to_model",
                    "description": "Переключить окно на Пространство Модели.",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "switch_to_sheet",
                    "description": "Переключить экран на конкретный лист по имени.",
                    "parameters": {
                        "type": "object",
                        "properties": {"sheet_name": {"type": "string"}},
                        "required": ["sheet_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "switch_to_drawing",
                    "description": "Активировать другую открытую вкладку чертежа.",
                    "parameters": {
                        "type": "object",
                        "properties": {"drawing_name": {"type": "string"}},
                        "required": ["drawing_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "change_object_color",
                    "description": "Изменить цвет объекта по Handle (цвет - название или ACI).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "object_handle": {"type": "string"},
                            "color_input": {},
                        },
                        "required": ["object_handle", "color_input"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "move_object_to_layer",
                    "description": "Перенести объект на указанный слой по его Handle.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "object_handle": {"type": "string"},
                            "layer_name": {"type": "string"},
                        },
                        "required": ["object_handle", "layer_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "change_layer_color",
                    "description": "Изменить цвет слоя по имени (цвет - название или ACI).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "layer_name": {"type": "string"},
                            "color_input": {},
                        },
                        "required": ["layer_name", "color_input"],
                    },
                },
            },
        ],
    }
    return descriptions.get(agent_key, [])


# ========================================================================
# ВЫЗОВ OLLAMA И РАЗБОР FUNCTION CALLING
# ========================================================================

def _ask_ollama(model: str, system_prompt: str, user_message: str, tools,
                agent_key: str = None):
    """Отправляет запрос в локальную Ollama и возвращает ответ модели.

    Выполняет POST-запрос на http://localhost:11434/api/chat. При наличии списка
    tools модель получает описание доступных функций и возвращает Function Calling
    (JSON с полем tool_calls) либо текстовый ответ. Перед каждым запросом в
    скрытый системный контекст принудительно подмешивается глобальная онтология
    (шпаргалка терминов AutoCAD + срез памяти проекта), а для Аналитика и
    Чертёжника дополнительно — динамическая подстраховка анафоры.

    Аргументы:
        model: имя модели Ollama.
        system_prompt: системный промпт агента.
        user_message: сообщение пользователя.
        tools: список описаний функций (или None для чисто текстового ответа).
        agent_key: ключ агента ('analyst', 'draftsman', ...) для инъекции контекста.

    Возвращает:
        str | dict — текстовый ответ либо словарь Function Calling.

    Исключения:
        requests.exceptions.ConnectionError — если Ollama недоступна.
    """
    # Глобальная онтология: подмешиваем шпаргалку терминов AutoCAD и срез стека
    # памяти в системный контекст перед каждым запросом (Qwen Coder, DeepSeek-R1).
    ontology_prompt = ONTOLOGY_STATE.render_thesaurus_prompt(agent_key)
    if ontology_prompt:
        system_prompt = f"{system_prompt}\n{ontology_prompt}"
    # Динамическая подстраховка анафоры для локальной LLM: подмешиваем справочник
    # анафоры в системный промпт Аналитика и Чертёжника, чтобы модель не задавала
    # встречные вопросы, а сразу использовала объект из истории действий.
    if agent_key in ("analyst", "draftsman"):
        anaphora_hint = _build_anaphora_hint()
        if anaphora_hint:
            system_prompt = f"{system_prompt}\n{anaphora_hint}"
    # ПРИНУДИТЕЛЬНАЯ инъекция визуального контекста «Ножниц» в системный промпт
    # ВСЕХ агентов (Аналитик, Чертёжник, Оператор, Справочник). Модель больше
    # никогда не имеет права отвечать «Я не могу просмотреть визуальный контекст».
    scissors_note = _build_scissors_system_note()
    if scissors_note:
        system_prompt = f"{system_prompt}\n{scissors_note}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "stream": False,
    }
    if tools:
        payload["tools"] = tools

    response = requests.post(OLLAMA_URL, json=payload, timeout=120)
    response.raise_for_status()
    data = response.json()

    message = data.get("message", {})
    tool_calls = message.get("tool_calls")
    if tool_calls:
        # Возвращаем первый инструментальный вызов модели.
        return tool_calls[0]
    # Если вызовов инструментов нет — возвращаем обычный текст.
    return message.get("content", "")


def _extract_function_call(function_call) -> tuple:
    """Извлекает имя функции и её аргументы из ответа модели.

    Поддерживает два формата:
        1. dict вида {"function": {"name": ..., "arguments": ...}};
        2. dict вида {"name": ..., "arguments": ...} (аргументы могут быть
           строкой JSON или уже готовым словарём).

    Аргументы:
        function_call: словарь инструментального вызова от модели.

    Возвращает:
        tuple (name, arguments_dict) либо (None, {}) при невозможности разбора.
    """
    if not isinstance(function_call, dict):
        return None, {}
    # Нормализуем: извлекаем внутренний объект function, если он есть.
    fc = function_call.get("function", function_call)
    name = fc.get("name") or ""
    if not name:
        return None, {}

    raw_args = fc.get("arguments", {})
    args = raw_args
    # Аргументы могут прийти строкой JSON — тогда распарсиваем.
    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args)
        except Exception:
            args = {}
    if not isinstance(args, dict):
        args = {}
    return name, args


# Регулярное выражение для поиска радиуса в тексте запроса («радиусом 80»).
_DRAW_RADIUS_RE = re.compile(r"(?:радиус[а-я]*|радиусом)\s*[:=]?\s*(-?\d+(?:[.,]\d+)?)",
                             re.IGNORECASE)


def _guess_draw_tool_from_prompt(user_prompt, tools):
    """Угадывает инструмент черчения из текста запроса пользователя.

    Резервный сценарий (Fallback): если модель Ollama вернула пустую функцию,
    None или аргументы с сырыми буквенными маркерами ('x', 'y'), маршрутизатор
    анализирует сам текст запроса с помощью простых условий if. Например, для
    фразы «начерти круг радиусом 80» принудительно выполняется draw_circle(0, 0, 80).

    Аргументы:
        user_prompt: исходный текст запроса пользователя.
        tools: реестр доступных инструментов {имя_функции: функция}.

    Возвращает:
        tuple (name, args) — имя угаданного инструмента и его словарь аргументов,
        либо (None, None), если надёжно определить команду черчения не удалось.
    """
    if not isinstance(user_prompt, str) or not user_prompt.strip():
        return None, None
    text = user_prompt.lower()

    # Извлекаем радиус, если он упомянут («радиус», «радиусом»).
    radius = None
    m = _DRAW_RADIUS_RE.search(user_prompt)
    if m:
        radius = _to_float(m.group(1))

    # ОПИСАТЬ объект окружностью СНАРУЖИ («опиши квадрат кругом»). Триггер
    # проверяется ДО обычного круга, чтобы не перехватить этот сценарий в draw_circle.
    if ("опиш" in text or "описа" in text) and \
            ("круг" in text or "окружн" in text) and "fit_circle_to_object" in tools:
        # Берём Handle последнего построенного объекта из Словаря Анафоры.
        handle = (CURRENT_CONTEXT_STATE.get("last_object_handle")
                  or ONTOLOGY_STATE.latest_handle())
        if handle:
            return "fit_circle_to_object", {"object_handle": str(handle), "mode": "described"}
        return None, None

    # ВПИСАТЬ объект ВНУТРЬ окружности («впиши круг в квадрат»).
    if ("впиш" in text or "вписать" in text) and \
            ("круг" in text or "окружн" in text) and "fit_circle_to_object" in tools:
        # Берём Handle последнего построенного объекта из Словаря Анафоры.
        handle = (CURRENT_CONTEXT_STATE.get("last_object_handle")
                  or ONTOLOGY_STATE.latest_handle())
        if handle:
            return "fit_circle_to_object", {"object_handle": str(handle), "mode": "inscribed"}
        return None, None

    # Круг / окружность — самый надёжный сценарий: центр (0,0) и радиус.
    # Например, «начерти круг радиусом 80» -> draw_circle(0.0, 0.0, 80.0).
    if ("круг" in text or "окружность" in text) and "draw_circle" in tools:
        # Если радиус не указан явно — берём безопасный дефолт 10.0.
        r = radius if radius is not None else 10.0
        return "draw_circle", {"x": 0.0, "y": 0.0, "radius": r}

    # Отрезок / линия — если в тексте указаны четыре числа (x1,y1,x2,y2),
    # строим отрезок по ним. Иначе вслепую не рисуем.
    if ("лини" in text or "отрезок" in text) and "draw_line" in tools:
        nums = _extract_numbers(user_prompt)
        if len(nums) >= 4:
            return "draw_line", {
                "x1": nums[0], "y1": nums[1],
                "x2": nums[2], "y2": nums[3],
            }
        return None, None

    # Прямоугольник / полилиния требуют координаты вершин; без них вслепую
    # не строим, чтобы не испортить чертёж.
    return None, None


def _looks_like_placeholder(value) -> bool:
    """Определяет, является ли значение строковой заглушкой Handle.

    Защита от галлюцинаций LLM: если модель вернула вместо реального дескриптора
    фразу вроде '<selected-object-handle>', 'selected', 'объект' или пустую строку,
    такой аргумент КАТЕГОРИЧЕСКИ нельзя передавать в HandleToObject — его нужно
    заменить живым Handle из выделения пользователя или из стека онтологии.

    Аргументы:
        value: значение аргумента object_handle от модели.

    Возвращает:
        bool — True, если значение является подозрительной заглушкой.
    """
    if value is None:
        return True
    text = str(value).strip().lower()
    if not text:
        return True
    # Строковые маркеры вида '<...>' — явная заглушка, а не настоящий Handle.
    if "<" in text and ">" in text:
        return True
    for marker in ("selected", "select", "объект", "обьект", "выделен",
                   "выбран", "фигур", "этот", "этих", "дескриптор",
                   "placeholder", "заглушк"):
        if marker in text:
            return True
    return False


def _resolve_live_selection_handles():
    """Возвращает живые Handle объектов, выделенных пользователем мышью.

    БЕЗ участия нейросети вызывает встроенную функцию Аналитика
    get_selected_or_all_objects() (коллекция PickfirstSelectionSet). Если
    пользователь выделил объекты на экране AutoCAD мышью — возвращаются их
    РЕАЛЬНЫЕ Handle; если выбора нет — Handle всех объектов Пространства Модели.

    Возвращает:
        list[str] — список Handle либо пустой список при ошибке подключения.
    """
    try:
        from agent_analyst import get_selected_or_all_objects
        sel = get_selected_or_all_objects()
        if isinstance(sel, dict) and sel.get("status") == "success":
            data = sel.get("data") or []
            return [str(h) for h in data]
    except Exception:
        pass
    return []


def _try_handle_intersections(user_prompt):
    """Обрабатывает команду «круги в точках пересечения объектов» напрямую.

    КРИТИЧНО: функция НЕ парсит пиксельные координаты экрана со скриншота
    «Ножниц» (проблема точности на мониторах 2К/4К). Вместо этого она берёт
    РЕАЛЬНЫЕ Handle объектов (сначала — выделенных пользователем мышью через
    get_selected_or_all_objects, затем — из стека онтологии), вызывает метод
    Аналитика get_intersection_points (IntersectWith) и ставит круги-маркеры
    в истинных CAD-координатах чертежа.

    Аргументы:
        user_prompt: исходный текст запроса пользователя.

    Возвращает:
        dict | None — словарь результата, если запрос относится к пересечениям,
        иначе None (маршрутизатор продолжает обычный поток обработки).
    """
    if not isinstance(user_prompt, str) or not user_prompt.strip():
        return None
    text = user_prompt.lower()
    # Триггеры: «точки пересечения», «пересечения этих объектов» и т.п.
    is_intersection = (
        ("точк" in text and "пересечен" in text)
        or ("пересечен" in text and ("объект" in text or "круг" in text))
    )
    if not is_intersection:
        return None

    # АВТО-ЗАХВАТ ВЫДЕЛЕНИЯ. Сначала берём РЕАЛЬНЫЕ Handle объектов, которые
    # пользователь выделил на экране AutoCAD мышью (PickfirstSelectionSet).
    # Это исключает подстановку строковых заглушек вроде '<selected-object-handle>'
    # и галлюцинации LLM. Если выбора нет — падаем на стек онтологии.
    handles = _resolve_live_selection_handles()
    if len(handles) < 2:
        handles = ONTOLOGY_STATE.last_handles(2)
    if len(handles) < 2:
        return {
            "status": "info", "data": "",
            "message": ("Для поиска точек пересечения нужно как минимум два объекта. "
                        "Выделите их мышью на чертеже или создайте и повторите запрос."),
        }

    from agent_analyst import get_intersection_points
    from agent_draftsman import draw_circle

    # Получаем настоящие CAD-координаты пересечений через IntersectWith.
    result = get_intersection_points(handles[0], handles[1])
    if result.get("status") != "success":
        return result
    points = result.get("data") or []
    if not points:
        return {"status": "info", "data": "",
                "message": "Точки пересечения между объектами не найдены."}

    # Строим круг-маркер в каждой истинной CAD-координате пересечения.
    placed = []
    for (x, y) in points:
        res = draw_circle(float(x), float(y), 5.0)
        if res.get("status") == "success":
            placed.append(res.get("message"))
    update_screen()

    # ЧИСТЫЙ текстовый ответ для пользователя. data оставляем пустой строкой,
    # чтобы в чат НЕ попадал сырой отладочный дамп массивов Python вида
    # [[70.0, 39.2], ...]. Пользователь видит только красивый список точек.
    lines = [f"Поставлено кругов в точках пересечения: {len(points)}."]
    for idx, (px, py) in enumerate(points, start=1):
        lines.append(f"• Точка {idx}: ({float(px):.1f}, {float(py):.1f})")
    return {
        "status": "success",
        "data": "",
        "message": "\n".join(lines),
    }


def _execute_function_call(function_call, user_prompt=None):
    """Безопасно выполняет выбранную функцию через реестр TOOLS.

    По имени функции находит реальную Python-функцию в реестре TOOLS и вызывает
    её с переданными аргументами. Никакого exec()/eval() — только статичный
    диспетчер по зарегистрированным именам. Если передан user_prompt, перед
    вызовом недостающие координаты и радиус достраиваются жёстким парсером.

    Аргументы:
        function_call: словарь Function Calling от модели.
        user_prompt: исходный текст запроса пользователя (для жёсткого парсера).

    Возвращает:
        dict — результат выполнения функции либо описание ошибки.
    """
    name, args = _extract_function_call(function_call)
    tools = _get_tools()

    # Fallback: модель вернула пустую функцию (None) или мусорные аргументы —
    # угадываем команду черчения прямо из текста запроса пользователя (условия if).
    # Например, «начерти круг радиусом 80» -> draw_circle(0.0, 0.0, 80.0).
    if (not name or name not in tools) and user_prompt:
        guessed_name, guessed_args = _guess_draw_tool_from_prompt(user_prompt, tools)
        if guessed_name is not None:
            name, args = guessed_name, guessed_args

    # Жёсткий парсер чисел: заполняем пустые координаты/радиус из текста запроса,
    # чтобы параметры никогда не улетали в агент как None.
    if user_prompt:
        args = _fill_geometry_args(name, args, user_prompt)
    # Жёсткая очистка от строковых маркеров координат ('x', 'y', ''): заменяем их
    # на безопасные числовые дефолты, чтобы исключить "could not convert string
    # to float" в математических методах и в AutoCAD ActiveX.
    args = _sanitize_geometry_args(name, args)

    # Если функция не найдена в реестре — возвращаем понятную ошибку.
    if name not in tools:
        return {"status": "error", "message": f"Неизвестная функция '{name}' для вызова."}

    # Интеллектуальное разрешение множественных анафор на основе онтологии.
    if user_prompt:
        args, block_message = _resolve_anaphora_in_args(name, args, user_prompt)
        # Сценарий Уточнения: контекст двусмысленен — запрещаем выполнение и
        # возвращаем вежливый уточняющий запрос пользователю.
        if block_message is not None:
            return {"status": "clarification", "message": block_message}

    # АВТО-ЗАХВАТ ВЫДЕЛЕНИЯ (самый важный шаг). Если функция работает с Handle
    # объекта, а модель вернула строковую заглушку ('<selected-object-handle>',
    # 'selected', пусто) вместо реального дескриптора, подставляем ЖИВОЙ Handle:
    # сначала из выделенных пользователем объектов (PickfirstSelectionSet), затем
    # из стека онтологии. Это полностью исключает галлюцинации LLM и гарантирует,
    # что в HandleToObject / IntersectWith уходит настоящий дескриптор, а не строка.
    if name in _HANDLE_FUNCTIONS_NEEDING_REAL and _looks_like_placeholder(
            args.get("object_handle")):
        real_handles = _resolve_live_selection_handles()
        real_handle = (real_handles[0] if real_handles else
                       (CURRENT_CONTEXT_STATE.get("last_object_handle")
                        or ONTOLOGY_STATE.latest_handle()))
        if real_handle:
            args["object_handle"] = str(real_handle)

    try:
        target = tools[name]
        # Сценарий Цикла («в каждом», «для всех»): выполняем функцию итерационно
        # для каждого Handle активной группы, если она есть в стеке памяти.
        if user_prompt and mentions_group_anaphora(user_prompt):
            group_handles = ONTOLOGY_STATE.resolve_group(user_prompt)
            if group_handles and name in _HANDLE_TOOL_FUNCTIONS:
                return _execute_cycle(target, name, args, group_handles, user_prompt)
        result = target(**args)
        # Обновляем словарь контекста (State Manager): запоминаем последний
        # чертёж, Handle построенного объекта и имя изменённого слоя.
        _update_context_state(name, args, result)
        # После черчения или манипуляции свойствами принудительно обновляем экран
        # (doc.Update()), чтобы пользователь сразу видел результат в AutoCAD.
        if name in _SCREEN_REFRESH_FUNCTIONS:
            update_screen()
        return result
    except TypeError as e:
        return {"status": "error", "message": f"Неверные аргументы для функции '{name}': {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Ошибка выполнения функции '{name}': {e}"}


# ========================================================================
# ИНТЕГРАЦИЯ С ГРАФИЧЕСКИМ ИНТЕРФЕЙСОМ
# ========================================================================

class AutoCADConnector:
    """Лёгкий адаптер-обёртка над COM-соединением для обратной совместимости.

    Сохранён из предыдущей версии маршрутизатора. Агенты, которым требуется объект
    «acad» с атрибутом .doc, используют этот класс. Новые операторские функции
    подключаются к AutoCAD самостоятельно через core_core, поэтому класс нужен
    преимущественно для диспетчера JSON-паспортов execute_json_command.
    """

    def __init__(self, doc, acad_app):
        """Инициализирует коннектор ссылками на документ и приложение AutoCAD.

        Аргументы:
            doc: активный документ AutoCAD (ActiveDocument).
            acad_app: объект Application AutoCAD.
        """
        self.doc = doc
        self.acad_app = acad_app


class MainRouter:
    """UI-обёртка над маршрутизатором: связывает графический интерфейс с логикой.

    Сохраняет прежний интерфейс (attach_ui_callbacks, process_text_input,
    process_vision_input) и делегирует фактическую обработку запроса в
    маршрутизирующую функцию route_request().
    """

    def __init__(self, ui_app):
        """Сохраняет ссылку на графическую оболочку.

        Аргументы:
            ui_app: экземпляр CadAiAssistantUI.
        """
        self.ui = ui_app

    def process_text_input(self) -> None:
        """Единая точка входа для текстовых команд из UI.

        Извлекает запрос из поля ввода, очищает его, подмешивает текущий контекст
        «Ножниц» (если он зафиксирован в UI) и запускает маршрутизацию route_request
        в отдельном потоке, чтобы окно интерфейса не зависало. Секундомер включается
        для всех типов агентов, а результат возвращается в главный поток через after().
        """
        prompt = self.ui.input_entry.get().strip()
        if not prompt:
            return
        self.ui.input_entry.delete(0, "end")
        self.ui.log(f"\nВы: {prompt}", "user")

        # Пытаемся получить контекст «Ножниц» из UI, если он доступен.
        scissors_context = getattr(self.ui, "scissors_context", None)

        # Получаем выбранного пользователем агента из выпадающего списка UI.
        preferred_agent = getattr(self.ui, "agent_var", None)
        agent_label = preferred_agent.get() if preferred_agent is not None else None

        # Перед отправкой запроса в главном потоке GUI принудительно опрашиваем
        # «пульс» COM-сессии и синхронизируем лампу статуса с реальным состоянием
        # связи (core_core.ensure_connection). Это исключает «залипание» зелёной
        # лампы при обрыве сессии (-2147220995 'Объект не подключен к серверу').
        try:
            self.ui.refresh_connection_indicator()
        except Exception:
            pass

        # Запускаем секундомер в главном потоке для любого выбранного агента.
        self.ui.start_live_timer()

        def _worker():
            # Инициализируем COM-апартамент потока для корректной работы с AutoCAD.
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pass
            try:
                # Передаём колбэк обновления статуса процесса, чтобы UI показывал
                # динамическую индикацию («Размышляю...», «Строю геометрию...»).
                result = route_request(
                    prompt, scissors_context=scissors_context,
                    preferred_agent=agent_label,
                    status_callback=getattr(self.ui, "set_process_status", None))
            except Exception as exc:
                result = {"status": "error",
                          "message": f"Ошибка обработки запроса: {exc}"}
            finally:
                # Возвращаемся в главный поток UI для безопасного вывода результата.
                self.ui.root.after(0, lambda res=result: self._finish_text_result(res))

        threading.Thread(target=_worker, daemon=True).start()

    @staticmethod
    def _format_result_data(data):
        """Приводит результат агента к читаемому строковому виду.

        Функции агентов возвращают либо простые значения, либо составные словари
        вида {"status": ..., "data": ..., "message": ...}. Из составного словаря
        извлекается русскоязычное сообщение и координаты для вывода в чат.

        Аргументы:
            data: результат, возвращённый функцией агента.

        Возвращает:
            str — текстовая строка для отображения пользователю.
        """
        if isinstance(data, dict):
            message = data.get("message", "")
            inner = data.get("data", "")
            parts = [str(message).strip()] if str(message).strip() else []
            if inner not in ("", None):
                parts.append(str(inner))
            return " ".join(parts)
        return str(data) if data is not None else ""

    def _finish_text_result(self, result) -> None:
        """Финальная отрисовка результата текстового запроса в главном потоке.

        Останавливает секундомер и выводит ответ либо ошибку в чат.

        Аргументы:
            result: словарь-ответ от route_request.
        """
        self.ui.stop_live_timer()
        # Сбрасываем динамическую индикацию процесса после завершения.
        try:
            self.ui.set_process_status(None)
        except Exception:
            pass
        # Выводим системные сообщения маршрутизатора (например, о перенаправлении).
        for sys_msg in (result.get("system_logs") or []):
            self.ui.log(sys_msg, "system")
        if result.get("status") == "success":
            # Программная синхронизация UI: обновляем Canvas-капсулу, чтобы она
            # показывала имя агента, который РЕАЛЬНО обработал запрос.
            real_key = result.get("agent")
            real_label = AGENT_KEY_TO_LABEL.get(real_key) if real_key else None
            if real_label:
                current_label = self.ui.agent_var.get()
                if current_label != real_label:
                    self.ui.sync_agent(real_key)
                    self.ui.log(
                        f"[Система]: Запрос обработан агентом {real_label}.", "system")
            agent_name = result.get("agent_name", "")
            # Компактный заголовок ответа с временем вычисления.
            # Формат: «ИИ-Ассистент (Аналитик) • 14 с» (целые секунды, суффикс «с»).
            self.ui.chat_area.configure(state="normal")
            self.ui.chat_area.insert("end", f"\nИИ-Ассистент ({agent_name})", "ai")
            elapsed_sec = getattr(self.ui, "last_elapsed_sec", None)
            if elapsed_sec:
                # Время выводим приглушённым серым отдельным тегом (не отвлекает).
                self.ui.chat_area.insert("end", f" • {elapsed_sec} с", "timer_muted")
            # Двоеточие + перевод строки: текст ответа начинается строго с новой
            # строки для идеальной читаемости истории чата.
            self.ui.chat_area.insert("end", ":\n", "ai")
            self.ui.chat_area.configure(state="disabled")
            self.ui.chat_area.see("end")
            self.ui.log(self._format_result_data(result.get("data")), "ai")
        else:
            self.ui.log(f"\n[Ошибка]: {result.get('message', 'Неизвестная ошибка')}", "system")

    def process_vision_input(self) -> None:
        """Обработчик режима «Ножницы» (техническое зрение).

        Логика захвата экрана перенесена из прежней версии: прячет окно, даёт
        выделить область, захватывает снимок и отправляет в Ollama Vision.
        """
        import time
        import cad_api
        import cad_vision

        prompt = self.ui.input_entry.get().strip() or "Проанализируй чертеж."
        self.ui.input_entry.delete(0, "end")
        self.ui.log(f"\nВы [Скриншот]: {prompt}", "user")
        self.ui.log("🖱 Режим «Ножницы»: выделите нужную область экрана рамкой мыши...", "system")

        # Прячем главное окно, чтобы оно не попало в кадр.
        self.ui.root.withdraw()
        time.sleep(0.2)

        img_b64 = None
        try:
            bbox = cad_vision.run_snipper_select()
            if bbox:
                img_b64 = cad_vision.grab_region_base64(bbox)
                # Сохраняем координаты выделенной области «Ножниц» как визуальный
                # контекст для route_request (аргумент scissors_context при отправке).
                try:
                    left, top, right, bottom = bbox
                    self.ui.scissors_context = (
                        f"область экрана [{left}, {top}] -> [{right}, {bottom}]"
                    )
                except Exception:
                    self.ui.scissors_context = f"выделенная область экрана: {bbox}"
            else:
                self.ui.log("⚠️ [Снимок]: Выделение области отменено (Esc).", "system")
        finally:
            # Восстанавливаем окно в любом случае.
            try:
                self.ui.root.deiconify()
                self.ui.root.lift()
            except Exception:
                pass

        if not img_b64:
            return

        self.ui.start_live_timer()

        def logged_callback_vision(text, tag="ai"):
            self.ui.log(text, tag)

        def on_vision_done():
            self.ui.stop_live_timer()

        threading = __import__("threading")
        threading.Thread(
            target=cad_api.request_ollama_vision,
            args=(prompt, img_b64, logged_callback_vision, on_vision_done),
            daemon=True,
        ).start()


def attach_ui_callbacks(app_ui, router) -> None:
    """Намертво сшивает внешние сигналы-колбэки интерфейса с обработчиками.

    Аргументы:
        app_ui: экземпляр CadAiAssistantUI (графическая оболочка).
        router: экземпляр MainRouter (обработчик команд).
    """
    app_ui.on_submit = lambda ui: router.process_text_input()
    app_ui.on_vision = lambda ui: router.process_vision_input()


def main() -> None:
    """Точка входа приложения: инициализирует UI, маршрутизатор и связывает их.

    Логика перенесена из прежних версий cad_ai_assistant.py / main_router.py.
    """
    import tkinter as tk
    from cad_ui_core import CadAiAssistantUI

    # 1. Инициализируем корневое окно операционной системы Windows.
    root = tk.Tk()

    # Включаем аппаратную поддержку высокой чёткости шрифтов (DPI Aware).
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    # Включаем нативную тёмную тему заголовка окна Windows 11 / 10.
    try:
        import ctypes
        hwnd = root.winfo_id()
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4)
    except Exception:
        pass

    # 2. Создаём «пустой» экземпляр графической оболочки интерфейса.
    app_ui = CadAiAssistantUI(root)

    # 3. Инициализируем независимый логический маршрутизатор агентов.
    router = MainRouter(app_ui)

    # 4. Намертво сшиваем колбэки интерфейса с обработчиками маршрутизатора.
    attach_ui_callbacks(app_ui, router)

    # 5. Запускаем бесконечный цикл обработки событий приложения.
    root.mainloop()


if __name__ == "__main__":
    main()