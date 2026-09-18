# -*- coding: utf-8 -*-
"""main_router.py — Единая точка входа логики ИИ-суждений (Канон 11.1, 11.2).

Данный модуль — финальный управляющий слой системы. Он принимает текстовый
запрос пользователя из UI, учитывает текущий режим интерфейса и возвращает
ЧИСТЫЙ словарь-решение для одномодельного фонового потока LlamaWorker:
модель всего одна — Qwen3 (Qwen3.8-27B-IQ3-MIX), поднятая через llama-server
на OpenAI-совместимом эндпоинте http://127.0.0.1:8080/v1/chat/completions
(адрес переопределяется переменной окружения LLAMA_SERVER_URL).

Старая многомодельная цепочка (DeepSeek-R1 -> Mistral-Small / Qwen через Ollama)
ПОЛНОСТЬЮ ЛИКВИДИРОВАНА. Модель сама генерирует рассуждения внутри тега
<thinking> на английском языке, а финальные ответы выдаёт на русском языке в
зависимости от активного режима.

Основные обязанности:
    1. Маршрутизация комбинированных запросов (текст + скриншот): картинка НЕ
       блокируется — роутер беспрепятственно пропускает её в фоновый поток
       LlamaWorker, где QPixmap кодируется в Base64 (Vision-контент, Этап 4).
    2. Лингвистический Барьер (Канон 16.5): жёсткая команда, зашитая в начало
       КАЖДОГО системного контекста трёх режимов — мысли строго на английском,
       ответ строго на русском языке.
    3. Подбор системного контекста по активному режиму:
         - ЧАТ (MODE_CHAT):      свободная градостроительная теория;
         - ПОМОЩНИК (MODE_ASSISTANT): боевое черчение по 4-этапному шаблону
           (Анализ -> JSON-команды кубиков -> Краткий итог);
         - ПЕСОЧНИЦА (MODE_SANDBOX): лаборатория AutoLISP/Python-макросов.
    4. Универсальный исполнитель команд (Этап 5, Шаг 2): ручные if/elif ветки
       обработки кубиков ПОЛНОСТЬЮ ЛИКВИДИРОВАНЫ. Кубики динамически
       вызываются из глобального реестра tool_registry.CAD_TOOL_REGISTRY
       по принципу протокола MCP.

Ключевые принципы:
    - Никакого exec()/eval() — ЕДИНСТВЕННОЕ санкционированное исключение: легальный
      математический кубик tools/math_engine/evaluate_geometry.py (RULE 2), вычисляющий
      формулы ИИ в песочнице с намертво заблокированными builtins. Роутер возвращает
      ЧИСТЫЙ словарь-решение, а механикой исполнения занимается статичный класс
      (см. core_core.LlamaWorker).
    - Все ошибки и сообщения оформляются на русском языке (Канон 16.5).
"""

from __future__ import annotations

import json         # преобразование очищенного JSON-блока в словарь Python
import logging      # скрытый системный лог ошибок парсинга (Пока-ёкэ)
import re           # регулярные выражения для поиска markdown-блока ```json
from typing import Any, Optional   # аннотации Any (хелпер) и Optional[str]

# Пакет атомарных кубиков САПР «пакет пакетов» (Этап 6, Шаг 1). Единственный
# сквозной импорт! При загрузке срабатывает АВТОСКАНЕР из tools/__init__.py:
# он рекурсивно обходит подпапки, принудительно импортирует каждый .py-файл
# кубика, и декораторы @register_cad_tool автоматически наполняют глобальный
# реестр tool_registry.CAD_TOOL_REGISTRY (MCP-протокол, Этап 5, Шаг 2).
import tools

# Центральный реестр атомарных кубиков (MCP-протокол, Этап 5). Универсальный
# исполнитель и генератор манифеста работают ТОЛЬКО через этот реестр —
# ручных конструкций if/elif и захардкоженных списков кубиков больше нет.
import tool_registry


# ---------------------------------------------------------------------------
# Константы режимов интерфейса (дублируются здесь, чтобы модуль оставался
# независимым от UI и мог использоваться без импорта графической оболочки).
# ---------------------------------------------------------------------------

MODE_CHAT: str = "chat"
MODE_ASSISTANT: str = "assistant"
MODE_SANDBOX: str = "sandbox"

# Единственная монолитная модель всей системы (зеркалит константу core_core).
MODEL_QWEN: str = "Qwen3.8-27B-IQ3-MIX"


# ---------------------------------------------------------------------------
# Лингвистический Барьер (Канон 16.5)
# ---------------------------------------------------------------------------

# Жёсткая системная команда, зашиваемая в начало КАЖДОГО системного контекста.
# Двухъязычный барьер модели: рассуждения внутри <thinking> — строго на
# английском языке (максимальная точность логики 27B-модели), а сразу после
# закрывающего тега </thinking> — мгновенное переключение на грамотный
# технический русский язык. Китайский язык запрещён на всём выводе.
LINGUISTIC_BARRIER: str = (
    "ALL REASONING INSIDE THE <thinking> TAG MUST BE WRITTEN STRICTLY IN ENGLISH. "
    "CHINESE IS STRICTLY FORBIDDEN! "
    "USE THE EXACT TAG <thinking>...</thinking>: NEVER RENAME, TRUNCATE OR "
    "TRANSLATE IT (FORMS LIKE <thing>, <think>, <thinkingg> ARE FORBIDDEN). "
    "IF THE SERVER ALREADY EXTRACTS REASONING INTO A SEPARATE FIELD, NEVER "
    "REPEAT ANY TAGS INSIDE THE VISIBLE ANSWER. "
    "ОДНАКО СРАЗУ ПОСЛЕ ЗАКРЫВАЮЩЕГО ТЕГА </thinking> ТЫ ОБЯЗАН ПЕРЕКЛЮЧИТЬСЯ "
    "И ПИСАТЬ ВСЕ ДАННЫЕ ИСКЛЮЧИТЕЛЬНО НА ГРАМОТНОМ РУССКОМ ЯЗЫКЕ!\n\n"
)


def build_system_prompt(base_prompt: str) -> str:
    """Зашивает Лингвистический Барьер в начало системного контекста режима.

    Аргументы:
        base_prompt: режимный системный промпт модели (Чат/Помощник/Песочница).

    Возвращает:
        str — готовый системный контекст с Барьером во главе.
    """
    return LINGUISTIC_BARRIER + (base_prompt or "").strip()


# ---------------------------------------------------------------------------
# Системные контексты трёх режимов (одномодельная архитектура Qwen3)
# ---------------------------------------------------------------------------

