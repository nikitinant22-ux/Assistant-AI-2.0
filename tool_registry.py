# -*- coding: utf-8 -*-
"""tool_registry.py — Центральный реестр атомарных кубиков САПР (Этап 5, Шаг 1).

Данный модуль полностью ликвидирует практику РУЧНОГО добавления инструментов
в роутер (main_router._TOOL_REGISTRY). Вместо ручного словаря каждый кубик
САМ заявляет о себе через декоратор @register_cad_tool(name), работающий по
принципу протокола MCP: декоратор перехватывает функцию, считывает её docstring
(техническое описание параметров) и сохраняет запись в глобальный словарь
CAD_TOOL_REGISTRY.

Модуль является ЧИСТОЙ инфраструктурой и импортирует ТОЛЬКО стандартную
библиотеку Python (inspect, json, typing). Он НЕ импортирует пакет tools или
main_router — это гарантирует отсутствие циклических зависимостей: декораторы
выполняются во время импорта пакета tools (автосканер), поэтому реестр обязан
оставаться полностью автономным.

Основные обязанности:
    1. CAD_TOOL_REGISTRY — глобальный словарь-реестр инструментов:
       ключ — строковое имя кубика ("draw_circle"),
       значение — {"function": func, "description": func.__doc__}.
    2. register_cad_tool(name) — фабрика декораторов автоматической
       регистрации кубиков (MCP-подобный протокол).
    3. generate_ai_tools_manifest() — автоматический генератор монолитного
       системного контекста (JSON-подобная структура на английском языке),
       перечисляющая доступный арсенал кубиков для модели Qwen3.8.

Канон 16.5: машиночитаемые идентификаторы (имена, ключи JSON, значения) —
строго английский ASCII; комментарии и docstrings — исключительно русский язык.
"""

from __future__ import annotations

import inspect      # чтение сигнатуры функций для JSON-описания параметров
import json         # сериализация JSON-подобной структуры манифеста
from typing import Any, Callable, Dict, List


# ---------------------------------------------------------------------------
# Глобальный реестр атомарных кубиков (MCP-подобный протокол, Этап 5, Шаг 1)
# ---------------------------------------------------------------------------

# Ключ — строковое имя кубика ("draw_circle"), значение — словарь:
#     "function":    callable-ссылка на исполнительную функцию;
#     "description": docstring функции — техническое описание для ИИ.
CAD_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Декоратор автоматической регистрации (принцип протокола MCP)
# ---------------------------------------------------------------------------

def register_cad_tool(name: str) -> Callable[[Callable], Callable]:
    """Фабрика декоратора автоматической регистрации атомарного кубика.

    Работает по принципу протокола MCP: разработчик просто помечает функцию
    декоратором @register_cad_tool("имя_кубика"), и кубик сам попадает в
    глобальный реестр CAD_TOOL_REGISTRY — ручное редактирование роутера
    больше не требуется.

    Аргументы:
        name: Строковое имя кубика в реестре (например, "draw_circle").
              Имя обязано быть уникальным — повторная регистрация того же
              имени вызывает ValueError (защита от тихой перезаписи, Пока-ёкэ).

    Возвращает:
        Декоратор, который регистрирует переданную функцию и возвращает её
        БЕЗ обёртки (identity): оригинальная сигнатура и атрибуты сохраняются,
        что критично для прямого вызова кубика роутером (tool_func(**args)).
    """

    def decorator(func: Callable) -> Callable:
        # Защита от дубликатов: одинаковое имя кубика — ошибка конфигурации.
        if name in CAD_TOOL_REGISTRY:
            raise ValueError(
                "Кубик '{}' уже зарегистрирован в CAD_TOOL_REGISTRY. "
                "Имена кубиков обязаны быть уникальными.".format(name)
            )

        # Модель Qwen3.8 получает docstring как описание вызова (MCP-схема).
        CAD_TOOL_REGISTRY[name] = {
            "function": func,
            "description": (func.__doc__ or "").strip(),
        }
        # Возвращаем исходную функцию без обёртки (сохраняем identity).
        return func

    return decorator


# ---------------------------------------------------------------------------
# Автоматический генератор системного контекста (манифест для Qwen3.8)
# ---------------------------------------------------------------------------

def _annotation_to_str(annotation: Any) -> str:
    """Преобразует аннотацию типа Python в компактную строку ASCII.

    Аргументы:
        annotation: объект аннотации из inspect.signature (тип либо
            typing-конструкция; может быть и строкой при ленивых аннотациях).

    Возвращает:
        str — читаемое имя типа, например "List[List[Union[int, float]]]".
    """
    if annotation is inspect.Parameter.empty:
        return "Any"
    if isinstance(annotation, type):
        # Обычный класс (int, float, str): берём только его короткое имя.
        return annotation.__name__
    # typing-конструкции вида typing.List[...]: убираем префикс "typing.".
    return str(annotation).replace("typing.", "")


def _describe_parameters(func: Callable) -> List[Dict[str, Any]]:
    """Извлекает JSON-описание параметров функции из её сигнатуры.

    Аргументы:
        func: зарегистрированная исполнительная функция кубика.

    Возвращает:
        Список словарей вида {"name", "type", "required"} — по одному на
        каждый параметр функции (порядок объявления сохранён). Вариативные
        маркеры *args/**kwargs в описание не включаются.
    """
    parameters: List[Dict[str, Any]] = []
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        # Сигнатура недоступна (например, встроенная функция) — пустой список.
        return parameters

    for param in signature.parameters.values():
        # Позиционные/ключевые маркеры *args, **kwargs для ИИ бесполезны.
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        parameters.append(
            {
                "name": param.name,
                "type": _annotation_to_str(param.annotation),
                "required": param.default is param.empty,
            }
        )
    return parameters


