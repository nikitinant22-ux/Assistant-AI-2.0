# -*- coding: utf-8 -*-
"""agent_draftsman.py — ИИ-агент «Чертёжник» (pyautocad).

Данный модуль отвечает за роль Ассистента-Чертёжника: он занимается исключительно
созданием и точной привязкой геометрии в пространстве модели AutoCAD. Все
инструменты оформлены как статичные Python-функции, принимают только базовые
типы данных (int, float, str, list) и всегда возвращают структурированный
JSON-совместимый словарь вида {"status": "success", "handle": ...}.

Работа выполняется СТРОГО через стабильную оболочку pyautocad:
    - acad.model  -> пространство модели (AddLine, AddCircle, ...);
    - to_cad_point() -> нативный класс точки APoint из core_core.

Ключевые принципы:
    1. Все координаты перед передачей в ActiveX упаковываются в APoint через
       core_core.to_cad_point().
    2. Внутри модуля НЕ используется exec()/eval() — только статичные функции.
    3. После построения объекта обязательно вызывается update_screen()
       (внутри — acad.doc.Regen(1)).
    4. Вся работа с AutoCAD обёрнута в try-except: при ошибке возвращается
       статус {"status": "error", "message": "..."} на русском языке.

Все комментарии и строки документации написаны на русском языке.
"""

from __future__ import annotations

# Главный объект связи pyautocad и фабрика double-массивов для полилиний.
from core_core import ensure_connection, get_acad, to_cad_point, update_screen


# ========================================================================
# ВНУТРЕННИЕ УТИЛИТЫ ПОДКЛЮЧЕНИЯ
# ========================================================================

def _ensure_connection():
    """Гарантирует актуальное подключение к AutoCAD.

    Делегирует проверку «пульса» COM-сессии в core_core.ensure_connection(),
    которая при обрыве (-2147220995 'Объект не подключен к серверу') на лету
    пересоздаёт мост pyautocad. Вызывается в начале каждой чертёжной функции,
    чтобы исключить падение последующих команд в цепочке запросов инженера.

    Возвращает:
        bool — True, если связь жива или успешно восстановлена;
               False — если AutoCAD физически закрыт.
    """
    return ensure_connection()


def _ok(handle, message):
    """Формирует словарь успешного результата операции построения.

    Аргументы:
        handle: строковый дескриптор (Handle) созданного объекта.
        message: текстовое описание результата на русском языке.

    Возвращает:
        dict — JSON-совместимый словарь со статусом «success».
    """
    return {"status": "success", "handle": str(handle), "message": message}


def _err(message):
    """Формирует словарь ошибки операции построения.

    Аргументы:
        message: текстовое описание ошибки на русском языке.

    Возвращает:
        dict — JSON-совместимый словарь со статусом «error».
    """
    return {"status": "error", "message": message}


# ========================================================================
# ГЕОМЕТРИЧЕСКИЕ ИНСТРУМЕНТЫ ЧЕРТЁЖНИКА (БЛОКИ 1-3 СПЕЦИФИКАЦИИ)
# ========================================================================

def draw_circle(x: float, y: float, radius: float, color: int = 7):
    """Строит круг в пространстве модели (Блок 1: acad.model.AddCircle).

    Центр пропускается через to_cad_point() и превращается в APoint, радиус
    приводится к float. Возвращается Handle созданного круга.

    Аргументы:
        x: координата X центра круга.
        y: координата Y центра круга.
        radius: радиус круга (положительное число).
        color: индекс стандартного цвета ACI (по умолчанию 7 — белый).

    Возвращает:
        dict — {"status": "success", "handle": "...", "message": "..."}
               либо {"status": "error", "message": "..."} при ошибке.
    """
    try:
        if not _ensure_connection():
            return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
        center = to_cad_point(x, y)
        circle = get_acad().model.AddCircle(center, float(radius))
        # Применяем заданный цвет ACI, если он отличается от стандартного.
        if int(color) != 7:
            try:
                circle.Color = int(color)
            except Exception:
                pass
        update_screen()
        return _ok(circle.Handle, f"Круг с радиусом {radius} построен в точке ({x}, {y}).")
    except Exception as e:
        return _err(f"Ошибка построения круга: {e}")


