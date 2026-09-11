# -*- coding: utf-8 -*-
"""core_ontology.py — Глобальная онтология и умный стек памяти проекта.

Данный модуль — централизованная система ведения контекста и тезауруса терминов
AutoCAD. Он защищает агентов от путаницы в инженерных классах (русский термин
«круг» жёстко связывается с официальным классом ActiveX IAcadCircle и методом
AddCircle) и позволяет корректно обрабатывать сложные запросы с несколькими
местоимениями, включая работу с группами объектов.

Вся техническая информация (классы, методы, свойства, коллекции) извлечена из
официального живого справочника ``cad_reference.md``, поэтому имена объектов
ActiveX абсолютно точны и не выдуманы «на глаз».

Основные сущности:
    1. AUTOCAD_THESAURUS — статический словарь: русский термин -> официальный
       класс ActiveX + метод создания + список свойств + «белый список» агентов.
    2. GlobalStateManager — класс динамического стека памяти проекта:
       имя активного документа, очередь последних одиночных Handle (LIFO,
       до 5 элементов) и словарь групп объектов (массивы Handle).
    3. get_cad_mapping(russian_word) — интерфейсный поиск: очищает слово
       пользователя, находит синоним в тезаурусе и возвращает готовую
       техническую спецификацию для агентов.

Все комментарии и docstrings написаны на русском языке.
"""

from __future__ import annotations

import re  # разбор местоимений и нормализация слов в запросе пользователя