# --- Режим «ЧАТ» (Вкладка Чат): свободная градостроительная теория ----------
# Модель ведёт развёрнутое техническое общение и вежливо напоминает сменить
# вкладку при попытке чертить (черчение — прерогатива Помощника).
QWEN_CHAT_SYSTEM_PROMPT: str = (
    "Ты — полезный, вежливый Помощник архитектора-градостроителя и ассистент по AutoCAD в режиме свободного диалога.\n"
    "Ты ведешь развернутое, технически грамотное общение на любые профессиональные темы. "
    "Твой финальный ответ после </thinking> должен быть написан строго на русском языке. "
    "Если пользователь просит что-то начертить или изменить файл в этом режиме, вежливо напомни ему "
    "переключить вкладку приложения на 'Помощник' для автоматического выполнения команд."
)


def _compose_cad_system_prompt() -> str:
    """Собирает системный контекст Помощника с АКТУАЛЬНЫМ манифестом кубиков.

    Манифест генерируется на лету из глобального реестра CAD_TOOL_REGISTRY
    (Этап 5, Шаг 2): модель Qwen3.8 узнаёт состав арсенала автоматически.
    Ручное перечисление доступных кубиков в тексте промпта ПОЛНОСТЬЮ
    ЛИКВИДИРОВАНО — любое изменение базы инструментов мгновенно отражается
    в промпте при следующем запуске приложения.

    Возвращает:
        str — полный системный контекст режима «Помощник» с внедрённым
        JSON-подобным блоком манифеста инструментов.
    """
    return (
        "Ты — исполнительный Инженер-Проектировщик и Чертёжник САПР в режиме боевого Помощника.\n"
        "Твоя задача — обрабатывать команды инженера по модификации чертежей AutoCAD, строго следуя "
        "следующему четырехэтапному шаблону вывода после тега </thinking>:\n\n"
        "CAD AVAILABILITY RULE (CRITICAL): Look at the dynamically provided [SYSTEM CONTEXT INFO] marker at the top of this prompt.\n"
        "- If the marker indicates that AutoCAD is CLOSED / DISCONNECTED, and the user asks you to perform a physical action on the drawing (e.g., draw a circle, line, polyline), you MUST intercept this request.\n"
        "- In Step 1 (Анализ), explicitly inform the engineer in polite technical Russian that you understand the task perfectly, but cannot execute it because AutoCAD is currently closed or unavailable.\n"
        "- In Step 2 (Блок исполнения), DO NOT generate any active \"status\": \"execute\" JSON blocks for drawing tools.\n"
        "- In Step 3 (Краткий итог), DO NOT report that the object was created and DO NOT promise that it will be executed automatically. You MUST explicitly state in technical Russian that the action is canceled/suspended, and the engineer MUST relaunch AutoCAD and resend the request manually (например: \"Черчение отменено. Пожалуйста, запустите AutoCAD и отправьте запрос повторно\").\n\n"
        "1. АНАЛИЗ И РАЗБИВКА ЗАДАЧИ:\n"
        "Напиши техническим русским языком, как ты понял задачу и на какие подзадачи её разбиваешь. "
        "КРИТИЧЕСКОЕ ПРАВИЛО: Если в твоем арсенале полностью отсутствуют нужные кубики — ОСТАНОВИ ВЫПОЛНЕНИЕ. "
        "Не выводи блок JSON! Вместо этого лаконично напиши в чат: 'Я не могу выполнить это задание автоматически, "
        "так как необходимые кубики автоматизации еще находятся в сборке. Но вот подробная инструкция, как сделать "
        "это в AutoCAD самостоятельно: [Инструкция]' и заверши ответ.\n\n"
        "ПРАВИЛО КООРДИНАТ (ОБЯЗАТЕЛЬНОЕ, КАНОН 16.5):\n"
        "Любое действие построения, требующее координат, принимает минимум ДВЕ координаты [x, y]; "
        "третья координата z НЕобязательна (по умолчанию 0.0). В JSON передавай точки строго в виде [x, y] или [x, y, z].\n"
        "КРИТИЧЕСКОЕ ПРАВИЛО: Если пользователь просит что-либо построить, а обязательные координаты [x, y] "
        "не указаны или неизвестны — НЕ создавай блок JSON! Останови выполнение, напиши в чат на русском языке, "
        "какие именно координаты требуются для продолжения команды, и вежливо попроси пользователя их указать. "
        "Никогда не выдумывай координаты сам.\n\n"
        "ПРАВИЛО ДИНАМИЧЕСКОГО КОНВЕЙЕРА (DYNAMIC PIPELINE RULE, RULE 2):\n"
        "DYNAMIC PIPELINE RULE: You are fully allowed to chain math calculations and drawing tools in a "
        "single JSON block. If you need to use a coordinate calculated by 'evaluate_geometry' in a "
        "subsequent tool (like 'draw_circle' or 'draw_polyline'), pass it as a string prefixed with a "
        "dollar sign (e.g., \"$calc_x\", \"$calc_y\"). The Python executor will automatically resolve "
        "these references to exact floats before triggering the AutoCAD engine.\n"
        "Русская расшифровка: многошаговые вычисления ПОЛНОСТЬЮ РАЗРЕШЕНЫ в рамках одного JSON-пакета. "
        "Сначала вызови кубик 'evaluate_geometry' из манифеста (формулы СТРОКАМИ в словаре 'expressions', "
        "базовые переменные — в словаре 'variables'; пример: {'expressions': "
        "{'center_x': 'radius * cos(radians(angle))', 'center_y': 'radius * sin(radians(angle))'}, "
        "'variables': {'radius': 500, 'angle': 45}}) — он вернёт точные float-значения в поле 'results'. "
        "Затем в аргументах последующих кубиков ('draw_circle', 'draw_polyline' и др.) подставь ссылку "
        "на результат строкой с префиксом '$': например, '$calc_x' вместо числа. НЕ вычисляй числа "
        "вручную и НЕ угадывай их — только динамическая подстановка через '$'.\n"
        "ЖЁСТКИЙ ЗАПРЕТ (во всех остальных случаях): система принимает и исполняет ТОЛЬКО зарегистрированные "
        "кубики из манифеста через JSON-команды. Генерировать или исполнять собственный Python-код, макросы "
        "AutoLISP или любые иные способы вычислений вне кубиков КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО — не выдумывай "
        "численные результаты, они вычисляются процессором ПК со 100% точностью.\n\n"
        "2. БЛОК ИСПОЛНЕНИЯ КОМАНД (JSON):\n"
        "CRITICAL JSON STRUCTURE RULE: Every JSON package you generate MUST include a root-level key named \"status\".\n"
        "- If the user explicitly commands you to draw, modify, calculate, or execute commands in AutoCAD, you MUST set \"status\": \"execute\" in the root of your JSON.\n"
        "- If the user asks you for examples, explanations, templates, guides, or lists of your capabilities, you MUST set \"status\": \"dry_run\" in the root of the JSON. Failure to do so will accidentally corrupt the engineer's active drawing.\n"
        "GUARANTEE FOR AI: When \"status\": \"dry_run\" is detected, the Python backend completely intercepts the package and 100% blocks any access to the AutoCAD COM port. It is mathematically impossible for a dry_run packet to corrupt or modify the active drawing. DO NOT spend any reasoning tokens or time doubting the safety of your examples — fill the 'commands' array with any demonstrative tool calls you want, knowing the backend guarantees absolute safety.\n"
        "Выведи строго структурированный JSON-пакет вызова кубиков внутри markdown-блока ```json. "
        "JSON должен содержать массив 'commands' И корневой ключ \"status\" (см. правило выше). "
        "Полный арсенал доступных кубиков с их параметрами "
        "приведён в АВТОМАТИЧЕСКОМ МАНИФЕСТЕ ниже — сверяйся с ним ПЕРЕД выводом JSON.\n\n"
        "=== АВТОМАТИЧЕСКИЙ МАНИФЕСТ КУБИКОВ (ГЕНЕРИРУЕТСЯ ИЗ РЕЕСТРА) ===\n"
        + tool_registry.generate_ai_tools_manifest()
        + "\n\n"
        "ОПТИМИЗАЦИЯ КОНТЕКСТА: Если тебе нужно построить несколько окружностей одинакового радиуса, "
        "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО плодить несколько команд 'draw_circle'. Ты обязан объединять их в один вызов, "
        "передавая все координаты списком в параметр 'centers': [[x1, y1], [x2, y2], ...].\n"
        "Пример:\n"
        "```json\n"
        "{\n"
        "  \"status\": \"execute\",\n"
        "  \"commands\": [\n"
        "    { \"action\": \"execute_tool\", \"tool_name\": \"draw_circle\", \"arguments\": { \"centers\": [[0.0, 0.0], [50.0, 0.0]], \"radius\": 10.0 } },\n"
        "    { \"action\": \"execute_tool\", \"tool_name\": \"draw_polyline\", \"arguments\": { \"points\": [[0.0, 0.0], [100.0, 0.0], [100.0, 50.0]] } }\n"
        "  ]\n"
        "}\n"
        "```\n\n"
        "GEOMETRIC SELF-DIAGNOSTIC RULE: When generating arrays of coordinates for 'draw_polyline' or 'draw_line' (in 'multi' mode), look closely at adjacent coordinate points.\n"
        "- If you detect that any consecutive adjacent points have identical coordinates (e.g., [x1, y1] == [x2, y2]), you are fully allowed to proceed and generate the JSON package anyway.\n"
        "- HOWEVER, in your Brief Summary (Краткий Итог) after the closing </thinking> tag, you MUST format a clean Markdown table explicitly informing the engineer that specific adjacent points overlap, providing their vertex numbers and exact coordinates for reference. (Note for polyline only: if the first and last points of a polyline match, consider it a standard closed contour request).\n\n"
        "3. КРАТКИЙ ИТОГ:\n"
        "Сразу под блоком JSON напиши краткий, емкий итог на русском языке о том, что именно было успешно смоделировано."
    )


