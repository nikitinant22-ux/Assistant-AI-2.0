# -*- coding: utf-8 -*-
"""agent_operator.py — ИИ-агент «Оператор» (Предприниматель) для управления средой.

Данный модуль отвечает за изменение рабочей среды AutoCAD:
    1. Навигация: переключение на Пространство Модели, на конкретный Лист
       (Layout) или на другую открытую вкладку чертежа.
    2. Управление свойствами объектов и слоёв: изменение цвета объекта по его
       Handle и цвета слоя по его имени, с поддержкой текстовых названий цветов
       на русском языке.
    3. Прочие операторские действия (зум, создание слоёв, модификация объектов)
       — сохранены для обратной совместимости с диспетчером JSON-паспортов.

Ключевые принципы:
    - Функции принимают только базовые типы (int, float, list, str) и возвращают
      JSON-совместимый словарь ({"status": "success"/"error", ...}).
    - Внутри модуля НЕ используется exec()/eval() — только статичные функции.
    - Для работы с COM используются объекты связи из core_core, включая
      update_screen() для мгновенного обновления чертежа.
    - Цвета: для текстовых названий используется справочник имён -> ACI.

Все комментарии и строки документации написаны на русском языке.
"""

from __future__ import annotations

import math  # тригонометрические функции для поворота объектов

import cad_colors_data  # справочник соответствия ACI-цветов значениям RGB
from core_core import (get_autocad_connection, acad_app, doc, model_space,
                       object_center, to_cad_point, update_screen)


# ========================================================================
# СПРАВОЧНИК НАЗВАНИЙ ЦВЕТОВ -> ACI (для текстового ввода на русском языке)
# ========================================================================
# Дополняет cad_colors_data.aci_map обратным словарём: человекочитаемое
# название цвета (русское/английское) -> числовой индекс AutoCAD (ACI).
COLOR_NAME_TO_ACI = {
    # Основные цвета
    "красный": 1, "red": 1,
    "жёлтый": 2, "yellow": 2,
    "зелёный": 3, "зеленый": 3, "green": 3,
    "голубой": 4, "cyan": 4,
    "синий": 5, "blue": 5,
    "фиолетовый": 6, "magenta": 6, "пурпурный": 6,
    "белый": 7, "white": 7,
    "серый": 8, "gray": 8, "grey": 8,
    "светло-серый": 9, "light gray": 9,
    # Частые пользовательские варианты
    "оранжевый": 30, "orange": 30,
    "чёрный": 250, "black": 250,
    "тёмно-синий": 170, "dark blue": 170,
    "тёмно-красный": 12, "dark red": 12,
    "светло-зелёный": 90, "light green": 90,
    "розовый": 210, "pink": 210,
}


def _resolve_aci(color_input):
    """Преобразует ввод пользователя (имя или число) в индекс цвета ACI.

    Если color_input — строка, ищет её в справочнике COLOR_NAME_TO_ACI (регистр
    не важен). Если найти не удалось — пробует привести строку к целому числу.
    Если color_input — число, возвращает его как есть. При неудаче возвращает None.

    Аргументы:
        color_input: значение цвета — текст («красный») или число (индекс ACI).

    Возвращает:
        int | None — индекс ACI либо None, если цвет не распознан.
    """
    if isinstance(color_input, bool):
        return int(color_input)
    if isinstance(color_input, int):
        return int(color_input)
    if isinstance(color_input, float):
        return int(color_input)
    # Строковый ввод: ищем по названию, затем пробуем парсинг как число.
    text = str(color_input).strip().lower()
    if text in COLOR_NAME_TO_ACI:
        return COLOR_NAME_TO_ACI[text]
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _err(message):
    """Формирует словарь ошибки операторской операции.

    Аргументы:
        message: текстовое описание ошибки на русском языке.

    Возвращает:
        dict — JSON-совместимый словарь со статусом «error».
    """
    return {"status": "error", "message": message}