# ========================================================================
# ТЕЗАУРУС ТЕРМИНОВ AUTOCAD (СТАТИЧЕСКИЙ СЛОВАРЬ)
# ========================================================================
# Ключ — русский инженерный термин (или вариант написания), значение — словарь
# технической спецификации объекта AutoCAD ActiveX.
#
# Поля спецификации:
#   cad_class        — официальное имя класса COM-объекта (например, IAcadCircle);
#   class_name       — краткое «человекочитаемое» имя класса для шпаргалки ИИ
#                      (сохранено для обратной совместимости с main_router.py);
#   api_method       — метод пространства ModelSpace/Block, создающий объект;
#   api_params       — кортеж имён параметров метода создания;
#   properties       — список читаемых/записываемых свойств объекта;
#   methods          — список методов, вызываемых на объекте;
#   collection       — выражение доступа к коллекции (например, "doc.Layers");
#   allowed_agents   — «белый список» агентов, которым разрешено использовать;
#   synonyms         — варианты написания / склонения русского термина.
AUTOCAD_THESAURUS = {
    # ------------------------------------------------------------------
    # ГЕОМЕТРИЧЕСКИЕ ПРИМИТИВЫ
    # ------------------------------------------------------------------
    "линия": {
        "cad_class": "IAcadLine",
        "class_name": "AcDbLine",
        "api_method": "AddLine",
        "api_params": ("StartPoint", "EndPoint"),
        "properties": [
            "StartPoint", "EndPoint", "Length", "Angle", "Delta",
            "Thickness", "Normal", "Layer", "color", "Linetype",
        ],
        "methods": ["Offset", "Move", "Rotate", "Mirror", "Copy", "ScaleEntity"],
        "collection": "doc.ModelSpace",
        "allowed_agents": ["draftsman", "analyst", "operator", "reference"],
        "synonyms": ("линию", "линии", "отрезок", "отрезка", "отрезки", "прямая", "прямую"),
    },
    "полилиния": {
        "cad_class": "IAcadLWPolyline",
        "class_name": "AcDbLWPolyline",
        "api_method": "AddLightWeightPolyline",
        "api_params": ("VerticesList",),
        "properties": [
            "Coordinates", "Closed", "Length", "Area", "ConstantWidth",
            "Elevation", "LinetypeGeneration", "Layer", "color", "Linetype",
        ],
        "methods": ["AddVertex", "SetBulge", "SetWidth", "Offset", "Explode", "Move"],
        "collection": "doc.ModelSpace",
        "allowed_agents": ["draftsman", "analyst", "operator", "reference"],
        "synonyms": ("ломаная", "контур", "полилинии", "контуры"),
    },
    "прямоугольник": {
        "cad_class": "IAcadLWPolyline",
        "class_name": "AcDbLWPolyline (замкнутый контур)",
        "api_method": "AddLightWeightPolyline",
        "api_params": ("VerticesList",),
        "properties": [
            "Coordinates", "Closed", "Length", "Area", "Layer", "color", "Linetype",
        ],
        "methods": ["Move", "Rotate", "Copy", "ScaleEntity"],
        "collection": "doc.ModelSpace",
        "allowed_agents": ["draftsman", "analyst", "operator", "reference"],
        "synonyms": ("прямоугольники", "рамку", "рамка", "четырёхугольник"),
    },
    "круг": {
        "cad_class": "IAcadCircle",
        "class_name": "AcDbCircle",
        "api_method": "AddCircle",
        "api_params": ("Center", "Radius"),
        "properties": [
            "Center", "Radius", "Diameter", "Area", "Circumference",
            "Thickness", "Normal", "Layer", "color", "Linetype",
        ],
        "methods": ["Offset", "Move", "Rotate", "Mirror", "Copy"],
        "collection": "doc.ModelSpace",
        "allowed_agents": ["draftsman", "analyst", "operator", "reference"],
        "synonyms": ("окружность", "окружности", "круги", "кружок"),
    },
    "дуга": {
        "cad_class": "IAcadArc",
        "class_name": "AcDbArc",
        "api_method": "AddArc",
        "api_params": ("Center", "Radius", "StartAngle", "EndAngle"),
        "properties": [
            "Center", "Radius", "StartAngle", "EndAngle", "TotalAngle",
            "ArcLength", "Area", "Thickness", "Layer", "color",
        ],
        "methods": ["Offset", "Move", "Rotate", "Mirror", "Copy"],
        "collection": "doc.ModelSpace",
        "allowed_agents": ["draftsman", "analyst", "reference"],
        "synonyms": ("дуги", "арка", "арки", "дугу"),
    },
    "эллипс": {
        "cad_class": "IAcadEllipse",
        "class_name": "AcDbEllipse",
        "api_method": "AddEllipse",
        "api_params": ("Center", "MajorAxis", "RadiusRatio"),
        "properties": [
            "Center", "MajorRadius", "MinorRadius", "RadiusRatio",
            "StartAngle", "EndAngle", "Layer", "color",
        ],
        "methods": ["Offset", "Move", "Rotate", "Mirror", "Copy"],
        "collection": "doc.ModelSpace",
        "allowed_agents": ["draftsman", "analyst", "reference"],
        "synonyms": ("эллипсы", "овал", "овалы"),
    },
    # ------------------------------------------------------------------
    # ТЕКСТЫ
    # ------------------------------------------------------------------
    "текст": {
        "cad_class": "IAcadText / IAcadMText",
        "class_name": "AcDbText / AcDbMText",
        "api_method": "AddText",
        "api_params": ("TextString", "InsertionPoint", "Height"),
        "properties": [
            "TextString", "InsertionPoint", "Height", "Rotation",
            "StyleName", "Layer", "color", "Linetype",
        ],
        "methods": ["Move", "Rotate", "Copy", "ScaleEntity"],
        "collection": "doc.ModelSpace",
        "allowed_agents": ["draftsman", "operator", "reference"],
        "synonyms": (
            "текстовую", "текстовые", "надпись", "надписи", "подпись",
            "многострочный текст", "мтекст",
        ),
    },
    "многострочный текст": {
        "cad_class": "IAcadMText",
        "class_name": "AcDbMText",
        "api_method": "AddMText",
        "api_params": ("InsertionPoint", "Width", "Text"),
        "properties": [
            "TextString", "InsertionPoint", "Width", "Height", "Rotation",
            "AttachmentPoint", "DrawingDirection", "LineSpacingFactor",
            "StyleName", "Layer", "color",
        ],
        "methods": ["Move", "Rotate", "Copy"],
        "collection": "doc.ModelSpace",
        "allowed_agents": ["draftsman", "operator", "reference"],
        "synonyms": ("мтекст", "мультитекст", "абзац текста"),
    },
    # ------------------------------------------------------------------
    # БЛОКИ
    # ------------------------------------------------------------------
    "блок": {
        "cad_class": "IAcadBlockReference",
        "class_name": "AcDbBlockReference",
        "api_method": "InsertBlock",
        "api_params": (
            "InsertionPoint", "Name", "Xscale", "Yscale", "Zscale",
            "Rotation", "Password",
        ),
        "properties": [
            "InsertionPoint", "Name", "EffectiveName", "Rotation",
            "XScaleFactor", "YScaleFactor", "ZScaleFactor",
            "HasAttributes", "IsDynamicBlock", "Layer", "color",
        ],
        "methods": ["Explode", "GetAttributes", "GetConstantAttributes", "Move"],
        "collection": "doc.ModelSpace",
        "allowed_agents": ["draftsman", "analyst", "operator", "reference"],
        "synonyms": ("блоки", "блока", "вставка", "вставки", "вставку"),
    },
    "штамп": {
        "cad_class": "IAcadBlockReference",
        "class_name": "AcDbBlockReference (атрибутивный блок штампа)",
        "api_method": "InsertBlock",
        "api_params": (
            "InsertionPoint", "Name", "Xscale", "Yscale", "Zscale",
            "Rotation", "Password",
        ),
        "properties": [
            "InsertionPoint", "Name", "EffectiveName", "Rotation",
            "HasAttributes", "Layer", "color",
        ],
        "methods": ["GetAttributes", "GetConstantAttributes", "Explode", "Move"],
        "collection": "doc.ModelSpace",
        "allowed_agents": ["draftsman", "operator", "reference"],
        "synonyms": ("основная надпись", "рамка чертежа", "угловой штамп", "штампик"),
    },
    # ------------------------------------------------------------------
    # РАЗМЕРЫ
    # ------------------------------------------------------------------
    "размер": {
        "cad_class": "IAcadDimAligned",
        "class_name": "AcDbDimension (выровненный размер)",
        "api_method": "AddDimAligned",
        "api_params": ("ExtLine1Point", "ExtLine2Point", "TextPosition"),
        "properties": [
            "Measurement", "TextOverride", "TextPosition", "TextHeight",
            "ScaleFactor", "StyleName", "Layer", "color", "ArrowheadSize",
        ],
        "methods": ["Move", "Rotate", "Update"],
        "collection": "doc.ModelSpace",
        "allowed_agents": ["draftsman", "operator", "reference"],
        "synonyms": ("размеры", "размера", "выносной размер", "проставить размер"),
    },
    # ------------------------------------------------------------------
    # СЛУЖЕБНЫЕ СУЩНОСТИ ЧЕРТЕЖА
    # ------------------------------------------------------------------
    "слой": {
        "cad_class": "IAcadLayer",
        "class_name": "AcDbLayerTableRecord (слой чертежа)",
        "collection": "doc.Layers",
        "properties": [
            "Name", "color", "TrueColor", "LayerOn", "Freeze",
            "Lock", "Plottable", "Linetype", "Lineweight", "Description",
        ],
        "methods": [],
        "allowed_agents": ["draftsman", "analyst", "operator", "reference"],
        "synonyms": ("слои", "слоёв", "слоям", "слоя"),
    },
    "лист": {
        "cad_class": "IAcadLayout",
        "class_name": "AcDbLayout (лист чертежа)",
        "collection": "doc.Layouts",
        "properties": [
            "Name", "TabOrder", "Block", "ModelType", "PlotType",
            "StyleSheet", "PaperUnits",
        ],
        "methods": ["CopyFrom", "GetPaperSize", "SetWindowToPlot"],
        "allowed_agents": ["draftsman", "analyst", "operator", "reference"],
        "synonyms": ("вкладка", "вкладки", "листы", "пространство листа", "layout"),
    },
    "ось": {
        "cad_class": "IAcadLine",
        "class_name": "AcDbLine (осевая линия)",
        "api_method": "AddLine",
        "api_params": ("StartPoint", "EndPoint"),
        "properties": ["StartPoint", "EndPoint", "Length", "Angle", "Layer", "color", "Linetype"],
        "methods": ["Move", "Rotate", "Offset", "Copy"],
        "collection": "doc.ModelSpace",
        "allowed_agents": ["draftsman", "analyst", "reference"],
        "synonyms": ("оси", "осевые линии", "сетка осей", "осевую линию"),
    },
    "выноска": {
        "cad_class": "IAcadMLeader / IAcadLeader",
        "class_name": "AcDbMLeader / AcDbLeader",
        "api_method": "AddMLeader",
        "api_params": ("PointsArray", "leaderLineIndex"),
        "properties": ["Layer", "color", "Visible"],
        "methods": ["Move", "Rotate", "Copy"],
        "collection": "doc.ModelSpace",
        "allowed_agents": ["draftsman", "operator", "reference"],
        "synonyms": ("выноски", "стрелка выноски", "позиция", "позиции"),
    },
    "чертеж": {
        "cad_class": "IAcadDocument",
        "class_name": "AcDbDocument",
        "properties": [
            "Name", "FullName", "Path", "ReadOnly", "Saved",
            "ActiveSpace", "ActiveLayout", "HWND", "WindowTitle",
        ],
        "methods": [
            "Save", "SaveAs", "Close", "Open", "GetVariable",
            "SetVariable", "SendCommand", "PostCommand", "Regen",
            "PurgeAll", "Activate",
        ],
        "allowed_agents": ["draftsman", "analyst", "operator", "reference"],
        "synonyms": ("файл", "файла", "документ", "документа", "чертежа", "dwg"),
    },
}