# --- Режим «ПОМОЩНИК» (Вкладка Помощник): боевое черчение -------------------
# Строка СОБИРАЕТСЯ динамически (Этап 5, Шаг 2): полный арсенал кубиков
# внедряется в промпт автоматически через generate_ai_tools_manifest(),
# который читает актуальный глобальный реестр CAD_TOOL_REGISTRY. Ручной
# хардкод списка доступных кубиков полностью ликвидирован.
QWEN_CAD_SYSTEM_PROMPT: str = _compose_cad_system_prompt()


# --- Режим «ПЕСОЧНИЦА» (Вкладка Песочница): лаборатория автоматизации --------
# Модель пишет сложные, оптимизированные макросы на AutoLISP или скрипты на
# Python, снабжая каждую ключевую строку комментариями на русском языке и
# инструкцией загрузки через команду _APPLOAD.
QWEN_SANDBOX_SYSTEM_PROMPT: str = (
    "Ты — Старший Архитектор-Программист и эксперт по автоматизации САПР в режиме 'Песочница'.\n"
    "Твоя задача — писать сложные, оптимизированные макросы на AutoLISP или скрипты на Python по запросу инженера.\n"
    "После тега </thinking> отвечай строго на русском языке. Сначала кратко опиши алгоритм работы макроса, "
    "затем выведи чистый код макроса внутри markdown-блока ```autolisp или ```python. "
    "Снабжай каждую ключевую строку кода подробными комментариями на русском языке. В конце дай краткую инструкцию, "
    "как загрузить этот макрос в AutoCAD через команду АПЛЗАГ (_APPLOAD)."
)


# ---------------------------------------------------------------------------
# Главный маршрутизатор (Канон 11.1)
# ---------------------------------------------------------------------------