def _ok(message, **extra):
    """Формирует словарь успешного результата операторской операции.

    Аргументы:
        message: текстовое описание результата на русском языке.
        extra: дополнительные поля для включения в результат (например, aci).

    Возвращает:
        dict — JSON-совместимый словарь со статусом «success».
    """
    result = {"status": "success", "message": message}
    result.update(extra)
    return result


def _ensure_connection():
    """Гарантирует актуальное подключение к AutoCAD и возвращает объекты связи.

    Вызывает get_autocad_connection() из core_core, который заполняет глобальные
    переменные acad_app, doc и model_space.

    Возвращает:
        tuple (acad_app, doc, model_space) — объекты COM-связи AutoCAD.
    """
    global acad_app, doc, model_space
    try:
        app, current_doc, ms = get_autocad_connection()
        acad_app = app
        doc = current_doc
        model_space = ms
    except Exception:
        pass
    return acad_app, doc, model_space


# ========================================================================
# ОПЕРАТОРСКИЕ ИНСТРУМЕНТЫ НАВИГАЦИИ (JSON-ИНТЕРФЕЙС)
# ========================================================================

def switch_to_model():
    """Переключает текущее графическое окно AutoCAD на Пространство Модели.

    Устанавливает активный лист на «Model» через коллекцию Layouts документа.
    После переключения экран принудительно обновляется.

    Возвращает:
        dict — {"status": "success", "message": "..."} либо ошибку.
    """
    app, current_doc, _ = _ensure_connection()
    if current_doc is None:
        return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
    try:
        # Устанавливаем активный лист на пространство модели («Model»).
        current_doc.ActiveLayout = current_doc.Layouts.Item("Model")
        update_screen()
        return _ok("Переключено на Пространство Модели.")
    except Exception as e:
        return _err(f"Ошибка переключения на пространство модели: {e}")


def switch_to_sheet(sheet_name: str):
    """Переключает экран на конкретный Лист (Layout) по его имени.

    Предварительно проверяет, существует ли такой лист в коллекции doc.Layouts
    (регистр символов не важен). После переключения экран обновляется.

    Аргументы:
        sheet_name: имя листа/вкладки для переключения.

    Возвращает:
        dict — {"status": "success", "message": "..."} либо ошибку.
    """
    app, current_doc, _ = _ensure_connection()
    if current_doc is None:
        return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
    if not sheet_name or not str(sheet_name).strip():
        return _err("Не задано имя листа для переключения.")
    target = str(sheet_name).strip().lower()
    try:
        # Ищем лист в коллекции Layouts (без учёта регистра).
        for layout in current_doc.Layouts:
            if str(layout.Name).strip().lower() == target:
                current_doc.ActiveLayout = layout
                update_screen()
                return _ok(f"Переключено на лист '{layout.Name}'.")
        return _err(f"Лист '{sheet_name}' не существует на этом чертеже")
    except Exception as e:
        return _err(f"Ошибка переключения на лист: {e}")


def switch_to_drawing(drawing_name: str):
    """Активирует другую открытую вкладку чертежа по её имени.

    Переносит фокус приложения на документ с указанным именем (например,
    «чертеж2.dwg») через коллекцию acad_app.Documents.

    Аргументы:
        drawing_name: имя открытого чертежа для активации.

    Возвращает:
        dict — {"status": "success", "message": "..."} либо ошибку.
    """
    app, _, _ = _ensure_connection()
    if app is None:
        return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
    if not drawing_name or not str(drawing_name).strip():
        return _err("Не задано имя чертежа для активации.")
    target = str(drawing_name).strip().lower()
    try:
        docs = app.Documents
        for i in range(docs.Count):
            candidate = docs.Item(i)
            candidate_name = str(candidate.Name).strip().lower()
            # Сравниваем по полному имени и по базовому имени (без расширения).
            if candidate_name == target or candidate_name.startswith(target):
                app.ActiveDocument = candidate
                update_screen()
                return _ok(f"Активирован чертёж '{candidate.Name}'.")
        return _err(f"Чертёж '{drawing_name}' не найден среди открытых вкладок")
    except Exception as e:
        return _err(f"Ошибка активации чертежа: {e}")