# Вспомогательная «обратная» карта синонимов -> канонический ключ тезауруса.
# Строится один раз при импорте, чтобы ускорить поиск в get_cad_mapping.
_SYNONYM_INDEX = {}
for _term, _info in AUTOCAD_THESAURUS.items():
    _syns = _info.get("synonyms", ())
    for _s in _syns:
        _SYNONYM_INDEX.setdefault(_s.lower(), _term)

# Регулярные выражения для распознавания анафорических маркеров в тексте.
_PLURAL_MARKER_RE = re.compile(r"(их|все эти|эти объекты|этих|несколько|группу)", re.IGNORECASE)
_CYCLE_MARKER_RE = re.compile(r"(в каждом|для всех|каждый|всех из них)", re.IGNORECASE)
_SINGULAR_MARKER_RE = re.compile(r"(его|эту деталь|этот объект|этого|удалить его|перекрась его)", re.IGNORECASE)


class GlobalStateManager:
    """Умный стек памяти проекта для разрешения анафор и работы с группами.

    Хранит в реальном времени:
        - active_document_name: имя текущего открытого чертежа (из doc.Name);
        - history_single_objects: LIFO-очередь последних одиночных Handle
          (ограничена 5 элементами, каждый элемент — словарь с типом и агентом);
        - history_groups: словарь имён групп -> списки Handle (выноски, сетки осей).

    Предоставляет методы для наполнения стека, определения двусмысленности,
    извлечения группы/цикла и генерации текстовой «шпаргалки» для ИИ-моделей.
    """

    # Максимальная длина стека одиночных объектов.
    MAX_SINGLE_STACK = 5

    def __init__(self):
        """Инициализирует пустой стек одиночных объектов и словарь групп."""
        # Имя активного документа (заполняется из doc.Name в main_router.py).
        self.active_document_name = ""
        # Очередь последних затронутых одиночных Handle (LIFO, до 5 элементов).
        self.history_single_objects = []
        # Словарь групп объектов: {имя_группы: [Handle, ...]}.
        self.history_groups = {}

    # ------------------------------------------------------------------
    # НАПОЛНЕНИЕ ПАМЯТИ
    # ------------------------------------------------------------------
    def set_active_document(self, doc_name):
        """Записывает имя текущего активного чертежа.

        Аргументы:
            doc_name: строковое имя документа из doc.Name (может быть None).
        """
        if doc_name:
            self.active_document_name = str(doc_name)

    def push_object(self, handle, obj_type="unknown", agent=None):
        """Добавляет одиночный объект в стек последних затронутых.

        Новый элемент помещается в начало очереди; если длина превышает
        MAX_SINGLE_STACK, самый старый элемент отбрасывается.

        Аргументы:
            handle: строковый Handle объекта.
            obj_type: класс/тип объекта (например, 'draw_circle' или AcDbCircle).
            agent: ключ агента, создавшего/затронувшего объект.
        """
        if not handle:
            return
        item = {"handle": str(handle), "obj_type": str(obj_type), "agent": agent}
        # Убираем дубликат того же Handle, если он уже есть в стеке.
        self.history_single_objects = [
            x for x in self.history_single_objects if x.get("handle") != str(handle)
        ]
        self.history_single_objects.insert(0, item)
        # Обрезаем до допустимой длины стека.
        if len(self.history_single_objects) > self.MAX_SINGLE_STACK:
            self.history_single_objects = self.history_single_objects[: self.MAX_SINGLE_STACK]

    def add_group(self, group_name, handles):
        """Сохраняет группу объектов (массив Handle) под заданным именем.

        Аргументы:
            group_name: имя группы (например, «выноски», «сетка осей»).
            handles: список Handle, входящих в группу.
        """
        if not group_name or not handles:
            return
        self.history_groups[str(group_name)] = [str(h) for h in handles]

    # ------------------------------------------------------------------
    # ЧТЕНИЕ СОСТОЯНИЯ
    # ------------------------------------------------------------------
    def latest_handle(self):
        """Возвращает Handle самого свежего одиночного объекта.

        Возвращает:
            str | None — Handle либо None, если стек пуст.
        """
        if self.history_single_objects:
            return self.history_single_objects[0].get("handle")
        return None

    def latest_obj_type(self):
        """Возвращает тип самого свежего одиночного объекта.

        Возвращает:
            str | None — тип объекта либо None, если стек пуст.
        """
        if self.history_single_objects:
            return self.history_single_objects[0].get("obj_type")
        return None

    def last_handles(self, count=2):
        """Возвращает список последних затронутых Handle (самые свежие первыми).

        Используется для анализа пересечений между двумя недавними объектами
        (например, квадратом и кругом из контекста анафоры).

        Аргументы:
            count: сколько последних Handle вернуть (по умолчанию 2).

        Возвращает:
            list[str] — список Handle, либо пустой список, если стек пуст.
        """
        return [x.get("handle") for x in self.history_single_objects[:count]
                if x.get("handle")]

    def is_ambiguous(self):
        """Определяет двусмысленность контекста одиночного объекта.

        Контекст считается неоднозначным, если в стеке лежит более одного
        объекта РАЗНОТИПНОГО происхождения (например, недавно построены и круг,
        и линия), а также если объекты созданы разными агентами.

        Возвращает:
            bool — True, если требуется уточнение у пользователя.
        """
        if len(self.history_single_objects) <= 1:
            return False
        types = {x.get("obj_type") for x in self.history_single_objects}
        agents = {x.get("agent") for x in self.history_single_objects}
        # Неоднозначно, если объектов несколько и они разнородны.
        return len(types) > 1 or len(agents) > 1

    def clarification_message(self):
        """Формирует вежливый уточняющий запрос к пользователю на русском языке.

        Используется при запрете выполнения функции из-за двусмысленности.

        Возвращает:
            str — вопрос-уточнение со списком недавних объектов.
        """
        recent = self.history_single_objects[:5]
        if not recent:
            return ("Уточните, пожалуйста, о каком объекте идёт речь: "
                    "в истории нет недавно созданных объектов.")
        items = []
        for item in recent:
            handle = item.get("handle")
            otype = item.get("obj_type", "объект")
            items.append(f"{otype} (Handle: {handle})")
        joined = ", ".join(items)
        return ("Уточните, пожалуйста, о каком именно объекте идёт речь. "
                f"Недавно были затронуты: {joined}. Укажите Handle или опишите объект точнее.")

    # ------------------------------------------------------------------
    # РАЗРЕШЕНИЕ АНАФОР (ГРУППА / ЦИКЛ)
    # ------------------------------------------------------------------
    def resolve_group(self, user_prompt):
        """Извлекает массив Handle активной группы по множественным местоимениям.

        Если в запросе встречаются маркеры группы («их», «все эти») или цикла
        («в каждом», «для всех»), возвращается конкатенация Handle из всех
        сохранённых групп (приоритет — самая последняя группа).

        Аргументы:
            user_prompt: текст запроса пользователя.

        Возвращает:
            list[str] — список Handle группы либо пустой список.
        """
        if not isinstance(user_prompt, str):
            return []
        is_group = bool(_PLURAL_MARKER_RE.search(user_prompt))
        is_cycle = bool(_CYCLE_MARKER_RE.search(user_prompt))
        if not (is_group or is_cycle):
            return []
        # Собираем все Handle из групп (для цикла «в каждом» берём всё сразу).
        handles = []
        for name, group_handles in self.history_groups.items():
            handles.extend(group_handles)
        return handles

    # ------------------------------------------------------------------
    # ГЕНЕРАЦИЯ ШПАРГАЛКИ ДЛЯ ИИ-МОДЕЛИ
    # ------------------------------------------------------------------
    def render_thesaurus_prompt(self, agent_key=None):
        """Формирует текстовую «шпаргалку» из тезауруса и текущего среза памяти.

        Подмешивается в скрытый системный контекст перед каждым запросом, чтобы
        локальные модели (Qwen Coder, DeepSeek-R1) не путались в терминах и знали,
        о каком объекте/чертеже идёт речь.

        Аргументы:
            agent_key: ключ агента (для фильтрации «белого списка» тезауруса).

        Возвращает:
            str — текст шпаргалки либо пустая строка, если нечего показать.
        """
        parts = ["[Онтология AutoCAD]: известные термины:"]
        shown_terms = 0
        for term, info in AUTOCAD_THESAURUS.items():
            allowed = info.get("allowed_agents", [])
            # Фильтруем по «белому списку» агента, если передан ключ.
            if agent_key and agent_key not in allowed:
                continue
            # Приоритет — официальный класс ActiveX; fallback на старое имя.
            cls = info.get("cad_class") or info.get("class_name") or "?"
            parts.append(f"- «{term}» -> {cls}")
            shown_terms += 1
        if shown_terms == 0:
            # Тезаурус для данного агента пуст — возвращаем только состояние памяти.
            return self.render_state_slice()
        # Добавляем актуальный срез памяти (последние объекты и группы).
        state_slice = self.render_state_slice()
        if state_slice:
            parts.append(state_slice)
        return "\n".join(parts)

    def render_state_slice(self):
        """Формирует строку-срез текущего состояния памяти проекта.

        Возвращает:
            str — сводка имени документа, последних одиночных объектов и групп.
        """
        lines = []
        if self.active_document_name:
            lines.append(f"Активный чертёж: {self.active_document_name}")
        if self.history_single_objects:
            objs = ", ".join(
                f"{x.get('obj_type')}(Handle:{x.get('handle')})"
                for x in self.history_single_objects
            )
            lines.append(f"Последние объекты: {objs}")
        if self.history_groups:
            groups = ", ".join(
                f"{name}[{len(handles)} шт.]" for name, handles in self.history_groups.items()
            )
            lines.append(f"Активные группы: {groups}")
        if not lines:
            return ""
        return "[Память проекта]: " + " | ".join(lines)