def draw_line(x1: float, y1: float, x2: float, y2: float):
    """Строит отрезок между двумя точками (Блок 1: acad.model.AddLine).

    Обе конечные точки упаковываются в APoint через to_cad_point().

    Аргументы:
        x1, y1: координаты первой конечной точки отрезка.
        x2, y2: координаты второй конечной точки отрезка.

    Возвращает:
        dict — {"status": "success", "handle": "...", "message": "..."}
               либо {"status": "error", "message": "..."} при ошибке.
    """
    try:
        if not _ensure_connection():
            return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
        start = to_cad_point(x1, y1)
        end = to_cad_point(x2, y2)
        line = get_acad().model.AddLine(start, end)
        update_screen()
        return _ok(line.Handle,
                   f"Отрезок построен от ({x1}, {y1}) до ({x2}, {y2}).")
    except Exception as e:
        return _err(f"Ошибка построения отрезка: {e}")


def draw_polyline(points_list: list, is_closed: bool = False):
    """Строит лёгкую полилинию (LWPolyline) по плоскому массиву координат.

    Список вида [[x1, y1], [x2, y2], ...] (или плоский [x1, y1, x2, y2, ...])
    разворачивается в одномерный double-массив и передаётся в метод
    acad.model.AddLightWeightPolyline (Блок 1). При is_closed=True задаётся
    свойство Closed, замыкающее контур.

    Аргументы:
        points_list: список вершин (плоский либо список пар/кортежей).
        is_closed: флаг замкнутости полилинии.

    Возвращает:
        dict — результат операции построения (успех или ошибка).
    """
    try:
        if not _ensure_connection():
            return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
        if not points_list or len(points_list) < 2:
            return _err("Для построения полилинии необходимо минимум две точки.")

        # Разворачиваем вход (плоский или список пар) в плоский double-массив.
        flat = []
        for item in points_list:
            if isinstance(item, (list, tuple)):
                flat.extend(float(c) for c in item[:2])
            else:
                flat.append(float(item))

        poly = get_acad().model.AddLightWeightPolyline(get_acad().aDouble(flat))
        # Задаём замкнутость контура по требованию.
        try:
            poly.Closed = bool(is_closed)
        except Exception:
            pass
        update_screen()
        return _ok(poly.Handle, f"Полилиния построена из {len(points_list)} вершин.")
    except Exception as e:
        return _err(f"Ошибка построения полилинии: {e}")


def draw_rectangle(x1: float, y1: float, x2: float, y2: float):
    """Строит замкнутый прямоугольник по двум противоположным углам.

    Капсульный алгоритм по 4 точкам: формируется плоский массив
    [x1, y1, x2, y1, x2, y2, x1, y2] и вызывается AddLightWeightPolyline с
    последующим .Closed = True (Блок 1 спецификации).

    Аргументы:
        x1, y1: координаты первого противоположного угла.
        x2, y2: координаты второго противоположного угла.

    Возвращает:
        dict — результат операции построения (успех или ошибка).
    """
    try:
        if not _ensure_connection():
            return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
        fx1, fy1, fx2, fy2 = float(x1), float(y1), float(x2), float(y2)
        # Капсульный алгоритм: 4 вершины прямоугольника по часовой стрелке.
        flat = [fx1, fy1, fx2, fy1, fx2, fy2, fx1, fy2]
        rect = get_acad().model.AddLightWeightPolyline(get_acad().aDouble(flat))
        # Замыкаем контур прямоугольника.
        try:
            rect.Closed = True
        except Exception:
            pass
        update_screen()
        return _ok(rect.Handle,
                   f"Прямоугольник построен от ({fx1}, {fy1}) до ({fx2}, {fy2}).")
    except Exception as e:
        return _err(f"Ошибка построения прямоугольника: {e}")


def draw_text(text: str, x: float, y: float, height: float = 2.5):
    """Вставляет однострочный текст (Блок 2: acad.model.AddText).

    Аргументы:
        text: строка текста для вставки.
        x, y: координаты точки вставки текста.
        height: высота шрифта (по умолчанию 2.5).

    Возвращает:
        dict — результат операции (успех с Handle или ошибка).
    """
    try:
        if not _ensure_connection():
            return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
        txt = str(text)
        point = to_cad_point(x, y)
        obj = get_acad().model.AddText(txt, point, float(height))
        update_screen()
        return _ok(obj.Handle, f"Текст \"{txt}\" вставлен в точке ({x}, {y}).")
    except Exception as e:
        return _err(f"Ошибка вставки текста: {e}")