# ========================================================================
# ОПЕРАТОРСКИЕ ИНСТРУМЕНТЫ УПРАВЛЕНИЯ ЦВЕТОМ (JSON-ИНТЕРФЕЙС)
# ========================================================================

def change_object_color(object_handle: str, color_input):
    """Изменяет цвет объекта в чертеже по его Handle.

    Находит объект через doc.HandleToObject(). Если color_input — строка
    (например, «красный», «синий»), она автоматически преобразуется в индекс
    ACI через словарь COLOR_NAME_TO_ACI. Если color_input — число, присваивается
    индекс напрямую. После изменения экран обновляется.

    Аргументы:
        object_handle: строковый Handle существующего объекта в чертеже.
        color_input: цвет — текст («красный») или число (индекс ACI).

    Возвращает:
        dict — {"status": "success", "message": "...", "aci": N} либо ошибку.
    """
    _, current_doc, _ = _ensure_connection()
    if current_doc is None:
        return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
    if not object_handle or not str(object_handle).strip():
        return _err("Не задан Handle объекта для изменения цвета.")
    aci = _resolve_aci(color_input)
    if aci is None:
        return _err(f"Не удалось распознать цвет '{color_input}'. "
                    f"Укажите название (например, «красный») или индекс ACI (1-255).")
    try:
        # Находим объект в базе чертежа по его уникальному дескриптору.
        try:
            target_obj = current_doc.HandleToObject(str(object_handle))
        except Exception:
            return _err(f"Объект с Handle '{object_handle}' не найден в чертеже.")
        target_obj.Color = aci
        target_obj.Update()
        update_screen()
        return _ok(f"Цвет объекта '{object_handle}' изменён на ACI {aci}.", aci=aci)
    except Exception as e:
        return _err(f"Ошибка изменения цвета объекта: {e}")


def change_layer_color(layer_name: str, color_input):
    """Изменяет цвет всего слоя по его имени.

    Находит слой в коллекции doc.Layers. Если color_input — строка, она
    преобразуется в индекс ACI через словарь COLOR_NAME_TO_ACI; если число —
    присваивается напрямую. После изменения экран обновляется.

    Аргументы:
        layer_name: имя слоя, цвет которого нужно изменить.
        color_input: цвет — текст («красный») или число (индекс ACI).

    Возвращает:
        dict — {"status": "success", "message": "...", "aci": N} либо ошибку.
    """
    _, current_doc, _ = _ensure_connection()
    if current_doc is None:
        return _err("Не удалось подключиться к AutoCAD. Проверьте, что программа запущена.")
    if not layer_name or not str(layer_name).strip():
        return _err("Не задано имя слоя для изменения цвета.")
    aci = _resolve_aci(color_input)
    if aci is None:
        return _err(f"Не удалось распознать цвет '{color_input}'. "
                    f"Укажите название (например, «красный») или индекс ACI (1-255).")
    try:
        # Ищем слой в коллекции Layers (без учёта регистра).
        layer_obj = None
        for layer in current_doc.Layers:
            if str(layer.Name).strip().lower() == str(layer_name).strip().lower():
                layer_obj = layer
                break
        if layer_obj is None:
            return _err(f"Слой '{layer_name}' не существует на этом чертеже")
        layer_obj.Color = aci
        update_screen()
        return _ok(f"Цвет слоя '{layer_name}' изменён на ACI {aci}.", aci=aci)
    except Exception as e:
        return _err(f"Ошибка изменения цвета слоя: {e}")


