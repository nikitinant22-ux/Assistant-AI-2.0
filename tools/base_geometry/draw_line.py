# -*- coding: utf-8 -*-
"""draw_line.py — Атомарный кубик №4: построение отрезков (одиночные/цепочки).

Изолированный файл по принципу «ОДИН КУБИК — ОДИН НЕЗАВИСИМЫЙ ФАЙЛ»
(Этап 6, Шаг 2). Декоратор @register_cad_tool(name="draw_line")
автоматически регистрирует кубик в глобальном реестре
tool_registry.CAD_TOOL_REGISTRY при импорте — автосканер пакета tools
загружает этот файл при старте приложения. Файл является МОДУЛЕМ: он не
запускается как скрипт (это исключает двойную регистрацию); самодиагностика
кубиков живёт в main_router.py (__main__).

Кубик обслуживает два режима построения отрезков:
  1. mode="single" — один независимый отрезок Line по двум точкам
     (p1 [x1,y1,z1] и p2 [x2,y2,z2]);
  2. mode="multi"  — последовательная цепочка НЕЗАВИСИМЫХ отрезков Line:
     каждая пара соседних точек соединяется отдельным объектом (никакого
     слияния в полилинию — лояльный режим без замыкания Closed).

Архитектурные правила (Канон 3, 8.1, 8.2, 16.5):
  - связь с САПР — исключительно через pyautocad (мост tools._shared);
  - координаты принудительно приводятся к float и упаковываются строго
    в нативный класс APoint (третья координата z необязательна: 0.0);
  - у отрезков НЕТ свойства Closed — каждый сегмент остаётся открытым;
  - кубик возвращает строго валидную JSON-строку (принцип Пока-ёкэ).
"""

import json
import sys
from pathlib import Path

# Bootstrap корня проекта в sys.path: страховка для нестандартных контекстов
# импорта. В штатном режиме автосканер загружает модуль через importlib —
# там пути уже корректны, и условие ниже не срабатывает.
_ROOT_PROJECT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT_PROJECT) not in sys.path:
    sys.path.insert(0, str(_ROOT_PROJECT))

# Декоратор автоматической регистрации кубика (MCP-протокол, Этап 5).
from tool_registry import register_cad_tool
# Общий служебный слой пакета: мост связи с САПР, точка APoint, JSON-ошибки.
from tools._shared import APoint, build_error, ensure_connection


def _to_apoint(point: list) -> APoint:
    """Нормализует точку в нативный APoint с принудительными float.

    Аргументы:
        point: Список координат [x, y] или [x, y, z].

    Возвращает:
        APoint — трёхмерную точку pyautocad (z по умолчанию 0.0).

    Исключения:
        TypeError/ValueError/IndexError — если координаты нечисловые
        или список короче двух элементов (перехватываются вызывающим кодом).
    """
    px = float(point[0])
    py = float(point[1])
    # Третья координата необязательна: плоскость Z = 0.0 (Канон 3).
    pz = float(point[2]) if len(point) > 2 else 0.0
    return APoint(px, py, pz)


@register_cad_tool(name="draw_line")
def draw_line(
    mode: str,
    p1: list = None,
    p2: list = None,
    points: list = None,
) -> str:
    """Line builder: draws single segments or independent segment chains.

    Builds AutoCAD Line objects (open segments — they have NO Closed
    property by design). Two distinct modes:

    Mode "single": draws ONE independent line between two points.
    Requires p1 [x1, y1, z1] and p2 [x2, y2, z2] (z is optional, default
    0.0). Uses the native COM method AddLine(StartPoint, EndPoint).

    Mode "multi": draws a chain of INDEPENDENT Line segments connecting
    consecutive point pairs. Requires a points array
    [[x1, y1, z1], [x2, y2, z2], ...] with AT LEAST TWO points. Each pair
    (points[i], points[i+1]) becomes a separate Line object — segments are
    never merged into a polyline and are never closed.

    In both modes every coordinate is force-converted to float and packed
    into the native pyautocad point class (APoint). Returns the Handles of
    all created segments.

    Args:
        mode: Mode selector, either "single" or "multi".
        p1: (single only) Start point [x1, y1, z1].
        p2: (single only) End point [x2, y2, z2].
        points: (multi only) Chain vertices [[x1, y1, z1], [x2, y2, z2], ...]
                (at least two points).

    Returns:
        Strictly valid JSON string:
          - success: {"status": "success", "handles": ["ID1", ...],
                      "message": "Lines built: N"}
          - error:   {"status": "error", "message": "Error description"}
    """
    # ---- Этап 0: Диспетчеризация по выбранному режиму (Пока-ёкэ) ----
    if mode == "single":
        # Обе точки обязаны быть списками координат минимум из [x, y].
        if (
            not isinstance(p1, (list, tuple)) or len(p1) < 2
            or not isinstance(p2, (list, tuple)) or len(p2) < 2
        ):
            return build_error(
                "Для одиночного отрезка обе точки p1 и p2 обязаны быть "
                "списками координат [x, y] или [x, y, z]."
            )
        try:
            # Принудительное приведение координат к float (Канон 3).
            start_point = _to_apoint(p1)
            end_point = _to_apoint(p2)
        except (TypeError, ValueError, IndexError):
            return build_error("Координаты точек отрезка должны быть числами.")
        segments: list = [(start_point, end_point)]
    elif mode == "multi":
        # Цепочка обязана содержать минимум ДВЕ точки (один сегмент).
        if (
            not isinstance(points, (list, tuple)) or len(points) < 2
        ):
            return build_error(
                "Для цепочки отрезков массив points должен содержать "
                "минимум две точки (например, [[0, 0], [100, 0]])."
            )
        try:
            # Нормализуем все вершины в нативные APoint заранее (Пока-ёкэ).
            apoints: list = [_to_apoint(pt) for pt in points]
        except (TypeError, ValueError, IndexError):
            return build_error(
                "Каждая точка цепочки должна быть списком координат "
                "[x, y] или [x, y, z] с числовыми значениями."
            )
        # Попарное соединение соседних вершин независимыми отрезками.
        segments = [
            (apoints[i], apoints[i + 1]) for i in range(len(apoints) - 1)
        ]
    else:
        # Неизвестный режим — честная JSON-ошибка вместо тихого молчания.
        return build_error(
            "Неизвестный режим '{}': доступны только 'single' и 'multi'.".format(
                mode
            )
        )

    # ---- Этап 1: Получение живого моста связи с САПР ----
    acad = ensure_connection()
    if acad is None:
        return build_error(
            "Не удалось установить соединение с AutoCAD. "
            "Проверьте, что САПР запущена, и повторите попытку."
        )

    # ---- Этап 2: Нативный вызов AddLine по всем сегментам (без Closed) ----
    try:
        handles: list = []
        for start_point, end_point in segments:
            line_obj = acad.model.AddLine(start_point, end_point)
            # Handle каждого построенного отрезка — в общий массив ответа.
            handles.append(str(line_obj.Handle))

        # ---- Этап 3: Формирование ответа (Пока-ёкэ) ----
        message = "Успешно построено отрезков: {}".format(len(handles))
        return json.dumps(
            {"status": "success", "handles": handles, "message": message},
            ensure_ascii=False,
        )
    except Exception as exc:
        # Любая COM/чертёжная ошибка перехватывается и возвращается в JSON.
        return build_error("Ошибка черчения отрезков: {}".format(exc))