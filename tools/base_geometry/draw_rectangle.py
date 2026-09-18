# -*- coding: utf-8 -*-
"""draw_rectangle.py — Атомарный кубик №3: полиморфный построитель контуров.

Изолированный файл по принципу «ОДИН КУБИК — ОДИН НЕЗАВИСИМЫЙ ФАЙЛ»
(Этап 6, Шаг 2). Декоратор @register_cad_tool(name="draw_rectangle")
автоматически регистрирует кубик в глобальном реестре
tool_registry.CAD_TOOL_REGISTRY при импорте — автосканер пакета tools
загружает этот файл при старте приложения. Файл является МОДУЛЕМ: он не
запускается как скрипт (это исключает двойную регистрацию); самодиагностика
кубиков живёт в main_router.py (__main__).

Кубик ПОЛИМОРФЕН и обслуживает два режима построения замкнутых контуров:
  1. mode="rectangle" — прямоугольник по двум противоположным диагональным
     точкам (p1 [x1, y1] и p2 [x2, y2]);
  2. mode="polygon"   — правильный многоугольник по центру описанной
     окружности (center [x, y]), радиусу (radius) и числу сторон (sides).

Архитектурные правила (Канон 3, 8.1, 8.2, 16.5):
  - связь с САПР — исключительно через pyautocad (мост tools._shared);
  - координаты принудительно приводятся к float, плоский массив double
    упаковывается стандартным модулем array (тип 'd') — точный формат
    ActiveX-метода AddLightWeightPolyline;
  - вершины многоугольника вычисляются тригонометрическим обходом
    (math.sin / math.cos) по равномерному угловому шагу 2*pi/sides;
  - замкнутость контура устанавливается намертво свойством Closed (Пока-ёкэ
    дыр в геометрии);
  - кубик возвращает строго валидную JSON-строку (принцип Пока-ёкэ).
"""

import array
import json
import math
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
# Общий служебный слой пакета: мост связи с САПР и строитель JSON-ошибок.
from tools._shared import build_error, ensure_connection


@register_cad_tool(name="draw_rectangle")
def draw_rectangle(
    mode: str,
    p1: list = None,
    p2: list = None,
    center: list = None,
    radius: float = None,
    sides: int = None,
) -> str:
    """Polymorphic closed-outline builder: rectangle OR regular polygon.

    Builds a closed lightweight polyline in AutoCAD in two distinct modes.

    Mode "rectangle": draws a rectangle from two OPPOSITE diagonal points.
    Requires p1 [x1, y1] and p2 [x2, y2]. The four vertices are derived by
    walking the perimeter sequentially: (x1, y1) -> (x2, y1) -> (x2, y2) ->
    (x1, y2). The flat coordinate array is [x1, y1, x2, y1, x2, y2, x1, y2].

    Mode "polygon": draws a REGULAR polygon (N-gon) inscribed in a circle.
    Requires center [x, y] (circumcircle center), radius (circumradius, a
    positive number) and sides (vertex count, an integer, minimum 3). Each
    i-th vertex lies on the circle and is computed by trigonometric
    traversal: angle = i * (2 * pi / sides), x = cx + radius * cos(angle),
    y = cy + radius * sin(angle).

    In both modes all numbers are force-converted to float and packed into
    a FLAT one-dimensional array of doubles via the standard library module
    array (type 'd') — the exact format required by the ActiveX method
    AddLightWeightPolyline(VerticesList). The outline is then forcibly
    closed via the property Closed = True (Poka-Yoke against geometry gaps).

    Args:
        mode: Mode selector, either "rectangle" or "polygon".
        p1: (rectangle only) First diagonal corner [x1, y1].
        p2: (rectangle only) Opposite diagonal corner [x2, y2].
        center: (polygon only) Circumcircle center [x, y].
        radius: (polygon only) Circumradius (positive number).
        sides: (polygon only) Number of sides (integer, minimum 3).

    Returns:
        Strictly valid JSON string:
          - success: {"status": "success", "handle": "<ID>",
                      "message": "Объект успешно построен"}
          - error:   {"status": "error", "message": "Error description"}
    """
    # ---- Этап 0: Диспетчеризация по выбранному режиму (Пока-ёкэ) ----
    if mode == "rectangle":
        return _build_rectangle(p1, p2)
    if mode == "polygon":
        return _build_polygon(center, radius, sides)
    # Неизвестный режим — честная JSON-ошибка вместо тихого молчания.
    return build_error(
        "Неизвестный режим '{}': доступны только 'rectangle' и 'polygon'.".format(
            mode
        )
    )


