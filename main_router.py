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
        "keywords": ["нарисуй", "начерти", "опиши", "нарисуйте", "начертите", "построй"],
    },
    "analyst": {
        "name": "Аналитик",
        "model": "deepseek-r1:14b",
        "system_prompt": (
            "Ты — ИИ-аналитик. Твоя задача — инспектировать чертеж, запрашивать "
            "списки слоев, листов и считать объекты. Не пытайся ничего чертить."
        ),
        "keywords": ["какие", "сколько", "проверь", "пересекаются", "посчитай", "статистика"],
    },
    "operator": {
        "name": "Оператор",
        "model": "qwen2.5-coder:14b-instruct-q8_0",
        "system_prompt": (
            "Ты — ИИ-оператор. Ты переключаешь вкладки (Модель/Лист), активируешь "
            "чертежи и меняешь цвета слоев и объектов."
        ),
        "keywords": ["перейди", "включи", "поменяй цвет", "активируй", "переключи", "измени цвет"],
    },
    "reference": {
        "name": "Справочник",
        "model": "mistral-small:22b",
        "system_prompt": (
            "Ты — ИИ-справочник по AutoCAD. Используй встроенную базу знаний "
            "cad_reference.md для ответов на технические вопросы пользователя. "
            "Давай развернутые ответы на русском."
        ),
        "keywords": ["как сделать", "почему", "справка", "что такое", "объясни", "зачем"],
    },
}

# Базовый URL локального API Ollama.
OLLAMA_URL = "http://localhost:11434/api/chat"

# Сообщение об ошибке при недоступной модели Ollama (на русском языке).
OLLAMA_ERROR_MESSAGE = (
    "Локальная модель Ollama не отвечает. Убедитесь, что она запущена."
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

    # Инструменты Чертёжника (построение геометрии).
    from agent_draftsman import (draw_circle, draw_polyline, draw_rectangle,
                                 draw_ellipse, fit_object_in_circle)
    tools.update({
        "draw_circle": draw_circle,
        "draw_polyline": draw_polyline,
        "draw_rectangle": draw_rectangle,
        "draw_ellipse": draw_ellipse,
        "fit_object_in_circle": fit_object_in_circle,
    })

    # Инструменты Аналитика (чтение и инспекция данных).
    from agent_analyst import (get_open_drawings, get_layers_list, get_sheets_list,
                               analyze_objects_summary, get_selected_or_all_objects,
                               check_lines_intersections)
    tools.update({
        "get_open_drawings": get_open_drawings,
        "get_layers_list": get_layers_list,
        "get_sheets_list": get_sheets_list,
        "analyze_objects_summary": analyze_objects_summary,
        "get_selected_or_all_objects": get_selected_or_all_objects,
        "check_lines_intersections": check_lines_intersections,
    })

    # Инструменты Оператора (навигация и управление свойствами).
    from agent_operator import (switch_to_model, switch_to_sheet, switch_to_drawing,
                                change_object_color, change_layer_color)
    tools.update({
        "switch_to_model": switch_to_model,
        "switch_to_sheet": switch_to_sheet,
        "switch_to_drawing": switch_to_drawing,
        "change_object_color": change_object_color,
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


def _detect_intent(user_prompt: str) -> str:
    """Определяет намерение (Intent) по ключевым словам запроса.

    Проходит по конфигурации AGENTS_CONFIG в порядке приоритета и возвращает
    ключ агента, чьи ключевые слова встретились в запросе. Если совпадений нет —
    по умолчанию выбирается Справочник (reference).

    Аргументы:
        user_prompt: текст запроса пользователя.

    Возвращает:
        str — ключ агента ('draftsman', 'analyst', 'operator' или 'reference').
    """
    prompt_lower = user_prompt.lower()
    for agent_key, agent_cfg in AGENTS_CONFIG.items():
        for keyword in agent_cfg["keywords"]:
            if keyword in prompt_lower:
                return agent_key
    # Без явного совпадения возвращаем Справочник (безопасный ответ по умолчанию).
    return "reference"


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


def route_request(user_prompt: str, scissors_context=None, preferred_agent: str = None):
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
        # 1. Интеграция «Ножниц»: подмешиваем визуальную подсказку.
        enriched_prompt = _merge_scissors_context(user_prompt, scissors_context)

        # 2. Определяем намерение и выбираем агента с его моделью.
        #    Явный выбор агента в UI имеет приоритет над автоопределением.
        intent = _resolve_preferred_agent(preferred_agent) or _detect_intent(enriched_prompt)
        agent_cfg = AGENTS_CONFIG[intent]

        # 3. Для Справочника результат — просто сгенерированный моделью текст.
        if intent == "reference":
            answer = _ask_ollama(
                model=agent_cfg["model"],
                system_prompt=agent_cfg["system_prompt"],
                user_message=enriched_prompt,
                tools=None,
            )
            return {
                "status": "success",
                "agent": intent,
                "agent_name": agent_cfg["name"],
                "data": answer,
            }

        # 4. Для остальных агентов запрашиваем Function Calling с описанием функций.
        function_call = _ask_ollama(
            model=agent_cfg["model"],
            system_prompt=agent_cfg["system_prompt"],
            user_message=enriched_prompt,
            tools=_describe_tools(intent),
        )

        # 5. Выполняем выбранную функцию через реестр TOOLS (без exec/eval).
        result = _execute_function_call(function_call)
        return {
            "status": "success",
            "agent": intent,
            "agent_name": agent_cfg["name"],
            "data": result,
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

def _ask_ollama(model: str, system_prompt: str, user_message: str, tools):
    """Отправляет запрос в локальную Ollama и возвращает ответ модели.

    Выполняет POST-запрос на http://localhost:11434/api/chat. При наличии списка
    tools модель получает описание доступных функций и возвращает Function Calling
    (JSON с полем tool_calls) либо текстовый ответ.

    Аргументы:
        model: имя модели Ollama.
        system_prompt: системный промпт агента.
        user_message: сообщение пользователя.
        tools: список описаний функций (или None для чисто текстового ответа).

    Возвращает:
        str | dict — текстовый ответ либо словарь Function Calling.

    Исключения:
        requests.exceptions.ConnectionError — если Ollama недоступна.
    """
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


def _execute_function_call(function_call):
    """Безопасно выполняет выбранную функцию через реестр TOOLS.

    По имени функции находит реальную Python-функцию в реестре TOOLS и вызывает
    её с переданными аргументами. Никакого exec()/eval() — только статичный
    диспетчер по зарегистрированным именам.

    Аргументы:
        function_call: словарь Function Calling от модели.

    Возвращает:
        dict — результат выполнения функции либо описание ошибки.
    """
    name, args = _extract_function_call(function_call)
    tools = _get_tools()

    # Если функция не найдена в реестре — возвращаем понятную ошибку.
    if name not in tools:
        return {"status": "error", "message": f"Неизвестная функция '{name}' для вызова."}

    try:
        target = tools[name]
        result = target(**args)
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
                result = route_request(prompt, scissors_context=scissors_context,
                                       preferred_agent=agent_label)
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
        if result.get("status") == "success":
            agent_name = result.get("agent_name", "")
            self.ui.log(f"\nИИ-Ассистент ({agent_name}):", "ai")
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