# -*- coding: utf-8 -*-
"""agent_analyst.py — ИИ-агент «Аналитик» (pyautocad, Блок 4 спецификации).

Данный агент занимается исключительно чтением данных, инспекцией чертежей и
сбором статистики. Он НЕ вносит изменения в геометрию или свойства объектов.

Работа выполняется СТРОГО через стабильную оболочку pyautocad:
    - acad.app   -> цикл по коллекции Documents (открытые вкладки);
    - acad.doc   -> Layers, Layouts, HandleToObject, Name, FullName;
    - acad.model -> итерация по объектам с чтением .ObjectName.

Основные возможности:
    1. Получение списка открытых чертежей, слоёв и листов (Layouts).
    2. Сводная статистика по типам объектов в Пространстве Модели.
    3. Чтение свойств объекта по его Handle через doc.HandleToObject().
    4. Выбор объектов и анализ пересечений отрезков (обратная совместимость).

Все аналитические инструменты оформлены как статичные Python-функции и
возвращают структурированный JSON-совместимый словарь вида
{"status": "success", "data": ...} либо {"status": "error", "message": ...}.

Ключевые принципы:
    - Внутри модуля НЕ используется exec()/eval() и НЕ используется сырой
      win32com.client — только статичные функции и методы pyautocad.
    - Все операции с коллекциями AutoCAD обёрнуты в try-except.

Модуль сохраняет обратную совместимость: функции, используемые agent_operator.py
(_filter_objects, read_document_property, inspect_object, find_objects,
count_by_layer, list_layers, list_layouts, list_open_documents), остаются
доступными с прежними сигнатурами.

Все комментарии и строки документации написаны на русском языке.
"""

from __future__ import annotations

from pyautocad import APoint
from core_core import ensure_connection, get_acad, line_intersection


# Человекочитаемые названия типов примитивов AutoCAD (из ObjectName COM).
# Сопоставляет внутренние имена Autodesk (AcDb...) с понятными названиями.
OBJECT_TYPE_NAMES = {
    "AcDbLine": "Line",
    "AcDbPolyline": "Polyline",
    "AcDbLWPolyline": "Polyline",
    "AcDbCircle": "Circle",
    "AcDbArc": "Arc",
    "AcDbText": "Text",
    "AcDbMText": "MText",
    "AcDbBlockReference": "Block Reference",
    "AcDbBlockInsert": "Block Insert",
    "AcDbDimension": "Dimension",
    "AcDbEllipse": "Ellipse",
    "AcDbSpline": "Spline",
    "AcDbHatch": "Hatch",
    "AcDbPoint": "Point",
    "AcDb3dSolid": "3D Solid",
}


# ========================================================================
# ВНУТРЕННИЕ УТИЛИТЫ ПОДКЛЮЧЕНИЯ И ФОРМАТИРОВАНИЯ
# ========================================================================

def _ensure_connection():
    """Гарантирует актуальное подключение к AutoCAD и возвращает объекты связи.

    Сначала проверяет «пульс» COM-сессии через core_core.ensure_connection()
    (с автоматическим восстановлением моста pyautocad при обрыве сессии
    -2147220995 'Объект не подключен к серверу'). При неудаче возвращает кортеж
    из None. Вызывается в начале каждой аналитической функции.

    Возвращает:
        tuple (app, doc, model) — объекты связи AutoCAD либо (None, None, None).
    """
    if not ensure_connection():
        return None, None, None
    acad = get_acad()
    return acad.app, acad.doc, acad.model


def _err(message):
    """Формирует словарь ошибки аналитической операции.

    Аргументы:
        message: текстовое описание ошибки на русском языке.

    Возвращает:
        dict — JSON-совместимый словарь со статусом «error».
    """
    return {"status": "error", "message": message}


def _ok(data, message=None):
    """Формирует словарь успешного результата аналитической операции.

    Аргументы:
        data: полезные данные результата (список, словарь, строка).
        message: необязательное текстовое описание результата.

    Возвращает:
        dict — JSON-совместимый словарь со статусом «success».
    """
    result = {"status": "success", "data": data}
    if message:
        result["message"] = message
    return result