# Единственный глобальный экземпляр менеджера состояния (singleton для проекта).
ONTOLOGY_STATE = GlobalStateManager()


# ========================================================================
# ИНТЕРФЕЙСНЫЙ ПОИСК ПО ТЕЗАУРУСУ
# ========================================================================
def _normalize_word(word):
    """Очищает и приводит русское слово к нижнему регистру.

    Убирает лишние пробелы, знаки пунктуации и дефисы по краям. Возвращает
    нормализованную строку либо пустую строку, если вход некорректен.

    Аргументы:
        word: произвольное русское слово/фраза пользователя.

    Возвращает:
        str — нормализованное слово.
    """
    if not isinstance(word, str):
        return ""
    cleaned = re.sub(r"\s+", " ", word.strip().lower())
    # Срезаем пунктуацию и незначащие символы по краям фразы.
    cleaned = cleaned.strip(" .,;:!?()[]{}«»\"'\\/|-\t\n")
    return cleaned


def get_cad_mapping(russian_word):
    """Находит техническую спецификацию объекта САПР по русскому термину.

    Очищает введённое слово, ищет его среди канонических ключей и синонимов
    тезауруса AUTOCAD_THESAURUS и возвращает готовую спецификацию: какой класс
    ActiveX, каким методом создавать, какие свойства читать, из какой коллекции.

    Аргументы:
        russian_word: русский инженерный термин или фраза пользователя.

    Возвращает:
        dict | None — словарь технической спецификации (cad_class, api_method,
        properties, collection, methods, allowed_agents и т.д.) либо None, если
        термин не найден в тезаурусе.
    """
    cleaned = _normalize_word(russian_word)
    if not cleaned:
        return None

    # 1. Прямое совпадение с каноническим ключом.
    if cleaned in AUTOCAD_THESAURUS:
        return AUTOCAD_THESAURUS[cleaned]

    # 2. Точное совпадение с синонимом через обратный индекс.
    if cleaned in _SYNONYM_INDEX:
        return AUTOCAD_THESAURUS[_SYNONYM_INDEX[cleaned]]

    # 3. Поиск по вхождению: слово может быть частью ключа или синонима.
    #    Например, «окружность радиусом» -> найдём «круг».
    for term, info in AUTOCAD_THESAURUS.items():
        if cleaned.startswith(term) or term in cleaned:
            return info
        for syn in info.get("synonyms", ()):
            if syn in cleaned:
                return info

    # Термин не распознан — возвращаем None, чтобы агент запросил уточнение.
    return None