def _build_rectangle(p1: list, p2: list) -> str:
    """Строит замкнутый прямоугольник по двум диагональным точкам.

    Аргументы:
        p1: Первая точка диагонали [x1, y1].
        p2: Противоположная точка диагонали [x2, y2].

    Возвращает:
        Текстовую JSON-строку результата построения (самодиагностика).
    """
    try:
        # ---- Этап 1: Извлечение и приведение координат к float (Канон 3) ----
        # Обе противоположные диагональные точки обязаны быть парами [x, y].
        x1, y1 = float(p1[0]), float(p1[1])
        x2, y2 = float(p2[0]), float(p2[1])

        # ---- Этап 2: Вычисление 4 вершин последовательным обходом периметра ----
        # Порядок обхода гарантирует корректный замкнутый контур без
        # самопересечений: (x1, y1) -> (x2, y1) -> (x2, y2) -> (x1, y2).
        points_flat: list = [x1, y1, x2, y1, x2, y2, x1, y2]
    except (TypeError, ValueError, IndexError):
        return build_error(
            "Для прямоугольника обе точки p1 и p2 обязаны быть списками "
            "координат [x, y] с числовыми значениями."
        )

    # ---- Этап 3: Общий исполнительный путь черчения замкнутой полилинии ----
    return _draw_closed_polyline(points_flat, "Ошибка черчения прямоугольника")


def _build_polygon(center: list, radius: float, sides: int) -> str:
    """Строит правильный многоугольник тригонометрическим обходом.

    Аргументы:
        center: Центр описанной окружности [x, y].
        radius: Радиус описанной окружности (положительное число).
        sides: Количество сторон (целое, минимум 3).

    Возвращает:
        Текстовую JSON-строку результата построения (самодиагностика).
    """
    try:
        # ---- Этап 1: Валидация и приведение параметров к числам ----
        cx, cy = float(center[0]), float(center[1])
        radius_value = float(radius)
        sides_count = int(sides)
    except (TypeError, ValueError, IndexError):
        return build_error(
            "Для многоугольника center обязан быть списком [x, y], "
            "а radius и sides — числами."
        )

    # Геометрические инварианты (Пока-ёкэ): радиус положителен, сторон ≥ 3.
    if radius_value <= 0.0:
        return build_error(
            "Радиус описанной окружности должен быть положительным числом."
        )
    if sides_count < 3:
        return build_error("Многоугольник обязан иметь минимум 3 стороны.")

    # ---- Этап 2: Тригонометрический обход по равномерному угловому шагу ----
    # Каждая i-я вершина лежит на окружности радиуса radius_value с центром
    # (cx, cy): угол отсчитывается от положительной оси X (полярные координаты).
    points_flat: list = []
    try:
        for i in range(sides_count):
            angle = i * (2 * math.pi / sides_count)
            x = cx + radius_value * math.cos(angle)
            y = cy + radius_value * math.sin(angle)
            points_flat.extend([x, y])
    except Exception as exc:
        # Любая ошибка тригонометрических вычислений перехватывается (Пока-ёкэ).
        return build_error("Ошибка вычисления вершин многоугольника: {}".format(exc))

    # ---- Этап 3: Общий исполнительный путь черчения замкнутой полилинии ----
    return _draw_closed_polyline(points_flat, "Ошибка черчения многоугольника")


def _draw_closed_polyline(points_flat: list, error_prefix: str) -> str:
    """Чертит замкнутую лёгкую полилинию по плоскому массиву координат.

    Общий исполнительный путь для обоих режимов: упаковка flat-массива
    double через array.array, нативный вызов AddLightWeightPolyline и
    принудительное замыкание контура свойством Closed (Пока-ёкэ дыр).

    Аргументы:
        points_flat: Плоский список координат [x1, y1, x2, y2, ...].
        error_prefix: Русскоязычный префикс сообщения об ошибке черчения.

    Возвращает:
        Текстовую JSON-строку результата построения (самодиагностика).
    """
    try:
        # Упаковка плоского массива координат в нативный массив double ('d') —
        # именно такой формат ожидает ActiveX API AutoCAD.
        packed_array = array.array("d", points_flat)

        # Получение живого моста связи с САПР.
        acad = ensure_connection()
        if acad is None:
            return build_error(
                "Не удалось установить соединение с AutoCAD. "
                "Проверьте, что САПР запущена, и повторите попытку."
            )

        # Нативный вызов AddLightWeightPolyline + намертво замкнутый контур.
        rect_obj = acad.model.AddLightWeightPolyline(packed_array)
        rect_obj.Closed = True

        # Формирование стандартного ответа (Пока-ёкэ).
        return json.dumps(
            {
                "status": "success",
                "handle": str(rect_obj.Handle),
                "message": "Объект успешно построен",
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        # Любая COM/чертёжная ошибка перехватывается и возвращается в JSON.
        return build_error("{}: {}".format(error_prefix, exc))