def draw_mtext(text: str, x: float, y: float, width: float = 100.0):
    """Вставляет многострочный текст (Блок 2: acad.model.AddMText).

    Аргументы:
        text: строка текста для вставки (поддерживает многострочность).
        x, y: координаты точки вставки.
        width: ширина ограничивающей рамки МText (по умолчанию 100.0).

    Возвращает:
        dict — результат операции (успех с Handle или ошибка).
    """
    try:
        if not _ensure_connection():
            return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
        txt = str(text)
        point = to_cad_point(x, y)
        obj = get_acad().model.AddMText(point, float(width), txt)
        update_screen()
        return _ok(obj.Handle, f"Многострочный текст вставлен в точке ({x}, {y}).")
    except Exception as e:
        return _err(f"Ошибка вставки многострочного текста: {e}")


def insert_block(block_name: str, x: float, y: float,
                 scale: float = 1.0, rotation_rad: float = 0.0):
    """Вставляет блок (Блок 3: acad.model.InsertBlock).

    Равномерно масштабирует блок по трём осям и задаёт угол поворота в радианах.

    Аргументы:
        block_name: имя существующего блока в чертеже.
        x, y: координаты точки вставки блока.
        scale: коэффициент масштабирования (по умолчанию 1.0).
        rotation_rad: угол поворота блока в радианах (по умолчанию 0.0).

    Возвращает:
        dict — результат операции (успех с Handle или ошибка).
    """
    try:
        if not _ensure_connection():
            return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
        point = to_cad_point(x, y)
        s = float(scale)
        obj = get_acad().model.InsertBlock(
            point, str(block_name), s, s, s, float(rotation_rad))
        update_screen()
        return _ok(obj.Handle, f"Блок \"{block_name}\" вставлен в точке ({x}, {y}).")
    except Exception as e:
        return _err(f"Ошибка вставки блока: {e}")


def draw_ellipse(center_x: float, center_y: float,
                 major_x: float, major_y: float, radius_ratio: float):
    """Строит эллипс по центру, вектору большой оси и соотношению радиусов.

    Метод AddEllipse требует точку центра, конечную точку большой оси и
    соотношение малого радиуса к большому (radius_ratio). Все координаты
    строго типизируются через to_cad_point() в нативные APoint.

    Аргументы:
        center_x, center_y: координаты центра эллипса.
        major_x, major_y: координаты конечной точки вектора большой оси.
        radius_ratio: соотношение радиусов (малый / большой), значение 0..1.

    Возвращает:
        dict — результат операции построения (успех или ошибка).
    """
    try:
        if not _ensure_connection():
            return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
        ratio = float(radius_ratio)
        if not (0.0 < ratio <= 1.0):
            return _err("Соотношение радиусов эллипса должно быть в диапазоне (0, 1].")
        center = to_cad_point(center_x, center_y)
        major_end = to_cad_point(major_x, major_y)
        ellipse = get_acad().model.AddEllipse(center, major_end, ratio)
        update_screen()
        return _ok(ellipse.Handle,
                   f"Эллипс построен: центр ({center_x}, {center_y}), "
                   f"соотношение радиусов {ratio}.")
    except Exception as e:
        return _err(f"Ошибка построения эллипса: {e}")