def _find_document_by_name(app, drawing_name):
    """Находит документ среди открытых чертежей по его имени (или активный).

    Аргументы:
        app: объект Application AutoCAD.
        drawing_name: имя искомого чертежа (может содержать расширение .dwg)
            либо None для выбора активного документа.

    Возвращает:
        COM-объект документа либо None, если файл не найден.
    """
    if drawing_name is None:
        try:
            return app.ActiveDocument
        except Exception:
            return None
    target = str(drawing_name).strip().lower()
    try:
        docs = app.Documents
        for candidate in docs:
            candidate_name = str(candidate.Name).strip().lower()
            # Сравниваем как по полному имени с расширением, так и по базовому.
            if candidate_name == target or candidate_name.startswith(target):
                return candidate
    except Exception:
        pass
    return None


# ========================================================================
# АНАЛИТИЧЕСКИЕ ИНСТРУМЕНТЫ (JSON-ИНТЕРФЕЙС) — БЛОК 4 СПЕЦИФИКАЦИИ
# ========================================================================

def get_open_drawings():
    """Возвращает список имён всех открытых вкладок чертежей в сессии AutoCAD.

    Цикл перебора по коллекции документов acad.app.Documents (Блок 4
    спецификации). Активный документ помечается суффиксом «(активный)».

    Возвращает:
        dict — {"status": "success", "data": [имена файлов]} либо ошибку.
    """
    app, current_doc, _ = _ensure_connection()
    if app is None:
        return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
    try:
        active_name = str(current_doc.Name)
        names = []
        for d in app.Documents:
            label = str(d.Name)
            if str(d.Name) == active_name:
                label += " (активный)"
            names.append(label)
        return _ok(names, "Получен список открытых чертежей.")
    except Exception as e:
        return _err(f"Ошибка получения списка открытых чертежей: {e}")


def get_layers_list(drawing_name: str = None):
    """Возвращает список имён всех существующих слоёв.

    Цикл перебора коллекции слоёв acad.doc.Layers (Блок 4 спецификации). Если
    drawing_name передан, функция ищет этот документ среди открытых файлов и
    читает слои из него; иначе — из текущего активного документа.

    Аргументы:
        drawing_name: имя чертежа (необязательно), из которого нужно прочитать слои.

    Возвращает:
        dict — {"status": "success", "data": [имена слоёв]} либо ошибку.
    """
    app, _, _ = _ensure_connection()
    if app is None:
        return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
    try:
        target_doc = _find_document_by_name(app, drawing_name)
        if target_doc is None:
            return _err(f"Файл '{drawing_name}' не найден среди открытых вкладок")
        layers = [str(layer.Name) for layer in target_doc.Layers]
        return _ok(layers, "Получен список слоёв.")
    except Exception as e:
        return _err(f"Ошибка чтения списка слоёв: {e}")


def get_sheets_list(drawing_name: str = None):
    """Возвращает список всех листов (Layouts) в файле, исключая «Model».

    Цикл перебора коллекции листов acad.doc.Layouts (Блок 4 спецификации) с
    фильтром ly.Name != "Model". Если drawing_name передан, функция ищет документ
    среди открытых файлов; иначе — читает листы из активного документа.

    Аргументы:
        drawing_name: имя чертежа (необязательно), из которого нужно прочитать листы.

    Возвращает:
        dict — {"status": "success", "data": [имена листов]} либо ошибку.
    """
    app, _, _ = _ensure_connection()
    if app is None:
        return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
    try:
        target_doc = _find_document_by_name(app, drawing_name)
        if target_doc is None:
            return _err(f"Файл '{drawing_name}' не найден среди открытых вкладок")
        # Исключаем пространство модели, которое не является листом печати.
        sheets = [ly.Name for ly in target_doc.Layouts if ly.Name != "Model"]
        return _ok(sheets, "Получен список листов.")
    except Exception as e:
        return _err(f"Ошибка чтения списка листов: {e}")


def analyze_objects_summary():
    """Сканирует Пространство Модели и возвращает сводную статистику по объектам.

    Итерация по пространству модели acad.model с чтением свойства .ObjectName
    (Блок 4 спецификации). Подсчитывает, сколько и каких типов объектов находится
    на чертеже.

    Возвращает:
        dict — {"status": "success", "data": {тип: количество}} либо ошибку.
    """
    _, _, model = _ensure_connection()
    if model is None:
        return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
    try:
        summary = {}
        for obj in model:
            raw = str(getattr(obj, "ObjectName", "") or "").strip()
            if not raw:
                continue
            summary[raw] = summary.get(raw, 0) + 1
        return _ok(summary, "Сводная статистика по объектам собрана.")
    except Exception as e:
        return _err(f"Ошибка сканирования пространства модели: {e}")