# ========================================================================
# ОБРАТНАЯ СОВМЕСТИМОСТЬ: навигация экрана, слои, геометрия (прежние сигнатуры)
# ========================================================================
# Эти функции используются диспетчером execute_json_command() и сохранены
# с прежними сигнатурами, чтобы не ломать интеграцию с main_router.py.

def zoom_to_object(acad, target_obj):
    """Железобетонный фокус экрана на конкретном объекте через командную строку.

    Аргументы:
        acad: объект-коннектор AutoCAD (с атрибутом doc).
        target_obj: COM-объект примитива для фокусировки.

    Возвращает:
        str — сообщение об успехе или ошибке.
    """
    if not target_obj:
        return "Ошибка: Объект не задан"
    try:
        acad.doc.SendCommand('_ZOOM _Object ')
        acad.doc.SendCommand(f'(handent "{target_obj.Handle}")  ')
        return f"Экран успешно сфокусирован на объекте {target_obj.ObjectName}"
    except Exception as e:
        return f"Ошибка зуммирования: {str(e)}"


def zoom_all(acad_app):
    """Показывает весь чертёж целиком, умещая его в текущем видовом экране.

    Аргументы:
        acad_app: объект Application AutoCAD.

    Возвращает:
        str — сообщение об успешном выполнении.
    """
    try:
        acad_app.ZoomExtents()
        return "Сценарий 'zoom_all' успешно выполнен: показан весь чертеж."
    except Exception as e:
        return f"Ошибка масштабирования к границам чертежа: {str(e)}"


def create_layer_aci(acad, layer_name, aci_color):
    """Создаёт или обновляет слой, жёстко задавая стандартный индекс цвета ACI.

    Аргументы:
        acad: объект-коннектор AutoCAD (с атрибутом doc).
        layer_name: имя создаваемого слоя.
        aci_color: индекс стандартного цвета ACI (целое число от 1 до 255).

    Возвращает:
        str — сообщение об успехе или ошибке.
    """
    try:
        layer_obj = acad.doc.Layers.Add(str(layer_name))
        layer_obj.Color = int(aci_color)
        return f"Слой '{layer_name}' успешно создан с ACI цветом {aci_color}"
    except Exception as e:
        return f"Ошибка создания ACI слоя: {str(e)}"


def set_all_layers_to_rgb(acad, acad_app):
    """Массово переводит все слои чертежа в RGB (TrueColor).

    Аргументы:
        acad: объект-коннектор AutoCAD (с атрибутом doc).
        acad_app: объект Application AutoCAD (для создания цветовых объектов).

    Возвращает:
        str — количество переведённых в RGB слоёв либо сообщение об ошибке.
    """
    try:
        prog_id = f'AutoCAD.AcCmColor.{acad_app.Version[:2]}'
        count = 0
        for layer in acad.doc.Layers:
            if layer.Name.upper() == 'DEFPOINTS':
                continue
            try:
                aci = layer.Color
                r, g, b = cad_colors_data.aci_map.get(aci, (255, 255, 255))
                color_obj = acad_app.GetInterfaceObject(prog_id)
                color_obj.ColorMethod = 2
                color_obj.SetRGB(r, g, b)
                layer.TrueColor = color_obj
                count += 1
            except Exception:
                pass
        acad.doc.Regen(1)
        return f"Успешно переведено в RGB режим слоев: {count}"
    except Exception as e:
        return f"Ошибка массового перевода в RGB: {str(e)}"


def set_all_layers_to_aci(acad):
    """Массово сбрасывает все слои чертежа обратно из RGB в индексацию ACI.

    Аргументы:
        acad: объект-коннектор AutoCAD (с атрибутом doc).

    Возвращает:
        str — количество возвращённых в режим ACI слоёв либо сообщение об ошибке.
    """
    try:
        count = 0
        for layer in acad.doc.Layers:
            if layer.Name.upper() == 'DEFPOINTS':
                continue
            try:
                clr = layer.Color
                clr.ColorMethod = 1
                layer.Color = clr
                count += 1
            except Exception:
                pass
        acad.doc.Regen(1)
        return f"Успешно возвращено в режим ACI слоев: {count}"
    except Exception as e:
        return f"Ошибка обратного перевода в ACI: {str(e)}"