def fit_object_in_circle(object_handle: str, padding: float = 10.0):
    """Описывает существующий объект в окружность.

    По уникальному Handle объект находится через acad.doc.HandleToObject(). Затем
    считываются его габариты методом .GetBoundingBox(), вычисляется геометрический
    центр и радиус (половина диагонали ограничивающей рамки + отступ padding).
    Вокруг объекта автоматически строится красная окружность (Color = 1).

    Аргументы:
        object_handle: строковый Handle существующего объекта в чертеже.
        padding: дополнительный отступ (зазор) вокруг объекта в единицах чертежа.

    Возвращает:
        dict — результат операции (успех с Handle круга или ошибка).
    """
    try:
        if not _ensure_connection():
            return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
        if not object_handle:
            return _err("Не задан Handle объекта для описания в окружность.")

        # Находим объект в базе чертежа по его уникальному дескриптору.
        try:
            target_obj = get_acad().doc.HandleToObject(str(object_handle))
        except Exception:
            return _err(f"Объект с Handle '{object_handle}' не найден в чертеже.")

        # Считываем ограничивающую рамку объекта (нижняя и верхняя точки).
        min_point, max_point = target_obj.GetBoundingBox()

        # Вычисляем геометрический центр ограничивающей рамки.
        cx = (float(min_point[0]) + float(max_point[0])) / 2.0
        cy = (float(min_point[1]) + float(max_point[1])) / 2.0

        # Радиус = половина диагонали рамки + отступ padding.
        half_width = (float(max_point[0]) - float(min_point[0])) / 2.0
        half_height = (float(max_point[1]) - float(min_point[1])) / 2.0
        radius = (half_width ** 2 + half_height ** 2) ** 0.5 + float(padding)

        # Строим описывающую окружность красного цвета (Color = 1).
        center = to_cad_point(cx, cy)
        circle = get_acad().model.AddCircle(center, radius)
        try:
            circle.Color = 1
        except Exception:
            pass
        update_screen()
        return _ok(circle.Handle,
                   f"Объект '{object_handle}' описан окружностью радиусом {radius:.2f}.")
    except Exception as e:
        return _err(f"Ошибка описания объекта в окружность: {e}")


def fit_circle_to_object(object_handle: str, mode: str = "described"):
    """Вписывает или описывает окружность вокруг объекта по его Handle.

    Универсальный математический алгоритм на базе метода GetBoundingBox:
        1. Объект находится через acad.doc.HandleToObject(object_handle).
        2. Считываются крайние точки габаритов min_pt и max_pt.
        3. Вычисляется геометрический центр ограничивающей рамки.
        4. Радиус окружности зависит от выбранного режима:
           - mode == "described" (Описать СНАРУЖИ): половина диагонали рамки;
           - mode == "inscribed"  (Вписать ВНУТРЬ):  половина меньшей стороны.
        5. Строится окружность (acad.model.AddCircle) красного цвета (Color = 1).

    Аргументы:
        object_handle: строковый Handle существующего объекта в чертеже.
        mode: режим подгонки — "described" (описать снаружи, по умолчанию)
              либо "inscribed" (вписать внутрь).

    Возвращает:
        dict — результат операции (успех с Handle круга или ошибка).
    """
    try:
        if not _ensure_connection():
            return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
        if not object_handle:
            return _err("Не задан Handle объекта для подгонки окружности.")
        if mode not in ("described", "inscribed"):
            return _err(f"Неизвестный режим '{mode}'. Допустимо: described | inscribed.")

        # Находим объект в базе чертежа по его уникальному дескриптору.
        try:
            target_obj = get_acad().doc.HandleToObject(str(object_handle))
        except Exception:
            return _err(f"Объект с Handle '{object_handle}' не найден в чертеже.")

        # Считываем ограничивающую рамку объекта (крайние точки габаритов).
        min_pt, max_pt = target_obj.GetBoundingBox()

        # Математический расчёт геометрического центра рамки.
        cx = (float(min_pt[0]) + float(max_pt[0])) / 2.0
        cy = (float(min_pt[1]) + float(max_pt[1])) / 2.0

        # Ширина и высота ограничивающей рамки.
        width = float(max_pt[0]) - float(min_pt[0])
        height = float(max_pt[1]) - float(min_pt[1])

        # Радиус окружности зависит от выбранного режима подгонки.
        if mode == "inscribed":
            # Вписать ВНУТРЬ: радиус равен половине меньшей стороны рамки.
            radius = min(width, height) / 2.0
            label = "вписанная"
        else:
            # Описать СНАРУЖИ: радиус равен половине диагонали рамки.
            radius = ((width ** 2 + height ** 2) ** 0.5) / 2.0
            label = "описанная"

        # Строим окружность и выделяем её красным цветом (Color = 1).
        center = to_cad_point(cx, cy)
        circle = get_acad().model.AddCircle(center, radius)
        try:
            circle.Color = 1
        except Exception:
            pass
        update_screen()
        return _ok(circle.Handle,
                   f"{label.capitalize()} окружность ({mode}) радиусом {radius:.2f} "
                   f"построена вокруг объекта '{object_handle}'.")
    except Exception as e:
        return _err(f"Ошибка подгонки окружности под объект: {e}")