def get_object_properties_by_handle(handle: str):
    """Читает свойства конкретного объекта по его Handle (Блок 4 спецификации).

    Находит объект через acad.doc.HandleToObject(handle) и собирает словарь его
    базовых свойств: тип, слой, цвет ACI, длина/периметр, площадь, координаты.

    Аргументы:
        handle: строковый Handle (паспорт) объекта в чертеже.

    Возвращает:
        dict — {"status": "success", "data": {...}} либо ошибку.
    """
    _, current_doc, _ = _ensure_connection()
    if current_doc is None:
        return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
    if not handle or not str(handle).strip():
        return _err("Не задан Handle объекта для чтения свойств.")
    try:
        target_obj = current_doc.HandleToObject(str(handle))
    except Exception:
        return _err(f"Объект с Handle '{handle}' не найден в чертеже.")

    props = {"handle": str(handle)}
    # Читаем базовые свойства (тип, слой, цвет ACI).
    for key, attr in (("object_name", "ObjectName"), ("layer", "Layer"),
                      ("color_aci", "Color")):
        try:
            props[key] = getattr(target_obj, attr)
        except Exception:
            pass
    # Читаем геометрические метрики, если они поддерживаются.
    for key, attr in (("length", "Length"), ("area", "Area")):
        try:
            props[key] = float(getattr(target_obj, attr))
        except Exception:
            pass
    # Читаем координаты вершин (полилинии/мультиточки), если доступны.
    try:
        coords = list(target_obj.Coordinates)
        props["coordinates"] = [float(c) for c in coords[:8]]
    except Exception:
        pass
    return _ok(props, f"Свойства объекта '{handle}' прочитаны.")


def get_intersection_points(handle1: str, handle2: str):
    """Находит истинные CAD-координаты точек пересечения двух объектов.

    Получает оба объекта через acad.doc.HandleToObject() и вызывает официальный
    метод AutoCAD ActiveX obj1.IntersectWith(obj2, 0) (см. справочник
    cad_reference.md). Метод возвращает плоский кортеж координат
    (X1, Y1, Z1, X2, Y2, Z2, ...). Парсер разбивает его по 3 элемента и
    упаковывает каждую точку в нативный APoint.

    Аргументы:
        handle1: строковый Handle первого объекта (например, квадрата).
        handle2: строковый Handle второго объекта (например, круга).

    Возвращает:
        dict — {"status": "success", "data": [[x, y], ...]} с массивом истинных
               CAD-координат точек пересечения, либо ошибку.
    """
    if not _ensure_connection():
        return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
    if not handle1 or not handle2:
        return _err("Для поиска пересечений необходимы Handles двух объектов.")
    try:
        # Получаем оба объекта как полноценные CAD-сущности.
        try:
            obj1 = get_acad().doc.HandleToObject(str(handle1))
        except Exception:
            return _err(f"Объект с Handle '{handle1}' не найден в чертеже.")
        try:
            obj2 = get_acad().doc.HandleToObject(str(handle2))
        except Exception:
            return _err(f"Объект с Handle '{handle2}' не найден в чертеже.")

        # Официальный метод ActiveX: режим 0 — без продления граней.
        # Возвращает плоский кортеж (X1, Y1, Z1, X2, Y2, Z2, ...).
        raw = obj1.IntersectWith(obj2, 0)

        # Парсер: разбиваем кортеж по 3 координаты и упаковываем в APoint.
        points = []
        coords = list(raw) if raw is not None else []
        for i in range(0, len(coords), 3):
            x = float(coords[i])
            y = float(coords[i + 1]) if i + 1 < len(coords) else 0.0
            pt = APoint(x, y, 0.0)
            points.append([pt.x, pt.y])

        # Очистка от дубликатов: сложные фигуры могут возвращать накладывающиеся
        # точки. Округляем координаты до 3 знаков после запятой и оставляем только
        # уникальные пары (X, Y), чтобы в ответ и круги-маркеры не дублировались.
        unique_points = []
        seen = set()
        for (px, py) in points:
            key = (round(float(px), 3), round(float(py), 3))
            if key not in seen:
                seen.add(key)
                unique_points.append([key[0], key[1]])

        return _ok(unique_points,
                   f"Найдено точек пересечения: {len(unique_points)}.")
    except Exception as e:
        return _err(f"Ошибка поиска точек пересечения: {e}")


