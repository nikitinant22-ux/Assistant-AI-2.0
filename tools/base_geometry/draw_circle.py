# -*- coding: utf-8 -*-
"""draw_circle.py — Атомарный кубик №1: пакетное построение окружностей.

Изолированный файл по принципу «ОДИН КУБИК — ОДИН НЕЗАВИСИМЫЙ ФАЙЛ»
(Этап 6, Шаг 2). Декоратор @register_cad_tool(name="draw_circle") автоматически
регистрирует кубик в глобальном реестре tool_registry.CAD_TOOL_REGISTRY при
импорте — автосканер пакета tools загружает этот файл при старте приложения.
Файл является МОДУЛЕМ: он не запускается как скрипт (это исключает двойную
регистрацию); самодиагностика кубиков живёт в main_router.py (__main__).

Архитектурные правила (Канон 3, 8.1, 8.2, 16.5):
  - связь с САПР — исключительно через pyautocad (мост tools._shared);
  - координаты принудительно приводятся к float и упаковываются в APoint;
  - кубик возвращает строго валидную JSON-строку (принцип Пока-ёкэ).
"""

import json
import sys
from pathlib import Path
from typing import List, Union

# Bootstrap корня проекта в sys.path: страховка для нестандартных контекстов
# импорта. В штатном режиме автосканер загружает модуль через importlib —
# там пути уже корректны, и условие ниже не срабатывает.
_ROOT_PROJECT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT_PROJECT) not in sys.path:
    sys.path.insert(0, str(_ROOT_PROJECT))

# Декоратор автоматической регистрации кубика (MCP-протокол, Этап 5).
from tool_registry import register_cad_tool
# Общий служебный слой пакета: мост связи с САПР и строитель JSON-ошибок.
from tools._shared import APoint, build_error, ensure_connection


@register_cad_tool(name="draw_circle")
def draw_circle(centers: List[List[Union[int, float]]], radius: float) -> str:
    """Batch circle builder: draws N circles with a single cube call.

    Accepts a LIST of centers [[x1, y1], [x2, y2], ...] and ONE shared
    radius — all circles are created in a single call (token optimization,
    no JSON duplication). Coordinate rule: every point requires at least
    [x, y]; the third z is optional (default 0.0). Values are force-converted
    to float and packed into the native pyautocad point class (APoint).
    Drawing uses the native COM method AddCircle(Center, Radius).

    Args:
        centers: List of centers [[x1, y1], [x2, y2], ...] (at least one).
        radius: Shared circle radius (positive number, common for all).

    Returns:
        Strictly valid JSON string:
          - success: {"status": "success", "handles": ["ID1", ...],
                      "message": "Circles built: N"}
          - error:   {"status": "error", "message": "Error description"}
    """
    # ---- Этап 0: Валидация входных данных (Пока-ёкэ) ----
    # Пакетный режим: минимум ОДИН центр, количество сверху не ограничено.
    if not isinstance(centers, (list, tuple)) or len(centers) < 1:
        return build_error(
            "Список центров должен содержать минимум одну точку [x, y] "
            "(например, [[0, 0]] или [[0, 0], [50, 0]])."
        )

    try:
        # ---- Этап 1: Проверка точек и приведение координат к float ----
        # Каждый центр обязан быть списком минимум из [x, y].
        for pt in centers:
            if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                return build_error(
                    "Каждый центр окружности должен быть списком "
                    "координат [x, y] или [x, y, z]."
                )
            # Принудительная конвертация координат во float (Канон 3).
            float(pt[0])
            float(pt[1])

        radius_value = float(radius)
    except (TypeError, ValueError):
        return build_error("Координаты центров и радиус должны быть числами.")

    if radius_value <= 0.0:
        return build_error("Радиус окружности должен быть положительным числом.")

    # ---- Этап 2: Получение живого моста связи с САПР ----
    acad = ensure_connection()
    if acad is None:
        return build_error(
            "Не удалось установить соединение с AutoCAD. "
            "Проверьте, что САПР запущена, и повторите попытку."
        )

    # ---- Этап 3: Пакетный цикл построения окружностей (нативный COM) ----
    try:
        handles: list = []
        for pt in centers:
            # Третья координата необязательна: по умолчанию плоскость Z = 0.0.
            x, y = float(pt[0]), float(pt[1])
            z = float(pt[2]) if len(pt) > 2 else 0.0
            center_point = APoint(x, y, z)
            circle_obj = acad.model.AddCircle(center_point, radius_value)
            # Handle каждой построенной окружности — в общий массив ответа.
            handles.append(str(circle_obj.Handle))

        # ---- Этап 4: Формирование единого ответа (Пока-ёкэ) ----
        message = "Успешно построено окружностей: {}".format(len(handles))
        return json.dumps(
            {"status": "success", "handles": handles, "message": message},
            ensure_ascii=False,
        )
    except Exception as exc:
        # Любая COM/чертёжная ошибка перехватывается и возвращается в JSON.
        return build_error("Ошибка черчения окружностей: {}".format(exc))