def mentions_singular_anaphora(text):
    """Проверяет наличие одиночного местоимения («его», «эту деталь» и т.п.).

    Аргументы:
        text: текст запроса пользователя.

    Возвращает:
        bool — True, если в тексте есть маркер одиночной анафоры.
    """
    if not isinstance(text, str):
        return False
    return bool(_SINGULAR_MARKER_RE.search(text))


def mentions_group_anaphora(text):
    """Проверяет наличие множественного/циклического маркера («их», «в каждом»).

    Аргументы:
        text: текст запроса пользователя.

    Возвращает:
        bool — True, если в тексте есть маркер группы или цикла.
    """
    if not isinstance(text, str):
        return False
    return bool(_PLURAL_MARKER_RE.search(text) or _CYCLE_MARKER_RE.search(text))


if __name__ == "__main__":
    # Лёгкая самопроверка при запуске модуля напрямую.
    state = GlobalStateManager()
    state.set_active_document("Чертеж_А.dwg")
    state.push_object("H1", "draw_circle", "draftsman")
    state.add_group("выноски", ["H10", "H11", "H12"])
    print(state.render_thesaurus_prompt("draftsman"))
    print()
    print("Анафора «их»:", state.resolve_group("измени их цвет"))
    print("Неоднозначность:", state.is_ambiguous())
    print()
    print("=== Проверка get_cad_mapping ===")
    for sample in ["круг", "окружность", "прямоугольник", "чертеж", "файл",
                   "слоя", "лист", "вкладка", "размер", "блоки", "многострочный текст"]:
        spec = get_cad_mapping(sample)
        if spec:
            print(f"«{sample}» -> {spec.get('cad_class')} | "
                  f"{spec.get('api_method')} | props: {len(spec.get('properties', []))} шт.")
        else:
            print(f"«{sample}» -> НЕ НАЙДЕНО")