def get_selected_or_all_objects():
    """Возвращает Handle выбранных объектов либо Handle всех объектов модели.

    Интегрирует логику из старого cad_tools.py: сначала проверяет, выбрал ли
    пользователь объекты на экране вручную (коллекция PickfirstSelectionSet).
    Если выбор есть — возвращает список их Handle; если выбора нет — возвращает
    Handle всех объектов в Пространстве Модели.

    Возвращает:
        dict — {"status": "success", "data": [handle, ...]} либо ошибку.
    """
    _, current_doc, model = _ensure_connection()
    if current_doc is None or model is None:
        return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
    try:
        handles = []
        # Пытаемся взять объекты, выделенные пользователем вручную.
        try:
            ss = current_doc.PickfirstSelectionSet
            if ss.Count > 0:
                for i in range(ss.Count):
                    handles.append(str(ss.Item(i).Handle))
                return _ok(handles, "Возвращены Handle выделенных объектов.")
        except Exception:
            pass
        # Резервный вариант: все объекты Пространства Модели.
        for obj in model:
            try:
                handles.append(str(obj.Handle))
            except Exception:
                continue
        return _ok(handles, "Возвращены Handle всех объектов модели.")
    except Exception as e:
        return _err(f"Ошибка получения Handle объектов: {e}")


def check_lines_intersections():
    """Находит все точки пересечения отрезков AcDbLine в Пространстве Модели.

    Перебирает все отрезки в acad.model и попарно сравнивает их с помощью
    line_intersection из core_core. Каждая найденная точка пересечения
    добавляется в список координат.

    Возвращает:
        dict — {"status": "success", "data": [[x, y], ...]} либо ошибку.
    """
    _, _, model = _ensure_connection()
    if model is None:
        return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
    try:
        # Собираем все отрезки AcDbLine с их конечными точками.
        lines = []
        for obj in model:
            try:
                if str(getattr(obj, "ObjectName", "")) != "AcDbLine":
                    continue
                start_pt = obj.StartPoint
                end_pt = obj.EndPoint
                lines.append(((float(start_pt[0]), float(start_pt[1])),
                              (float(end_pt[0]), float(end_pt[1]))))
            except Exception:
                continue

        # Попарно ищем пересечения между всеми отрезками.
        intersections = []
        for i in range(len(lines)):
            for j in range(i + 1, len(lines)):
                pt = line_intersection(lines[i], lines[j])
                if pt is not None:
                    # Округляем координаты до двух знаков для компактности.
                    x, y = round(float(pt[0]), 2), round(float(pt[1]), 2)
                    if [x, y] not in intersections:
                        intersections.append([x, y])

        return _ok(intersections, f"Найдено точек пересечения: {len(intersections)}.")
    except Exception as e:
        return _err(f"Ошибка анализа пересечений отрезков: {e}")


# ========================================================================
# ОБРАТНАЯ СОВМЕСТИМОСТЬ С agent_operator.py (прежние сигнатуры)
# ========================================================================
# Эти функции используются диспетчером agent_operator.execute_json_command().
# Они сохранены с прежними сигнатурами, чтобы не ломать существующую интеграцию.

def get_selected_object(acad):
    """Безопасно извлекает первый выделенный пользователем объект на чертеже.

    Аргументы:
        acad: объект-коннектор AutoCAD (с атрибутом doc).

    Возвращает:
        COM-объект примитива либо None, если выделение пусто.
    """
    try:
        ss = acad.doc.PickfirstSelectionSet
        if ss.Count > 0:
            return ss.Item(0)
    except Exception:
        pass
    return None


def _collect_object_types(active_doc):
    """Собирает уникальные типы объектов из пространства модели (ModelSpace).

    Аргументы:
        active_doc: активный документ AutoCAD.

    Возвращает:
        str — строка со статистикой либо сообщение об ошибке/отсутствии объектов.
    """
    types = {}
    try:
        for obj in active_doc.ModelSpace:
            raw = str(getattr(obj, "ObjectName", "") or "").strip()
            if not raw:
                continue
            key = OBJECT_TYPE_NAMES.get(raw, raw)
            types[key] = types.get(key, 0) + 1
    except Exception:
        return "Не удалось получить список типов объектов в чертеже."
    if not types:
        return "В чертеже не найдено объектов."
    parts = [f"{name} ({cnt})" for name, cnt in sorted(types.items(), key=lambda x: -x[1])]
    return "Типы объектов в чертеже: " + ", ".join(parts) + "."