def modify_object(acad, target_obj, action, params, unsupported_prefix="[НЕ ПОДДЕРЖИВАЕТСЯ]"):
    """Изменяет свойства/геометрию выделенного объекта.

    Действия (action): move, rotate, scale, set_layer, set_color, hide, show,
    delete. Координаты перемещения упаковываются в COM-массив через to_cad_point.

    Аргументы:
        acad: объект-коннектор AutoCAD (с атрибутом doc).
        target_obj: COM-объект примитива для изменения (или None).
        action: строка с названием действия.
        params: словарь параметров (dx, dy, angle, factor, layer, color и т.п.).
        unsupported_prefix: маркер при невозможности выполнить операцию.

    Возвращает:
        str — сообщение о результате операции либо понятную ошибку.
    """
    if not target_obj:
        return unsupported_prefix + " Не выделен объект для изменения. Выделите примитив и повторите запрос."
    action = str(action or "").strip().lower()
    try:
        if action in ("set_layer", "layer", "перенести на слой", "сменить слой", "перевести на слой"):
            name = params.get("layer") or params.get("name") or ""
            if not name:
                return "Ошибка: для смены слоя укажите параметр 'layer'."
            target_obj.Layer = name
            target_obj.Update()
            return f"Объект перенесён на слой '{name}'."
        if action in ("set_color", "color", "цвет", "изменить цвет"):
            aci = int(params.get("color") or params.get("color_aci") or 7)
            target_obj.Color = aci
            target_obj.Update()
            return f"Цвет объекта изменён на ACI {aci}."
        if action in ("hide", "скрыть", "спрятать"):
            target_obj.Visible = False
            target_obj.Update()
            return "Объект скрыт."
        if action in ("show", "show_visible", "показать", "вернуть видимость"):
            target_obj.Visible = True
            target_obj.Update()
            return "Объект снова видим."
        if action in ("delete", "erase", "удалить", "стереть"):
            target_obj.Delete()
            acad.doc.Regen(1)
            return "Объект удалён."
        if action in ("move", "переместить", "передвинуть", "сдвинуть"):
            dx = float(params.get("dx") or params.get("deltax") or 0)
            dy = float(params.get("dy") or params.get("deltay") or 0)
            cx, cy, cz = object_center(target_obj)
            target_obj.Move(to_cad_point(cx, cy, cz), to_cad_point(cx + dx, cy + dy, cz))
            target_obj.Update()
            return f"Объект перемещён на Δx={dx}, Δy={dy}."
        if action in ("rotate", "повернуть", "развернуть", "поворот"):
            angle_deg = float(params.get("angle") or params.get("degrees") or 0)
            cx, cy, cz = object_center(target_obj)
            target_obj.Rotate(to_cad_point(cx, cy, cz), math.radians(angle_deg))
            target_obj.Update()
            return f"Объект повёрнут на {angle_deg}°."
        if action in ("scale", "масштаб", "масштабировать", "увеличить"):
            factor = float(params.get("factor") or params.get("scale") or 1)
            cx, cy, cz = object_center(target_obj)
            target_obj.ScaleEntity(to_cad_point(cx, cy, cz), factor)
            target_obj.Update()
            return f"Объект масштабирован в {factor} раз."
        return (f"Ошибка: неизвестное действие '{action}' для modify_object. "
                f"Доступно: move, rotate, scale, set_layer, set_color, hide, show, delete.")
    except Exception as e:
        return f"Ошибка изменения объекта: {str(e)}"