def route_request(
    prompt: str,
    mode: str = MODE_ASSISTANT,
) -> dict:
    """Принимает запрос из UI и возвращает чистое решение-словарь для исполнения.

    Это ЕДИНАЯ точка входа всей логики ИИ-суждений. Функция ничего не исполняет
    сама — она лишь возвращает маршрут, который затем обработает движок
    стриминга LlamaWorker (см. core_core).

    Аргументы:
        prompt: текст запроса пользователя.
        mode: текущий режим интерфейса ('chat' | 'assistant' | 'sandbox').

    Возвращает:
        dict — решение вида:
            {"action": "stream", "model": str, "system_prompt": str,
             "user_prompt": str, "mode": str, "title": str} — маршрут для
                потоковой генерации монолитной моделью Qwen3 (все 3 режима).
    """
    prompt = (prompt or "").strip()

    # 1. Одномодельная маршрутизация ВО ВСЕХ РЕЖИМАХ (Канон 16.5). Единая точка
    #    входа: ЛЮБОЙ запрос из бокса чата (Чат / Помощник / Песочница) уходит
    #    на одну монолитную модель Qwen3 через llama-server. Различие — только
    #    в системном контексте, который подставляется динамически в зависимости
    #    от активной вкладки (системный промпт для Qwen3 подбирается по режиму).
    _mode_system_prompts: dict = {
        MODE_CHAT: QWEN_CHAT_SYSTEM_PROMPT,
        MODE_ASSISTANT: QWEN_CAD_SYSTEM_PROMPT,
        MODE_SANDBOX: QWEN_SANDBOX_SYSTEM_PROMPT,
    }
    _mode_titles: dict = {
        MODE_CHAT: "Чат",
        MODE_ASSISTANT: "Помощник",
        MODE_SANDBOX: "Песочница",
    }
    base_prompt = _mode_system_prompts.get(mode, QWEN_CHAT_SYSTEM_PROMPT)

    # Динамическая метка статуса подключения САПР — СТРОГО для вкладки
    # «Помощник»: модель сама решает, исполнять ли боевые команды черчения.
    # Чат и Песочница метку не получают (черчение — прерогатива Помощника).
    if mode == MODE_ASSISTANT:
        base_prompt = _compose_cad_availability_marker() + base_prompt

    return {
        "action": "stream",
        "mode": mode,
        "title": (
            f"{_mode_titles.get(mode, mode)} → Qwen3 ({MODEL_QWEN}) "
            "через llama-server"
        ),
        "model": MODEL_QWEN,
        # Лингвистический Барьер зашивается в начало системного контекста
        # принудительно, ДО того как промпт уйдёт в llama-server.
        "system_prompt": build_system_prompt(base_prompt),
        "user_prompt": prompt,
    }


# ---------------------------------------------------------------------------
# Исполнительный контур (Этап 3, Шаг 3 -> Этап 5, Шаг 2): распарсивание JSON и
# физический вызов кубиков из глобального реестра CAD_TOOL_REGISTRY (MCP).
# Срабатывает ПОСЛЕ завершения стриминга ответа модели (сигнал
# generation_finished в LlamaWorker -> обработчик UI).
# ---------------------------------------------------------------------------

# Скрытый системный лог: ошибки парсинга битого JSON из-за квантовых сбоев
# модели пишутся СЮДА, а не в интерфейс — приложение продолжает жить (Пока-ёкэ).
_ROUTER_LOGGER = logging.getLogger("assistant_ai.main_router")

