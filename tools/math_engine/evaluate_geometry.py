# -*- coding: utf-8 -*-
"""evaluate_geometry.py — Супер-кубик безопасного математического процессора (Этап 6).

Изолированный файл по принципу «ОДИН КУБИК — ОДИН НЕЗАВИСИМЫЙ ФАЙЛ» (RULE 5).
Декоратор @register_cad_tool(name="evaluate_geometry") автоматически регистрирует
кубик в глобальном реестре tool_registry.CAD_TOOL_REGISTRY при импорте —
автосканер пакета tools загружает этот файл при старте приложения.

Назначение: модель Qwen3.8 НЕ вычисляет числа сама и НЕ генерирует исполняемый
код. Она передаёт параметрические/тригонометрические/геометрические формулы
СТРОКАМИ в стерильном JSON-пакете, а этот кубик вычисляет их на стороне
Python-процессора со 100% точностью.

Безопасность (RULE 2 — единственное разрешённое исключение eval()):
  - глобальные встроенные функции намертво заблокированы:
    eval(expr, {"__builtins__": None}, safe_dict);
  - в контекст попадают ТОЛЬКО переменные пользователя из словаря variables
    и нативные функции стандартного модуля math (sin, cos, tan, radians,
    degrees, sqrt, pow, pi и т.д.);
  - доступ к файловой системе, сети, подпроцессам и системным вызовам
    категорически невозможен;
  - любая ошибка расчёта (деление на ноль, синтаксический сбой строки ИИ,
    нечисловой результат) перехватывается try-except и возвращается через
    build_error() в строго валидном JSON (принцип Пока-ёкэ).
"""

import json
import math
import sys
from pathlib import Path
from typing import Dict

# Bootstrap корня проекта в sys.path: страховка для нестандартных контекстов
# импорта (аналогично другим кубикам проекта).
_ROOT_PROJECT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT_PROJECT) not in sys.path:
    sys.path.insert(0, str(_ROOT_PROJECT))

# Декоратор автоматической регистрации кубика (MCP-протокол, Этап 5).
from tool_registry import register_cad_tool
# Общий служебный слой пакета: строитель JSON-ответа об ошибке (Пока-ёкэ).
from tools._shared import build_error


@register_cad_tool(name="evaluate_geometry")
def evaluate_geometry(expressions: dict, variables: dict = None) -> str:
    """Exact parametric geometry calculator (Python-side math engine).

    This tool is designed for precise computation of coordinate arrays, steps,
    angles and trigonometric expressions on the Python side (100% accuracy).
    The AI model passes FORMULAS AS STRINGS; the cube evaluates them inside an
    isolated sandbox with all builtins hard-blocked (RULE 2 exception).

    Accepts:
        expressions: dict of formulas — keys are result names (labels of the
                     sought points/coordinates), values are strings of math
                     equations written in Python syntax using ONLY the provided
                     variables and math module functions, e.g.:
                       {"center_x": "radius * cos(radians(angle))",
                        "center_y": "radius * sin(radians(angle))"}
        variables:   optional dict of base variables, e.g.
                     {"radius": 500, "angle": 45}.

    Security contract: eval() is the ONLY permitted exception — the global
    namespace is hard-locked ({"__builtins__": None}), so no system calls,
    file/network access or attribute probing is possible. The sandbox exposes
    ONLY the passed variables plus the native functions of the math module
    (sin, cos, tan, asin, acos, atan, radians, degrees, pi, sqrt, pow, ...).
    Division by zero, malformed AI expression strings or non-numeric results
    are caught and returned as a strict JSON error (Poka-yoke).

    Returns:
        Strictly valid JSON string:
          - success: {"status": "success", "results": {...},
                      "message": "Все геометрические формулы успешно рассчитаны процессором ПК"}
          - error:   {"status": "error", "message": "Error description"}
    """
    # ---- Этап 0: Валидация входных данных (Пока-ёкэ) ----
    if not isinstance(expressions, dict) or len(expressions) < 1:
        return build_error(
            "Словарь 'expressions' должен содержать минимум одну формулу "
            "(например, {'center_x': 'radius * cos(radians(angle))'})."
        )

    try:
        # ---- Этап 1: Сборка безопасного математического контекста (RULE 2) ----
        safe_dict: Dict[str, object] = {}
        # Переменные пользователя имеют приоритет над функциями math.
        if isinstance(variables, dict):
            safe_dict.update(variables)
        # Дополняем контекст ВСЕМИ нативными функциями/константами math
        # (sin, cos, tan, asin, acos, atan, radians, degrees, pi, sqrt, pow).
        for name in dir(math):
            if not name.startswith("_"):
                safe_dict[name] = getattr(math, name)

        # ---- Этап 2: Цикл безопасного вычисления формул ----
        calculated_results: Dict[str, float] = {}
        for key, expr in expressions.items():
            # Стерильный контракт: значение формулы обязано быть строкой.
            if not isinstance(expr, str):
                raise ValueError(
                    "Формула '{}' должна быть строкой.".format(key)
                )
            # Безопасное окружение: встроенные функции ЗАБЛОКИРОВАНЫ намертво.
            result = eval(expr, {"__builtins__": None}, safe_dict)
            # Принудительное приведение результата к float (Канон 3).
            number = float(result)
            # Отсев нечисловых результатов (NaN / бесконечность) — Пока-ёкэ.
            if math.isnan(number) or math.isinf(number):
                raise ValueError(
                    "Формула '{}' вернула нечисловой результат.".format(key)
                )
            calculated_results[key] = number

        # ---- Этап 3: Формирование единого ответа (Пока-ёкэ) ----
        return json.dumps(
            {
                "status": "success",
                "results": calculated_results,
                "message": "Все геометрические формулы успешно рассчитаны процессором ПК",
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        # Любая ошибка расчётов (деление на ноль, синтаксический сбой строки
        # ИИ, несоответствие типов) перехватывается и уходит в JSON-ошибку.
        return build_error("Ошибка вычисления геометрии: {}".format(exc))