def select_by_type(acad, model_space, obj_type=None, layer=None, win32com_client=None, pythoncom_mod=None):
    """Выделяет в AutoCAD объекты, отфильтрованные по типу и/или слою.

    Аргументы:
        acad: объект-коннектор AutoCAD (с атрибутом doc).
        model_space: коллекция ModelSpace текущего чертежа.
        obj_type: тип примитива для фильтрации.
        layer: имя слоя для фильтрации.
        win32com_client: модуль win32com.client (ленивый импорт при необходимости).
        pythoncom_mod: модуль pythoncom (ленивый импорт при необходимости).

    Возвращает:
        str — количество выделенных объектов либо сообщение об ошибке.
    """
    from agent_analyst import _filter_objects
    found = _filter_objects(model_space, obj_type, layer)
    if not found:
        return "Не найдено объектов для выделения."
    try:
        ssets = acad.doc.SelectionSets
        for i in range(ssets.Count - 1, -1, -1):
            try:
                if ssets.Item(i).Name.startswith("AI_SELECT"):
                    ssets.Item(i).Delete()
            except Exception:
                pass
        # Ленивый импорт COM-модулей для упаковки набора в VARIANT.
        if win32com_client is None:
            import win32com.client as win32com_client
        if pythoncom_mod is None:
            import pythoncom as pythoncom_mod
        ss = ssets.Add("AI_SELECT")
        data = win32com_client.VARIANT(pythoncom_mod.VT_ARRAY | pythoncom_mod.VT_DISPATCH, found)
        ss.AddItems(data)
        acad.doc.Regen(1)
        return f"Выделено и подсвечено объектов: {len(found)}."
    except Exception as e:
        return f"Ошибка выделения объектов: {str(e)}"


def switch_to_layout(active_doc, layout_name):
    """Безошибочное переключение листов/вкладок чертежа (прежняя сигнатура).

    Аргументы:
        active_doc: активный документ AutoCAD.
        layout_name: имя листа/вкладки либо синоним "модель".

    Возвращает:
        str — сообщение об успехе или ошибке.
    """
    try:
        target_clean = str(layout_name).strip().lower()
        if not target_clean:
            return "Ошибка: Имя листа не задано"

        # Корректируем контекстные синонимы пространства модели.
        if target_clean in ["модель", "model", "пространство модели"]:
            active_doc.ActiveLayout = active_doc.Layouts.Item("Model")
            return "Успешно переключено на пространство Модели"

        # Перебираем все существующие листы в текущем DWG.
        for layout in active_doc.Layouts:
            if layout.Name.strip().lower() == target_clean:
                active_doc.ActiveLayout = layout
                return f"Успешно переключено на лист '{layout.Name}'"

        return f"Ошибка: Лист с именем '{layout_name}' не найден в текущем чертеже"
    except Exception as e:
        return f"Внутренний сбой ActiveX при переключении листа: {str(e)}"


def undo_last(acad):
    """Отменяет последнее действие в чертеже через команду UNDO.

    Аргументы:
        acad: объект-коннектор AutoCAD (с атрибутом doc).

    Возвращает:
        str — сообщение об успехе или ошибке.
    """
    try:
        acad.doc.SendCommand("_UNDO _Back ")
        return "Последнее действие отменено."
    except Exception as e:
        return f"Ошибка отмены последнего действия: {str(e)}"


# ========================================================================
# ГЛАВНЫЙ ДИСПЕТЧЕР ДЕКЛАРАТИВНЫХ JSON-ПАСПОРТОВ
# ========================================================================