# Регулярное выражение markdown-блока ```json ... ``` (IGNORECASE + DOTALL —
# допускает разнобой в регистре тега и многострочное содержимое блока).
_JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)```", re.IGNORECASE | re.DOTALL)

# ---------------------------------------------------------------------------
# Предохранитель индикатора подключения CAD (Пока-ёкэ фронтенда).
# Флаг выставляется графическим интерфейсом (слот _on_cad_status) через
# set_cad_connection_state: True — САПР доступна (зелёный светодиод),
# False — AutoCAD закрыт (красный «Нет подключения»). None — состояние
# неизвестно (роутер используется автономно, без UI) — предохранитель спит.
# ---------------------------------------------------------------------------
_cad_connection_state: Optional[bool] = None

# Маркер результата блокировки: интерфейс распознаёт его и показывает
# пользователю явную ошибку в ленте чата вместо ложного отчёта об успехе.
CAD_FUSE_MARKER: str = "@cad_fuse_blocked"


def set_cad_connection_state(connected: bool) -> None:
    """Синхронизирует предохранитель роутера с индикатором подключения GUI.

    Вызывается из слота _on_cad_status (cad_ui_core) при каждом тике
    CadStatusPoller: состояние светодиода «Нет подключения» превращается
    в аппаратный предохранитель — при закрытом AutoCAD боевые JSON-пакеты
    блокируются ещё ДО вызова любого кубика pyautocad (анти-фантомное
    черчение, Пока-ёкэ фронтенда).

    Аргументы:
        connected: True, если AutoCAD доступен (зелёный индикатор),
            False — САПР закрыта или связь потеряна (красный индикатор).
    """
    global _cad_connection_state
    _cad_connection_state = bool(connected)


def _compose_cad_availability_marker() -> str:
    """Собирает динамическую метку статуса подключения AutoCAD для Помощника.

    Метка вставляется в САМОЕ НАЧАЛО системного контекста Помощника перед
    каждой отправкой истории в LlamaWorker: модель Qwen3.8 видит актуальное
    состояние САПР (флаг предохранителя set_cad_connection_state) и САМА
    принимает решение — исполнять боевые команды или вежливо отложить
    черчение до запуска AutoCAD. Чат и Песочница метку НЕ получают.

    Возвращает:
        str — текстовую метку системного статуса САПР с завершающим переводом
        строки (неизвестное состояние трактуется как подключённое — фьюз спит).
    """
    if _cad_connection_state is False:
        return (
            "[SYSTEM CONTEXT INFO: AutoCAD is currently CLOSED / DISCONNECTED. "
            "Drawing commands are unavailable.]\n\n"
        )
    return "[SYSTEM CONTEXT INFO: AutoCAD is currently ACTIVE and CONNECTED]\n\n"

# Реестр атомарных кубиков (Этап 5, Шаг 2): ручной словарь _TOOL_REGISTRY
# ПОЛНОСТЬЮ ЛИКВИДИРОВАН. Кубики сами регистрируются в глобальном реестре
# tool_registry.CAD_TOOL_REGISTRY через декоратор @register_cad_tool(name)
# во время импорта пакета tools (автосканер, см. tools/__init__.py). Роутер
# больше не хранит ни одной ссылки на конкретный кубик — только универсальный
# динамический вызов функции из реестра (принцип MCP, Канон 8.1/8.2).


def extract_json_block(text: str) -> Optional[str]:
    """Извлекает чистый JSON из markdown-оболочки ```json ... ``` ответа модели.

    Модель Qwen3 в режиме «Помощник» выводит JSON-пакет команд строго внутри
    markdown-блока ```json. Эта функция снимает оболочку и возвращает только
    полезную нагрузку для последующего json.loads().

    Аргументы:
        text: финальный текстовый ответ модели (после закрытия </thinking>).

    Возвращает:
        str | None — очищенная JSON-строка, либо None, если блок не найден
        (модель не прислала команд — парсить нечего).
    """
    if not text:
        return None
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        return None
    # Срезаем markdown-оболочку и обрезаем лишние пробелы/переводы строк.
    return match.group(1).strip()


def _resolve_session_references(value, session_vars: dict) -> Any:
    """Рекурсивно подставляет '$'-ссылки на переменные сессии (Пока-ёкэ).

    Сквозной конвейер (DYNAMIC PIPELINE, RULE 2): результаты математического
    кубика evaluate_geometry накапливаются в session_vars, а аргументы
    последующих кубиков того же JSON-пакета могут ссылаться на них строкой
    вида "$<имя>". Функция рекурсивно обходит произвольную структуру аргументов
    (словари, списки, кортежи, примитивы) и заменяет найденные ссылки на точные
    float-значения из кэша ДО передачи в кубик.

    Аргументы:
        value: произвольное значение из словаря arguments (строка, число,
            список, словарь или их вложенная комбинация).
        session_vars: кэш переменных текущего JSON-пакета (имя -> float).

    Возвращает:
        Значение той же структуры, но с разрешёнными '$'-ссылками. Неизвестные
        ссылки остаются строками как есть — ошибку корректно вернёт сам кубик
        (принцип Пока-ёкэ: исполнитель не угадывает значения за модель).
    """
    if isinstance(value, str):
        # Ссылка вида "$calc_x": имя — весь хвост строки после префикса "$".
        if value.startswith("$") and value[1:] in session_vars:
            return float(session_vars[value[1:]])
        return value
    if isinstance(value, dict):
        # Рекурсивный обход подсловарей (например, expressions/variables).
        return {
            key: _resolve_session_references(item, session_vars)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        # Рекурсивный обход списков координат и вложенных коллекций.
        return [_resolve_session_references(item, session_vars) for item in value]
    return value


def execute_tool_commands(text: str) -> list:
    """Парсит JSON-команды модели и ФИЗИЧЕСКИ вызывает кубики из реестра.

    Активируется триггером перехвата JSON в момент, когда стриминг ответа
    от llama-server полностью завершён (сигнал generation_finished). Конвейер:
        1. Извлечь блок ```json ... ``` регулярным выражением.
        2. Преобразовать его в словарь Python через json.loads().
        2.5. Проверить корневой ключ "status": пакеты dry_run/demo/example
           НАМЕРТВО блокируются и не вызывают ни один кубик (Пока-ёкэ
           ложного запуска демонстрационных примеров в активном чертеже).
        2.6. Проверить предохранитель индикатора подключения: при закрытом
           AutoCAD (флаг False) боевой пакет блокируется ещё ДО кубиков,
           чтобы исключить фантомное черчение и ложные отчёты об успехе.
        3. Пройти по массиву commands и поочерёдно вызвать кубики через
           глобальный реестр CAD_TOOL_REGISTRY (MCP-протокол, Этап 5 Шаг 2).

    Универсальный исполнитель: ручные if/elif ветки ликвидированы. Кубик
    ищется по имени в реестре, аргументы JSON распаковываются прямо в
    параметры запечатанной функции (**arguments). Принцип Пока-ёкэ: ЛЮБАЯ
    ошибка (битый JSON, неизвестный кубик, несоответствие аргументов, сбой
    COM) перехватывается и пишется в скрытый системный лог — интерфейс
    приложения никогда не падает из-за выходок модели.

    Аргументы:
        text: финальный ответ модели (видимая часть без <thinking>).

    Возвращает:
        list — результаты выполненных команд в виде словарей вида
        {"tool_name": str, "result": str} (result — JSON-строка кубика);
        пустой список, если JSON-блок отсутствует или повреждён.
    """
    results: list = []

    # --- Этап 1: поиск и очистка JSON-блока (триггер перехвата) ---
    block = extract_json_block(text)
    if not block:
        # Команд нет — модель просто ответила текстом (Чат/Песочница).
        return results

    # --- Этап 2: преобразование очищенного JSON в словарь Python ---
    try:
        data = json.loads(block)
    except (ValueError, TypeError) as exc:
        # Битый JSON (квантовый сбой модели) — только в скрытый лог.
        _ROUTER_LOGGER.error("Битый JSON-блок от модели: %s", exc)
        return results

    # --- Этап 2.5: проверка корневого статуса исполнения (Пока-ёкэ ложного запуска) ---
    # Корневой ключ "status" управляет судьбой ВСЕГО JSON-пакета. Если модель
    # забыла указать его — пакет по умолчанию считается БОЕВЫМ ("execute").
    execution_status = data.get("status", "execute")

    if execution_status in ["dry_run", "demo", "example"]:
        # Намертво блокируем цикл вызова кубиков pyautocad: демонстрационные
        # блоки (объяснения, шаблоны, примеры кода) НЕ должны трогать чертёж.
        print(
            "[Пока-ёкэ] Обнаружен демонстрационный блок JSON. "
            "Выполнение в AutoCAD заблокировано."
        )
        return results  # Прерываем выполнение текущего пакета команд

    # --- Этап 2.6: предохранитель индикатора подключения (Пока-ёкэ фронтенда) ---
    # Даже боевой пакет "execute" НЕ исполняется, если флаг подключения GUI
    # сигнализирует о закрытом AutoCAD (красный светодиод «Нет подключения»).
    # Флаг выставляется UI через set_cad_connection_state; None (автономный
    # запуск роутера без интерфейса) предохранитель не активирует.
    if _cad_connection_state is False:
        print(
            "[Пока-ёкэ] Выполнение заблокировано: Индикатор сигнализирует "
            "об отсутствии подключения к AutoCAD. Пожалуйста, запустите САПР."
        )
        # Помечаем блокировку маркером для интерфейса: он покажет явную
        # ошибку в ленте чата, чтобы пользователь не получил ложный успех.
        return [
            {
                "tool_name": CAD_FUSE_MARKER,
                "result": json.dumps(
                    {
                        "status": "error",
                        "message": (
                            "Выполнение заблокировано: Индикатор сигнализирует "
                            "об отсутствии подключения к AutoCAD. "
                            "Пожалуйста, запустите САПР."
                        ),
                    },
                    ensure_ascii=False,
                ),
            }
        ]

    # --- Этап 3: конвейер динамического исполнения команд (MCP) ---
    commands = data.get("commands") or []
    # Сквозной кэш переменных текущего JSON-пакета (DYNAMIC PIPELINE, RULE 2):
    # результаты кубика evaluate_geometry накапливаются здесь и подставляются
    # в аргументы последующих кубиков через префикс "$" на лету.
    session_vars: dict = {}
    for cmd in commands:
        if not isinstance(cmd, dict):
            continue  # неожиданный тип элемента — пропускаем
        if cmd.get("action") != "execute_tool":
            continue  # пока поддерживается только этот тип действия
        tool_name = cmd.get("tool_name")
        if tool_name not in tool_registry.CAD_TOOL_REGISTRY:
            # Кубик не зарегистрирован — честный отказ в лог (модель обязана
            # была проверить манифест ДО вывода JSON, см. промпт Помощника).
            _ROUTER_LOGGER.warning(
                "Кубик '%s' не найден в CAD_TOOL_REGISTRY — команда пропущена.",
                tool_name,
            )
            continue

        # Извлекаем ссылку на запечатанную функцию кубика (MCP-контракт).
        tool_function = tool_registry.CAD_TOOL_REGISTRY[tool_name]["function"]
        # Распаковываем аргументы из JSON прямо в параметры функции.
        arguments = cmd.get("arguments") or {}
        # Динамическая подстановка '$'-ссылок на результаты математики из кэша
        # сессии (DYNAMIC PIPELINE, RULE 2) — строго ДО вызова кубика (Пока-ёкэ).
        arguments = _resolve_session_references(arguments, session_vars)
        try:
            # Боевой автоматический запуск кубика (динамический вызов)!
            result = tool_function(**arguments)
            # Сквозной конвейер: успешный результат evaluate_geometry питает
            # кэш session_vars для последующих команд того же JSON-пакета.
            if tool_name == "evaluate_geometry":
                try:
                    execution_result = json.loads(result)
                    if execution_result.get("status") == "success":
                        session_vars.update(execution_result.get("results", {}))
                except (ValueError, TypeError) as parse_exc:
                    # Кубик всегда возвращает валидный JSON, но страховка
                    # дешевле простыни (Пока-ёкэ): битый ответ — в скрытый лог.
                    _ROUTER_LOGGER.error(
                        "Некорректный JSON ответа evaluate_geometry: %s",
                        parse_exc,
                    )
            # Накопленный технический ответ передаем дальше по контуру.
            results.append({"tool_name": tool_name, "result": result})
        except Exception as exc:
            # Перехват ошибок несоответствия аргументов / сбоя COM (Пока-ёкэ).
            _ROUTER_LOGGER.error(
                "Ошибка вызова кубика '%s': %s", tool_name, exc
            )

    return results


# ---------------------------------------------------------------------------
# Самопроверка (без запуска GUI)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # Включаем UTF-8 в консоли Windows, чтобы символы вроде «→» не падали.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # 1. Во ВСЕХ трёх режимах маршрут — единый "stream" на монолитную Qwen3,
    #    а системный контекст начинается с Лингвистического Барьера.
    for mode in (MODE_ASSISTANT, MODE_CHAT, MODE_SANDBOX):
        decision = route_request("Как работает AddLine?", mode)
        assert decision["action"] == "stream", f"режим {mode}: ожидали stream"
        assert decision["model"] == MODEL_QWEN, "модель должна быть единой Qwen3"
        system_prompt = decision["system_prompt"]
        assert system_prompt.startswith(
            "ALL REASONING INSIDE THE <thinking> TAG"
        ), "Лингвистический Барьер не зашит в начало системного контекста"
        assert "STRICTLY IN ENGLISH" in system_prompt, "нет английского блока Барьера"
        assert (
            "ИСКЛЮЧИТЕЛЬНО НА ГРАМОТНОМ РУССКОМ ЯЗЫКЕ" in system_prompt.upper()
        ), "нет русского блока Барьера"
        assert decision["user_prompt"] == "Как работает AddLine?"
        print(decision["title"])
        print(f"  системный промпт: {len(system_prompt)} символов")

    # 2. Режимные промпты содержат свои ключевые директивы.
    chat_prompt = build_system_prompt(QWEN_CHAT_SYSTEM_PROMPT)
    cad_prompt = build_system_prompt(QWEN_CAD_SYSTEM_PROMPT)
    sandbox_prompt = build_system_prompt(QWEN_SANDBOX_SYSTEM_PROMPT)
    assert "Помощник архитектора-градостроителя" in chat_prompt
    assert "'Помощник' для автоматического выполнения команд" in chat_prompt
    assert "Инженер-Проектировщик" in cad_prompt
    assert "'commands'" in cad_prompt and "draw_circle" in cad_prompt
    # Арсенал кубиков внедряется в промпт АВТОМАТИЧЕСКИ через манифест реестра
    # (Этап 5, Шаг 2): ручной хардкод списка кубиков полностью ликвидирован.
    assert "АВТОМАТИЧЕСКИЙ МАНИФЕСТ КУБИКОВ" in cad_prompt
    assert "CAD AUTOMATION TOOLS MANIFEST" in cad_prompt
    assert "draw_polyline" in cad_prompt
    assert "set_layer_status" in cad_prompt, "кубик set_layer_status должен быть в манифесте"
    assert "evaluate_geometry" in cad_prompt, "кубик evaluate_geometry должен быть в манифесте"
    # Сквозной конвейер (DYNAMIC PIPELINE): многошаговые вычисления разрешены
    # в одном JSON-пакете, '$'-ссылки подставляет исполнитель Python на лету.
    assert "DYNAMIC PIPELINE RULE" in cad_prompt
    assert "$calc_x" in cad_prompt and "$calc_y" in cad_prompt
    # Правило координат обязано быть зашито в промпт Помощника (Канон 16.5).
    assert "ПРАВИЛО КООРДИНАТ" in cad_prompt
    assert "Никогда не выдумывай координаты сам" in cad_prompt
    assert "Старший Архитектор-Программист" in sandbox_prompt
    assert "_APPLOAD" in sandbox_prompt and "```autolisp" in sandbox_prompt
    print("Режимные системные промпты (Чат/Помощник/Песочница): OK")

    # 3. Комбинированный запрос (текст + скриншот) БЕСПРЕПЯТСТВЕННО уходит в
    #    поток стриминга: текстовая заглушка-отказ Этапа 3 ликвидирована (Этап 4).
    combined = route_request("Посмотри скриншот и объясни построение", MODE_ASSISTANT)
    assert combined["action"] == "stream"
    print("Комбинированный запрос (текст + скриншот) проходит в поток: OK")

    # 4. Каждый системный контекст начинается ровно с одного Барьера (нет дублей).
    for base in (QWEN_CHAT_SYSTEM_PROMPT, QWEN_CAD_SYSTEM_PROMPT,
                 QWEN_SANDBOX_SYSTEM_PROMPT):
        wrapped = build_system_prompt(base)
        assert wrapped.count("ALL REASONING INSIDE THE <thinking> TAG") == 1
    print("Все 3 режима полностью интегрированы в одномодельный роутер: OK")

    # 5. Исполнительный контур (Этап 3, Шаг 3 -> Этап 5, Шаг 2): извлечение
    #    JSON-блока и динамический вызов draw_circle через реестр (MCP).
    sample_json = (
        "```json\n"
        "{\n"
        '  "commands": [\n'
        '    { "action": "execute_tool", "tool_name": "draw_circle",'
        ' "arguments": { "centers": [[10.0, 20.0, 0.0]], "radius": -5.0 } }\n'
        "  ]\n"
        "}\n"
        "```"
    )
    block = extract_json_block(sample_json)
    assert block is not None and block.startswith("{"), "JSON-блок не извлечён"
    # Радиус -5 отсекается валидацией кубика ДО обращения к САПР: результат —
    # честный JSON со статусом error (живой AutoCAD для самопроверки не нужен).
    results = execute_tool_commands(sample_json)
    assert len(results) == 1, "команда draw_circle должна быть выполнена"
    assert '"status": "error"' in results[0]["result"], "радиус -5 должен быть отклонён"
    print("Универсальный исполнитель (draw_circle через реестр, Пока-ёкэ): OK")

    # 6. Битый JSON от модели (квантовый сбой) не роняет приложение.
    broken_block = "```json\n{ 'commands': [ битый }\n```"
    assert execute_tool_commands(broken_block) == [], "битый JSON должен молча уйти в лог"

    # 7. Неизвестный кубик честно пропускается (в арсенале его нет).
    unknown_cube = (
        '```json\n{"commands": [{"action": "execute_tool", '
        '"tool_name": "draw_line", "arguments": {}}]}\n```'
    )
    assert execute_tool_commands(unknown_cube) == [], "неизвестный кубик пропускается"

    # 8. Обычный текст без JSON-блока ничего не исполняет.
    assert extract_json_block("Просто ответ модели без команд") is None
    assert execute_tool_commands("Просто ответ модели без команд") == []
    print("Пока-ёкэ исполнительного контура (битый JSON / нет кубика): OK")

    # 9. Правило координат: 2D-центр [x, y] принимается конвейером (z не нужна).
    sample_2d = (
        "```json\n"
        '{"commands": [{"action": "execute_tool", "tool_name": "draw_circle",'
        ' "arguments": {"centers": [[30.0, 40.0]], "radius": -7.0}}]}\n'
        "```"
    )
    results_2d = execute_tool_commands(sample_2d)
    assert len(results_2d) == 1, "2D-центр [x, y] должен пройти проверку координат"
    assert '"status": "error"' in results_2d[0]["result"]
    print("Правило координат (2D-центр [x, y], z по умолчанию 0.0): OK")

    # 10. Пока-ёкэ: команда draw_circle БЕЗ обязательного аргумента centers
    #     вызывает TypeError при распаковке **arguments — универсальный
    #     исполнитель перехватывает ошибку в скрытый лог, кубик не выполнен.
    no_point = (
        '```json\n{"commands": [{"action": "execute_tool", "tool_name": '
        '"draw_circle", "arguments": {"radius": 10.0}}]}\n```'
    )
    assert execute_tool_commands(no_point) == [], "без [x, y] кубик не вызывается"
    print("Правило координат (команда без [x, y] приостановлена): OK")

    # 11. Кубик draw_polyline (Этап 3, Шаг 4): одна вершина не проходит правило
    #     «минимум две точки [x, y]» — теперь валидацию выполняет САМ кубик и
    #     возвращает честный JSON со статусом error ДО обращения к живой САПР.
    polyline_one_point = (
        '```json\n{"commands": [{"action": "execute_tool", "tool_name": '
        '"draw_polyline", "arguments": {"points": [[10.0, 20.0]]}}]}\n```'
    )
    results_one_point = execute_tool_commands(polyline_one_point)
    assert len(results_one_point) == 1, "кубик вызван и вернул результат"
    assert results_one_point[0]["tool_name"] == "draw_polyline"
    assert '"status": "error"' in results_one_point[0]["result"], "одна вершина отклоняется"

    # 12. Пока-ёкэ: команда draw_polyline БЕЗ аргумента points вызывает TypeError
    #     при распаковке **arguments — кубик не вызывается (Пока-ёкэ).
    polyline_no_points = (
        '```json\n{"commands": [{"action": "execute_tool", "tool_name": '
        '"draw_polyline", "arguments": {}}]}\n```'
    )
    assert execute_tool_commands(polyline_no_points) == [], "без points кубик не вызывается"

    # 13. Оба кубика сосуществуют в одном JSON-пакете без конфликтов: draw_circle
    #     (радиус -5) и draw_polyline (одна вершина) оба вызываются динамически
    #     через реестр и возвращают честные error-статусы от собственных
    #     валидаций. Никаких исключений (Канон 8.1, MCP-протокол).
    mixed_pack = (
        '```json\n{"commands": ['
        '{"action": "execute_tool", "tool_name": "draw_circle", '
        '"arguments": {"centers": [[10.0, 20.0]], "radius": -5.0}},'
        '{"action": "execute_tool", "tool_name": "draw_polyline", '
        '"arguments": {"points": [[0.0, 0.0]]}}'
        ']}\n```'
    )
    results_mixed = execute_tool_commands(mixed_pack)
    assert len(results_mixed) == 2, "оба кубика вызваны и вернули результаты"
    assert results_mixed[0]["tool_name"] == "draw_circle"
    assert results_mixed[1]["tool_name"] == "draw_polyline"
    print("Кубики draw_circle/draw_polyline сосуществуют через реестр: OK")

    # 14. Пакетный режим draw_circle (Этап 3, Шаг 5): несколько центров в ОДНОМ
    #     вызове. Радиус -5 отсекается валидацией кубика ДО обращения к САПР —
    #     самопроверка работает без живой САПР.
    batch_circles = (
        '```json\n{"commands": [{"action": "execute_tool", "tool_name": "draw_circle",'
        ' "arguments": {"centers": [[0.0, 0.0], [50.0, 0.0], [100.0, 25.0]], '
        '"radius": -5.0}}]}\n```'
    )
    results_batch = execute_tool_commands(batch_circles)
    assert len(results_batch) == 1, "пакетная команда draw_circle должна быть выполнена"
    assert '"status": "error"' in results_batch[0]["result"], "радиус -5 должен быть отклонён"
    print("Пакетный режим draw_circle (BATCH PROCESSING, Этап 3 Шаг 5): OK")

    # 15. Кубик set_layer_status (Этап 3, Шаг 6): команда доходит до кубика через
    #     реестр. Неизвестный статус отсекается валидацией кубика ДО обращения
    #     к САПР — результат — честный JSON со статусом error (живой AutoCAD
    #     для самопроверки не нужен).
    layer_cmd = (
        '```json\n{"commands": [{"action": "execute_tool", "tool_name": '
        '"set_layer_status", "arguments": {"layer_name": "0", "status": '
        '"teleport"}}]}\n```'
    )
    results_layer = execute_tool_commands(layer_cmd)
    assert len(results_layer) == 1, "команда set_layer_status должна быть выполнена"
    assert results_layer[0]["tool_name"] == "set_layer_status"
    assert '"status": "error"' in results_layer[0]["result"], "неизвестный статус отклоняется"

    # 16. Пока-ёкэ: пустой arguments не роняет конвейер — значения по умолчанию
    #     (слой "0", статус "create") зашиты в СИГНАТУРУ кубика set_layer_status
    #     (Этап 5, Шаг 2), а не в ручную ветку роутера.
    layer_cmd_empty = (
        '```json\n{"commands": [{"action": "execute_tool", "tool_name": '
        '"set_layer_status", "arguments": {}}]}\n```'
    )
    results_layer_empty = execute_tool_commands(layer_cmd_empty)
    assert len(results_layer_empty) == 1, "пустые аргументы подставляются по умолчанию"
    print("Кубик set_layer_status интегрирован через реестр (Этап 3, Шаг 6): OK")

    # 17. Кубик evaluate_geometry (Этап 6, RULE 2): безопасный расчёт параметрических
    #     формул на стороне Python-процессора БЕЗ обращения к живой САПР. Модель
    #     передаёт формулы строками, кубик вычисляет их в песочнице с намертво
    #     заблокированными builtins и возвращает точные float-значения.
    math_success_cmd = (
        '```json\n{"commands": [{"action": "execute_tool", "tool_name": '
        '"evaluate_geometry", "arguments": {"expressions": '
        '{"center_x": "radius * cos(radians(angle))", '
        '"center_y": "radius * sin(radians(angle))"}, '
        '"variables": {"radius": 100.0, "angle": 45.0}}}]}\n```'
    )
    results_math = execute_tool_commands(math_success_cmd)
    assert len(results_math) == 1, "команда evaluate_geometry должна быть выполнена"
    assert results_math[0]["tool_name"] == "evaluate_geometry"
    assert '"status": "success"' in results_math[0]["result"], "расчёт должен пройти успешно"
    # При радиусе 100 и угле 45° обе координаты равны 100/sqrt(2) ≈ 70.71067811865476.
    assert '"center_x": 70.71067811865476' in results_math[0]["result"]
    print("Кубик evaluate_geometry (безопасный расчёт формул ИИ, RULE 2): OK")

    # 18. Пока-ёкэ: деление на ноль в формуле ИИ не роняет конвейер — кубик
    #     перехватывает исключение и возвращает честный JSON со статусом error.
    math_error_cmd = (
        '```json\n{"commands": [{"action": "execute_tool", "tool_name": '
        '"evaluate_geometry", "arguments": {"expressions": {"bad": "1 / 0"}}}]}\n```'
    )
    results_math_err = execute_tool_commands(math_error_cmd)
    assert len(results_math_err) == 1, "команда evaluate_geometry должна быть выполнена"
    assert '"status": "error"' in results_math_err[0]["result"], "деление на ноль отклоняется"
    print("Пока-ёкэ evaluate_geometry (деление на ноль / битая формула): OK")

    # 19. Сквозной конвейер переменных (DYNAMIC PIPELINE, RULE 2): рекурсивная
    #     подстановка '$'-ссылок из кэша сессии в аргументы кубиков. Скалярные
    #     ссылки становятся float; несуществующие ссылки остаются строками —
    #     ошибку корректно вернёт сам кубик (Пока-ёкэ).
    resolved_args = _resolve_session_references(
        {
            "centers": [["$calc_x", "$calc_y"], [10.0, "$unknown_ref"]],
            "radius": "$calc_x",
            "layer": "0",
        },
        {"calc_x": 70.71067811865476, "calc_y": -70.71067811865476},
    )
    assert resolved_args["radius"] == 70.71067811865476, "скалярная '$'-ссылка -> float"
    assert isinstance(resolved_args["radius"], float), "подстановка обязана дать float"
    assert resolved_args["centers"][0] == [70.71067811865476, -70.71067811865476]
    # Неизвестная ссылка не трогается: пусть её отклонит сам кубик (Пока-ёкэ).
    assert resolved_args["centers"][1] == [10.0, "$unknown_ref"]
    # Обычные строки (не ссылки) не изменяются.
    assert resolved_args["layer"] == "0"
    print("Сквозной конвейер переменных (_resolve_session_references): OK")

    # 20. Полный DYNAMIC PIPELINE через исполнительный контур БЕЗ живой САПР:
    #     первый вызов evaluate_geometry кладёт calc_x/calc_y в session_vars,
    #     второй вызов получает их '$'-ссылками в 'variables' и вычисляет
    #     производные формулы (calc_x + calc_y и calc_x * 2) — доказывает, что
    #     результаты математики сквозным образом перетекают между кубиками.
    pipeline_cmd = (
        '```json\n{"commands": ['
        '{"action": "execute_tool", "tool_name": "evaluate_geometry", '
        '"arguments": {"expressions": '
        '{"calc_x": "radius * cos(radians(angle))", '
        '"calc_y": "radius * sin(radians(angle))"}, '
        '"variables": {"radius": 100.0, "angle": 45.0}}},'
        '{"action": "execute_tool", "tool_name": "evaluate_geometry", '
        '"arguments": {"expressions": '
        '{"sum_coords": "calc_x + calc_y", "scaled_x": "calc_x * 2"}, '
        '"variables": {"calc_x": "$calc_x", "calc_y": "$calc_y"}}}'
        ']}\n```'
    )
    results_pipeline = execute_tool_commands(pipeline_cmd)
    assert len(results_pipeline) == 2, "оба кубика конвейера должны выполниться"
    assert results_pipeline[0]["tool_name"] == "evaluate_geometry"
    assert '"status": "success"' in results_pipeline[0]["result"]
    assert results_pipeline[1]["tool_name"] == "evaluate_geometry"
    second_stage = json.loads(results_pipeline[1]["result"])
    assert second_stage["status"] == "success", "вторая ступень обязана получить числа"
    # При R=100 и угле 45° calc_x == calc_y ≈ 70.71067811865476,
    # поэтому sum_coords == scaled_x == 141.42135623730952.
    assert abs(second_stage["results"]["sum_coords"] - 141.42135623730952) < 1e-9
    assert abs(second_stage["results"]["scaled_x"] - 141.42135623730952) < 1e-9
    print("DYNAMIC PIPELINE (evaluate_geometry -> evaluate_geometry через '$'): OK")