# ========================================================================
# ЖЁСТКИЕ ПРАВИЛА РАБОТЫ С AUTOCAD (общие для всех ИИ-агентов)
# ========================================================================
# Правила, встраиваемые в системный промпт, чтобы модель не совершала типовых
# ошибок при работе через pyautocad и нативный класс точек APoint.

AUTOCAD_RULES: str = (
    "═══ УЛЬТИМАТИВНОЕ ПРАВИЛО ТИПИЗАЦИИ PYTHON -> AUTOCAD ═══\n"
    "AutoCAD COM НЕ принимает нативные списки Python (вроде [x, y, z]). Каждый раз, "
    "когда ты передаёшь точку, вектор или массив координат (Point, Center, "
    "InsertionPoint, VerticesList и аналогичные) — ОБЯЗАН упаковывать их в нативный "
    "класс точек pyautocad APoint. Используй готовую функцию core_core.to_cad_point():\n"
    "    from core_core import to_cad_point\n"
    "    pt = to_cad_point(x, y, z)\n"
    "Это гарантирует тип array.array('d', ...), понятный AutoCAD ActiveX.\n\n"

    "═══ ПРАВИЛО РАБОТЫ С УГЛАМИ (РАДИАНЫ) ═══\n"
    "Все тригонометрические методы AutoCAD (Rotate, InsertBlock и др.) принимают "
    "углы ТОЛЬКО в радианах. Если пользователь указал угол в градусах — приведи "
    "его к радианам через math.radians(angle_deg). Никогда не передавай градусы "
    "напрямую в Rotate.\n\n"

    "═══ ПРАВИЛО ОБНОВЛЕНИЯ ЭКРАНА (РЕГЕНЕРАЦИЯ) ═══\n"
    "После любого построения или изменения объекта вызывай update_screen() из "
    "core_core (внутри она делает acad.doc.Regen(1)), чтобы пользователь сразу "
    "увидел результат. Для массовых изменений также используется doc.Regen(1).\n"
)


# ========================================================================
# ЕДИНЫЙ СИСТЕМНЫЙ ПРОМПТ ДЛЯ QWEN (Чертежник)
# ========================================================================
# Вся инструкция хранится здесь и используется модулем cad_api через ленивый
# импорт. Формат ответа модели — ДЕКЛАРАТИВНЫЙ JSON-паспорт, а не исполняемый
# код, поэтому в системе отсутствуют exec()/eval() и прямая генерация кода.