def execute_json_command(json_str_or_dict, acad, acad_app, active_doc, model_space, target_obj,
                         unsupported_prefix="[НЕ ПОДДЕРЖИВАЕТСЯ]"):
    """Безопасно исполняет декларативный JSON-паспорт команды.

    Принимает паспорт в виде СТРОКИ или уже готового dict:
        { "command": "имя_команды", "params": { ... } }

    На основе поля "command" вызывает соответствующую функцию агента. Возвращает
    строку результата; при сбоях ИИ (неполные/некорректные параметры) перехватывает
    KeyError/ValueError и возвращает понятное сообщение об ошибке для Self-Healing.

    Аргументы:
        json_str_or_dict: строка JSON либо словарь-паспорт команды.
        acad: объект-коннектор AutoCAD (с атрибутом doc).
        acad_app: объект Application AutoCAD.
        active_doc: активный документ AutoCAD.
        model_space: коллекция ModelSpace.
        target_obj: COM-объект примитива (выбранный пользователем или None).
        unsupported_prefix: маркер при невозможности выполнить операцию.

    Возвращает:
        str — результат выполнения команды либо понятное сообщение об ошибке.
    """
    import json

    # Ленивый импорт функций агента-аналитика для чтения данных.
    from agent_analyst import (read_document_property, inspect_object, find_objects,
                               count_by_layer, list_layers, list_layouts, list_open_documents)

    # ---- 1. БЕЗОПАСНЫЙ ПАРСИНГ: нормализуем вход в словарь ----
    if isinstance(json_str_or_dict, str):
        try:
            cmd_dict = json.loads(json_str_or_dict.strip())
        except Exception as e:
            return f"Ошибка парсинга JSON-паспорта: {str(e)}"
    elif isinstance(json_str_or_dict, dict):
        cmd_dict = json_str_or_dict
    else:
        return f"Ошибка: Неверный формат паспорта команды: {type(json_str_or_dict).__name__}"

    # ---- 2. ВАЛИДАЦИЯ СТРУКТУРЫ ----
    if not isinstance(cmd_dict, dict):
        return f"Ошибка: JSON должен быть объектом-паспортом, получено: {type(cmd_dict).__name__}"

    command = cmd_dict.get("command", "")
    params = cmd_dict.get("params", {})
    if not isinstance(params, dict):
        params = {}

    # ---- 3. МАРШРУТИЗАЦИЯ ПО ФУНКЦИЯМ АГЕНТОВ ----
    try:
        if command == "zoom_all":
            # Показать весь чертёж целиком (без параметров).
            return zoom_all(acad_app)

        elif command == "create_layer":
            # Создание слоя с ACI-цветом: обязательны name и color_aci.
            try:
                name = params["name"]
                color_aci = params["color_aci"]
            except KeyError as e:
                return f"Ошибка: Отсутствует обязательный параметр {e} для команды 'create_layer'. Требуются 'name' и 'color_aci'."
            return create_layer_aci(acad, name, color_aci)

        elif command == "switch_layout":
            # Переключение листа: обязателен name.
            try:
                name = params["name"]
            except KeyError as e:
                return f"Ошибка: Отсутствует обязательный параметр {e} для команды 'switch_layout'. Требуется 'name'."
            return switch_to_layout(active_doc, name)

        elif command == "switch_to_model":
            # Переключение на пространство модели (JSON-интерфейс оператора).
            return switch_to_model()

        elif command == "switch_to_sheet":
            # Переключение на конкретный лист по имени.
            name = params.get("name") or params.get("sheet") or ""
            return switch_to_sheet(name)

        elif command == "switch_to_drawing":
            # Активация другой открытой вкладки чертежа.
            name = params.get("name") or params.get("drawing") or ""
            return switch_to_drawing(name)

        elif command == "change_object_color":
            # Изменение цвета объекта по его Handle.
            handle = params.get("handle") or ""
            color = params.get("color") or params.get("color_aci") or 7
            return change_object_color(handle, color)

        elif command == "change_layer_color":
            # Изменение цвета слоя по его имени.
            layer = params.get("layer") or params.get("name") or ""
            color = params.get("color") or params.get("color_aci") or 7
            return change_layer_color(layer, color)

        elif command == "zoom_to_object":
            # Фокус на выделенном объекте (без параметров, объект уже известен).
            return zoom_to_object(acad, target_obj)

        elif command in ("get_property", "read_data"):
            # ИНФОРМАЦИОННЫЙ ЗАПРОС: чтение данных/свойств документа через COM.
            try:
                property_name = params.get("property") or params.get("name") or ""
            except KeyError:
                property_name = ""
            if not property_name:
                return ("Ошибка: Для команды 'get_property' обязателен параметр 'property'. "
                        "Доступно: document_name, document_path, object_count, object_types, layout_name.")
            return read_document_property(active_doc, property_name)

        elif command in ("inspect_object", "inspect", "информация об объекте", "свойства объекта"):
            # Чтение свойств выделенного объекта (тип, слой, цвет, геометрия).
            return inspect_object(target_obj, unsupported_prefix)

        elif command in ("modify_object", "modify", "изменить объект", "edit_object", "редактировать объект"):
            # Изменение выделенного объекта. action: move/rotate/scale/set_layer/set_color/hide/show/delete.
            action = params.get("action") or params.get("operation") or params.get("операция") or ""
            return modify_object(acad, target_obj, action, params, unsupported_prefix)

        elif command in ("find_objects", "search_objects", "найти объекты", "поиск объектов"):
            # Поиск объектов по типу примитива и/или слою.
            return find_objects(
                model_space,
                params.get("obj_type") or params.get("type") or params.get("тип"),
                params.get("layer") or params.get("слой"),
            )

        elif command in ("select_by_type", "select_objects", "выделить объекты", "выделить по типу"):
            # Выделение объектов в AutoCAD по типу и/или слою.
            return select_by_type(
                acad, model_space,
                params.get("obj_type") or params.get("type") or params.get("тип"),
                params.get("layer") or params.get("слой"),
            )

        elif command in ("count_by_layer", "count_layer", "подсчет по слоям", "количество по слоям"):
            # Количество объектов на каждом слое.
            return count_by_layer(model_space)

        elif command in ("list_layers", "layers", "список слоёв", "список слоев", "какие слои"):
            # Список всех слоёв чертежа.
            return list_layers(active_doc)

        elif command in ("list_layouts", "layouts", "список листов", "список вкладок", "какие листы"):
            # Список всех листов/вкладок чертежа.
            return list_layouts(active_doc)

        elif command in ("undo", "undo_last", "отменить", "отмена"):
            # Отмена последнего действия в чертеже.
            return undo_last(acad)

        elif command in ("list_open_documents", "open_documents", "список чертежей",
                         "открытые чертежи", "какие чертежи открыты", "все открытые чертежи"):
            # Перечисление всех открытых чертежей в AutoCAD.
            return list_open_documents(acad_app)

        elif command in ("unsupported", "unavailable", "cannot", "noop", "not_supported"):
            # ЧЕСТНЫЙ ОТКАЗ: запрос выходит за пределы доступных команд.
            reason = params.get("reason") or "Запрошенная операция не поддерживается текущим набором команд ассистента."
            return unsupported_prefix + " " + str(reason)

        else:
            return (f"Ошибка: Неизвестная команда '{command}' в JSON-паспорте. "
                    f"Доступно: zoom_all, create_layer, switch_layout, switch_to_model, switch_to_sheet, "
                    f"switch_to_drawing, change_object_color, change_layer_color, zoom_to_object, "
                    f"get_property, read_data, inspect_object, modify_object, find_objects, select_by_type, "
                    f"count_by_layer, list_layers, list_layouts, undo, unsupported.")

    except ValueError as e:
        # Например, color_aci передали строкой, которую не удалось привести к int.
        return f"Ошибка значения параметров команды '{command}': {str(e)}"
    except Exception as e:
        # Любой внутренний сбой движка (в т.ч. OLE/COM) — отправляется на Self-Healing.
        return f"Внутренний сбой движка при выполнении '{command}': {str(e)}"