def _build_placeholder_example(parameters: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Строит пример arguments для JSON-команды из типов параметров.

    Подставляет в каждый параметр реалистичный типозависимый плейсхолдер:
    списки координат -> [[0.0, 0.0]], числа -> 0.0, строки -> осмысленное
    значение по имени параметра ("layer" -> "0", "status" -> "create").
    Пример носит ИЛЛЮСТРАТИВНЫЙ характер: реальные значения модель обязана
    брать из запроса пользователя, а не выдумывать (Канон 16.5).

    Аргументы:
        parameters: результат _describe_parameters(func).

    Возвращает:
        dict — словарь вида {"имя_параметра": плейсхолдер} для подстановки
        в поле "arguments" шаблона JSON-команды.
    """
    example: Dict[str, Any] = {}
    for param in parameters:
        p_type: str = param.get("type", "Any")
        p_name: str = param.get("name", "")
        # Порядок проверок важен: тип List содержит подстроки "int"/"float",
        # поэтому списки обрабатываются ПЕРВЫМИ.
        if "List" in p_type:
            # Список координат [x, y] — минимальный допустимый плейсхолдер.
            example[p_name] = [[0.0, 0.0]]
        elif "dict" in p_type.lower():
            # Словарь (формулы/переменные) — иллюстративный мини-пример: реальные
            # ключи и значения модель обязана взять из запроса пользователя.
            example[p_name] = {"example_key": 0.0}
        elif "float" in p_type or "int" in p_type:
            example[p_name] = 0.0
        elif "str" in p_type:
            # Именованные подсказки делают пример осмысленным для модели.
            lowered = p_name.lower()
            if "layer" in lowered:
                example[p_name] = "0"
            elif "status" in lowered:
                example[p_name] = "create"
            else:
                example[p_name] = "text"
        else:
            # Неизвестный тип — нейтральное пустое значение.
            example[p_name] = None
    return example


def generate_ai_tools_manifest() -> str:
    """Генерирует монолитный манифест доступных кубиков для модели Qwen3.8.

    Функция обходит ВЕСЬ глобальный словарь CAD_TOOL_REGISTRY (в алфавитном
    порядке имён для детерминированного вывода) и собирает единый текстовый
    JSON-подобный контекст на английском языке: имя каждого инструмента, его
    техническое описание (docstring), JSON-типы параметров, признак
    обязательности и готовый шаблон JSON-команды вызова. Собранный манифест
    подставляется в системный контекст режима «Помощник» (боевое черчение).

    Возвращает:
        str — монолитная текстовая структура с заголовком протокола вызова
        и JSON-блоком {"protocol", "tools_count", "tools": [...]}.
    """
    tools: List[Dict[str, Any]] = []
    for tool_name in sorted(CAD_TOOL_REGISTRY.keys()):
        entry: Dict[str, Any] = CAD_TOOL_REGISTRY[tool_name]
        func: Callable = entry["function"]
        description: str = entry.get("description") or ""
        parameters: List[Dict[str, Any]] = _describe_parameters(func)
        tools.append(
            {
                "name": tool_name,
                "description": description,
                "parameters": parameters,
                "calling_example": {
                    "action": "execute_tool",
                    "tool_name": tool_name,
                    "arguments": _build_placeholder_example(parameters),
                },
            }
        )

    # Монолитная JSON-подобная структура (структурный текст — английский).
    manifest: Dict[str, Any] = {
        "protocol": (
            "Respond with a JSON command packet inside a markdown ```json block. "
            "Every atomic action MUST be wrapped as "
            '{"action": "execute_tool", "tool_name": "<NAME>", "arguments": {...}}. '
            "Combine all primitive operations of the same kind into ONE call. "
            "Coordinates rule: every point requires at least [x, y]; "
            "z is optional (default 0.0). "
            "NEVER invent coordinates — ask the user when they are missing."
        ),
        "tools_count": len(tools),
        "tools": tools,
    }

    header_lines: List[str] = [
        "=== CAD AUTOMATION TOOLS MANIFEST (AUTO-GENERATED FROM CAD_TOOL_REGISTRY) ===",
        "Below is the complete arsenal of atomic CAD tools available for automatic "
        "execution. Check this manifest BEFORE emitting any JSON command packet.",
        "",
        json.dumps(manifest, ensure_ascii=False, indent=2),
    ]
    return "\n".join(header_lines)


# ---------------------------------------------------------------------------
# Самодиагностика инфраструктуры: запускается только при прямом исполнении
# файла и проверяет работу декоратора + генератора БЕЗ обращения к живой САПР.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Регистрируем ДВА демонстрационных кубика (не настоящие) — исключительно
    # чтобы проверить протокол регистрации и генерацию манифеста на живом коде.

    @register_cad_tool("demo_circle")
    def demo_circle(centers: List[List[Any]], radius: float) -> str:
        """Демонстрационный кубик пакетного построения окружностей.

        Аргументы:
            centers: Список центров [[x, y], ...] (минимум одна точка).
            radius: Общий радиус всех окружностей.

        Возвращает:
            Текстовую JSON-строку статуса выполнения (самодиагностика).
        """
        return "ok"

    @register_cad_tool("demo_layer")
    def demo_layer(layer_name: str, status: str = "create") -> str:
        """Демонстрационный кубик управления слоями AutoCAD.

        Аргументы:
            layer_name: Имя целевого слоя (например, "0").
            status: Операция: "create", "freeze", "thaw", "on", "off".

        Возвращает:
            Текстовую JSON-строку статуса выполнения (самодиагностика).
        """
        return "ok"

    # Печатаем монолитный манифест с демонстрационными кубиками.
    print(generate_ai_tools_manifest())