SYSTEM_PROMPT: str = (
    "Ты — «Умный автономный инженер» автоматизации AutoCAD 2025 "
    "(Мультиагентная архитектура, агент «Чертёжник»). "
    "Ты — прозрачный «думающий соавтор», а НЕ молчаливый исполнитель скриптов.\n\n"

    "═══ ЖЁСТКИЙ СТАНДАРТ СТРУКТУРЫ ОТВЕТА ═══\n"
    "Каждый твой ответ состоит из ДВУХ НЕЗАВИСИМЫХ блоков.\n\n"

    "### БЛОК А — Цепочка рассуждений (Chain-of-Thought, обычный текст на русском)\n"
    "Начни ответ СТРОГО с трёх структурированных пунктов с эмодзи:\n"
    "🤔 **Как я понял задачу:** краткая интерпретация конечной цели инженера.\n"
    "🔍 **Текущий контекст:** поиск необходимых методов в справочнике cad_reference.md. "
    "Если параметров (координат, направлений, углов, выбранного объекта) НЕ хватает — "
    "остановись и задай пользователю уточняющий вопрос.\n"
    "🛠 **План действий:** пошаговый алгоритм: какие объекты/слои будут построены или затронуты.\n"
    "Этот блок НЕ содержит исполняемого кода — он показывается пользователю как текст.\n\n"

    "### БЛОК Б — Декларативный JSON-паспорт команды (строго внутри тегов ```json ... ```)\n"
    "Только ПОСЛЕ текстовых размышлений размести РОВНО ОДИН JSON-объект вида:\n"
    '{"command": "имя_команды", "params": { ... }}\n'
    "Весь текст до этого блока (Блок А) парсер полностью игнорирует.\n\n"

    "ДОСТУП К ОБЪЕКТАМ AUTOCAD (через фундамент core_core и pyautocad):\n"
    "- acad.model — пространство модели (AddLine, AddCircle, AddLightWeightPolyline, AddText).\n"
    "- acad.doc — активный документ (Name, FullName, Layers, Layouts, HandleToObject).\n"
    "- acad.app — COM Application (ZoomExtents).\n"
    "Для любых координат используй core_core.to_cad_point(), возвращающую APoint.\n\n"

    + AUTOCAD_RULES +

    "ДОСТУПНЫЕ ДЕКЛАРАТИВНЫЕ КОМАНДЫ (command -> описание):\n"
    "- zoom_all — показать весь чертёж целиком (без params).\n"
    "- create_layer — создать слой: {name, color_aci}.\n"
    "- switch_layout — переключить лист: {name}.\n"
    "- zoom_to_object — сфокусировать экран на выделенном объекте.\n"
    "- get_property — прочитать свойство документа: {property: document_name | "
    "document_path | object_count | object_types | layout_name}.\n"
    "- inspect_object — прочитать свойства выделенного объекта.\n"
    "- modify_object — изменить объект: {action: move | rotate | scale | set_layer | "
    "set_color | hide | show | delete, + параметры dx/dy, angle, factor, layer, color}.\n"
    "- find_objects — найти объекты: {obj_type, layer}.\n"
    "- select_by_type — выделить объекты: {obj_type, layer}.\n"
    "- count_by_layer — количество объектов по слоям.\n"
    "- list_layers — список слоёв.\n"
    "- list_layouts — список листов.\n"
    "- undo — отменить последнее действие.\n"
    "- list_open_documents — перечислить открытые чертежи.\n"
    "- unsupported — честный отказ: {reason}.\n\n"

    "ПРАВИЛО ИНФОРМАЦИОННЫХ ЗАПРОСОВ (QUERY):\n"
    "Если пользователь просит данные/строку/свойство/подсчёт (имя чертежа, типы "
    "объектов, количество, список слоёв/листов, свойства объекта) — используй команды "
    "get_property, inspect_object, find_objects, count_by_layer, list_layers, "
    "list_layouts, list_open_documents. На такие запросы ЗАПРЕЩЕНО строить объекты, "
    "создавать слои или менять что-либо на чертеже.\n\n"

    "ЧЕСТНОСТЬ:\n"
    "Если запрос нельзя выполнить — верни паспорт с command='unsupported' и пояснением "
    "в reason. НЕ выдумывай ложные изменения и НЕ имитируй успех.\n\n"

    "═══ КОНТУР УТОЧНЯЮЩЕГО ДИАЛОГА (ЗАЩИТА ОТ ГЛУПОСТИ) ═══\n"
    "Если пользователь просит выполнить геометрическое действие, НО в запросе НЕ ХВАТАЕТ "
    "критических параметров (направление, расстояние/угол/коэффициент, выделенный объект) — "
    "НЕ выдавай JSON. Вместо этого верни ТОЛЬКО вежливый текстовый вопрос на русском, "
    "который: (1) повторяет понятую часть задачи, (2) перечисляет недостающие параметры, "
    "(3) предлагает варианты ответа. Система распознает это как уточняющий диалог.\n\n"

    "ЗАПРЕТ ЗАЦИКЛИВАНИЯ:\n"
    "Если предыдущий сгенерированный паспорт для этого запроса был отклонён — НИКОГДА "
    "не повторяй тот же алгоритм. Кардинально смени подход и выбери другой набор команд.\n"
)


def build_system_instructions() -> str:
    """Возвращает единственный источник истины системного промпта для Qwen 2.5.

    Функция находится в «Чертёжнике» (agent_draftsman.py) и делегируется модулем
    cad_api.build_system_instructions() через ленивый импорт во избежание
    циклической зависимости на этапе загрузки модулей.
    """
    return SYSTEM_PROMPT