def read_document_property(active_doc, property_name):
    """ЖЁСТКОЕ ЧТЕНИЕ СВОЙСТВА ДОКУМЕНТА ЧЕРЕЗ COM (только чтение).

    Аргументы:
        active_doc: активный документ AutoCAD.
        property_name: строковое имя/синоним запрашиваемого свойства.

    Возвращает:
        str — результат чтения свойства либо понятное сообщение об ошибке.
    """
    prop = str(property_name or "").strip().lower()
    try:
        if prop in ("document_name", "имя", "name", "имя чертежа", "имя документа"):
            return f"Имя текущего чертежа: {active_doc.Name}"
        if prop in ("document_path", "path", "полный путь", "путь", "путь к чертежу"):
            return f"Путь к чертежу: {active_doc.FullName}"
        if prop in ("object_count", "count", "количество объектов", "кол-во", "число объектов"):
            return f"Количество объектов в модели: {active_doc.ModelSpace.Count}"
        if prop in ("object_types", "types", "типы объектов", "виды объектов", "список объектов",
                    "какие объекты", "что начерчено", "типы примитивов"):
            return _collect_object_types(active_doc)
        if prop in ("layout_name", "layout", "активный лист", "текущий лист", "лист"):
            return f"Активный лист: {active_doc.ActiveLayout.Name}"
        return (f"Неизвестное свойство документа: '{property_name}'. "
                f"Доступно: document_name, document_path, object_count, object_types, layout_name")
    except Exception as e:
        return f"Ошибка чтения свойства '{property_name}' через COM: {str(e)}"


def inspect_object(target_obj, unsupported_prefix="[НЕ ПОДДЕРЖИВАЕТСЯ]"):
    """Читает свойства выделенного объекта: тип, слой, цвет, геометрию, длины.

    Аргументы:
        target_obj: COM-объект примитива для инспекции (или None).
        unsupported_prefix: маркер, добавляемый при невозможности выполнить операцию.

    Возвращает:
        str — многострочная информация об объекте либо сообщение об ошибке.
    """
    if not target_obj:
        return unsupported_prefix + " Не выделен объект для инспекции. Выделите примитив и повторите запрос."
    info = []
    for label, fn in (("Тип объекта", lambda o: o.ObjectName),
                      ("Слой", lambda o: o.Layer),
                      ("Цвет (ACI)", lambda o: o.Color)):
        try:
            info.append(f"{label}: {fn(target_obj)}")
        except Exception:
            pass
    for label, attr in (("Длина/Периметр", "Length"), ("Площадь", "Area")):
        try:
            info.append(f"{label}: {float(getattr(target_obj, attr)):.2f}")
        except Exception:
            pass
    try:
        info.append(f"Замкнутый: {target_obj.Closed}")
    except Exception:
        pass
    try:
        coords = list(target_obj.Coordinates)
        shown = ", ".join(f"{c:.2f}" for c in coords[:8])
        info.append(f"Координаты: [{shown}{'...' if len(coords) > 8 else ''}]")
    except Exception:
        pass
    if info:
        return "Информация об объекте:\n" + "\n".join(info)
    return unsupported_prefix + " Не удалось прочитать свойства объекта."


def _filter_objects(model_space, obj_type=None, layer=None):
    """Фильтрует объекты модели по типу примитива и/или слою.

    Аргументы:
        model_space: коллекция ModelSpace текущего чертежа.
        obj_type: тип примитива (человекочитаемый либо COM-имя AcDb...).
        layer: имя слоя, на котором должны находиться объекты.

    Возвращает:
        list — список COM-объектов, удовлетворяющих условиям фильтра.
    """
    target_types = set()
    if obj_type:
        low = str(obj_type).strip().lower()
        for k, v in OBJECT_TYPE_NAMES.items():
            if low in k.lower() or low in v.lower():
                target_types.add(k)
        if not target_types:
            target_types.add(str(obj_type))
    found = []
    for obj in model_space:
        ok = True
        if target_types:
            raw = str(getattr(obj, "ObjectName", "") or "")
            if raw not in target_types:
                ok = False
        if layer and str(getattr(obj, "Layer", "") or "") != str(layer):
            ok = False
        if ok:
            found.append(obj)
    return found


