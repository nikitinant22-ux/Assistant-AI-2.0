# -*- coding: utf-8 -*-
"""draw_polyline.py — Атомарный кубик №2: построение лёгкой полилинии.

Изолированный файл по принципу «ОДИН КУБИК — ОДИН НЕЗАВИСИМЫЙ ФАЙЛ»
(Этап 6, Шаг 2). Декоратор @register_cad_tool(name="draw_polyline")
автоматически регистрирует кубик в глобальном реестре
tool_registry.CAD_TOOL_REGISTRY при импорте — автосканер пакета tools
загружает этот файл при старте приложения. Файл является МОДУЛЕМ: он не
запускается как скрипт (это исключает двойную регистрацию); самодиагностика
кубиков живёт в main_router.py (__main__).

Архитектурные правила (Канон 3, 8.1, 8.2, 16.5):
  - связь с САПР — исключительно через pyautocad (мост tools._shared);
  - координаты принудительно приводятся к float, плоский массив double
    упаковывается нативным хелпером aDouble;
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
from tools._shared import build_error, ensure_connection


@register_cad_tool(name="draw_polyline")
def draw_polyline(points: List[List[Union[int, float]]]) -> str:
    """LightWeightPolyline builder: connects vertices into one polyline.

    Accepts a list of vertices, each vertex is [x, y] coordinates (the third
    z coordinate is ignored: a lightweight polyline always lies in the XY
    plane). Coordinate rule: at least TWO vertices are required, upper bound
    is unlimited. All numbers are force-converted to float and packed into a
    FLAT one-dimensional array [x1, y1, x2, y2, ...] — the exact format
    required by the ActiveX method AddLightWeightPolyline(VerticesList).
    Packing uses the native pyautocad helper aDouble.

    Args:
        points: List of vertices [[x1, y1], [x2, y2], ...] (at least two).

    Returns:
        Strictly valid JSON string:
          - success: {"status": "success", "handle": "<ID>",
                      "message": "Polyline built, vertex count: X"}
          - error:   {"status": "error", "message": "Error description"}
    """
    # ---- Этап 0: Валидация входных данных (Пока-ёкэ) ----
    # Правило координат: минимум ДВЕ вершины, каждая — [x, y].
    if not isinstance(points, (list, tuple)) or len(points) < 2:
        return build_error(
            "Список точек должен содержать минимум две вершины [x, y] "
            "(например, [[0, 0], [100, 0]])."
        )

    try:
        # ---- Этап 1: Проверка вершин и приведение координат к float ----
        # Каждая вершина обязана быть списком минимум из [x, y].
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                return build_error(
                    "Каждая вершина полилинии должна быть списком "
                    "координат [x, y]."
                )
            # Принудительная конвертация обеих координат во float (Канон 3).
            float(point[0])
            float(point[1])

        # Плоский одномерный массив double: [x1, y1, x2, y2, ...].
        flat_array_of_doubles: list = [
            float(coord) for point in points for coord in point[:2]
        ]
    except (TypeError, ValueError):
        return build_error("Все координаты вершин должны быть числами.")

    # ---- Этап 2: Получение живого моста связи с САПР ----
    acad = ensure_connection()
    if acad is None:
        return build_error(
            "Не удалось установить соединение с AutoCAD. "
            "Проверьте, что САПР запущена, и повторите попытку."
        )

    # ---- Этап 3: Нативный вызов AddLightWeightPolyline (плоский массив double) ----
    try:
        # aDouble упаковывает список float в SAFEARRAY(double) — именно его
        # ждёт ActiveX-метод AddLightWeightPolyline (Канон 3, pyautocad).
        polyline_obj = acad.model.AddLightWeightPolyline(
            acad.aDouble(flat_array_of_doubles)
        )

        # ---- Этап 4: Формирование ответа (Пока-ёкэ) ----
        handle = str(polyline_obj.Handle)
        vertex_count = len(points)
        message = "Полилиния успешно построена, количество вершин: {}".format(
            vertex_count
        )
        return json.dumps(
            {"status": "success", "handle": handle, "message": message},
            ensure_ascii=False,
        )
    except Exception as exc:
        # Любая COM/чертёжная ошибка перехватывается и возвращается в JSON.
        return build_error("Ошибка черчения полилинии: {}".format(exc))