def find_objects(model_space, obj_type=None, layer=None):
    """Ищет объекты по типу примитива и/или слою. Возвращает количество найденного.

    Аргументы:
        model_space: коллекция ModelSpace текущего чертежа.
        obj_type: тип примитива для поиска.
        layer: имя слоя для поиска.

    Возвращает:
        str — сообщение о количестве найденных объектов.
    """
    found = _filter_objects(model_space, obj_type, layer)
    desc = ""
    if obj_type:
        desc += f" типа '{obj_type}'"
    if layer:
        desc += f" на слое '{layer}'"
    if not found:
        return f"Объекты{desc} не найдены."
    return f"Найдено объектов{desc}: {len(found)}."


def count_by_layer(model_space):
    """Подсчитывает количество объектов по каждому слою.

    Аргументы:
        model_space: коллекция ModelSpace текущего чертежа.

    Возвращает:
        str — строка со статистикой объектов по слоям (сортировка по убыванию).
    """
    counts = {}
    for obj in model_space:
        try:
            lay = str(getattr(obj, "Layer", "") or "")
            counts[lay] = counts.get(lay, 0) + 1
        except Exception:
            pass
    if not counts:
        return "В чертеже нет объектов."
    rows = [f"  {k}: {v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])]
    return "Количество объектов по слоям:\n" + "\n".join(rows)


def list_layers(active_doc):
    """Возвращает список всех слоёв чертежа с их цветами.

    Аргументы:
        active_doc: активный документ AutoCAD.

    Возвращает:
        str — перечень слоёв и их цветов ACI.
    """
    rows = []
    for layer in active_doc.Layers:
        try:
            color = layer.Color
        except Exception:
            color = "?"
        rows.append(f"  {layer.Name} (цвет {color})")
    if not rows:
        return "В чертеже нет слоёв."
    return "Слои чертежа:\n" + "\n".join(rows)


def list_layouts(active_doc):
    """Возвращает список всех листов/вкладок чертежа.

    Аргументы:
        active_doc: активный документ AutoCAD.

    Возвращает:
        str — перечень имён листов (вкладок) чертежа.
    """
    rows = []
    for layout in active_doc.Layouts:
        rows.append(f"  {layout.Name}")
    if not rows:
        return "В чертеже нет листов."
    return "Листы чертежа:\n" + "\n".join(rows)


def list_open_documents(acad_app):
    """Перечисляет все открытые чертежи в AutoCAD (активный помечается).

    Аргументы:
        acad_app: объект Application AutoCAD.

    Возвращает:
        str — перечень открытых документов, активный помечен как «(активный)».
    """
    try:
        docs = acad_app.Documents
        active_name = str(acad_app.ActiveDocument.Name)
        rows = []
        for d in docs:
            mark = " (активный)" if str(d.Name) == active_name else ""
            rows.append(f"  {d.Name}{mark}")
        if not rows:
            return "В AutoCAD нет открытых чертежей."
        return "Открытые чертежи:\n" + "\n".join(rows)
    except Exception as e:
        return f"Ошибка получения списка открытых чертежей: {str(e)}"


def check_self_intersections(acad, model_space, APoint, target_obj, obj_points):
    """Вычисляет самопересечения полилинии и ставит отметки-круги.

    Аргументы:
        acad: объект-коннектор AutoCAD (с атрибутом doc).
        model_space: коллекция ModelSpace текущего чертежа.
        APoint: класс точки pyautocad (APoint) для создания кругов.
        target_obj: COM-объект полилинии.
        obj_points: список вершин полилинии [(x, y), ...].

    Возвращает:
        None; результат анализа выводится в консоль.
    """
    if not target_obj or len(obj_points) < 4:
        print("⚠️ Полилиния не выбрана или имеет слишком мало вершин!")
        return

    segments = [(obj_points[i], obj_points[i + 1]) for i in range(len(obj_points) - 1)]
    try:
        if target_obj.Closed:
            segments.append((obj_points[-1], obj_points[0]))
    except Exception:
        pass

    intersect_count = 0
    for i in range(len(segments)):
        for j in range(i + 2, len(segments)):
            if i == 0 and j == len(segments) - 1:
                continue
            pt = line_intersection(segments[i], segments[j])
            if pt:
                model_space.AddCircle(APoint(pt[0], pt[1]), 5.0)
                intersect_count += 1

    acad.doc.Regen(1)
    print(f"📊 Анализ топологии завершен! Найдено и отмечено кругами точек "
          f"самопересечения: {intersect_count}")