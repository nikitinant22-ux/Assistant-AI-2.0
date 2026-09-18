# -*- coding: utf-8 -*-
"""set_layer_status.py — Атомарный кубик №3: управление слоями AutoCAD.

Изолированный файл по принципу «ОДИН КУБИК — ОДИН НЕЗАВИСИМЫЙ ФАЙЛ»
(Этап 6, Шаг 2). Декоратор @register_cad_tool(name="set_layer_status")
автоматически регистрирует кубик в глобальном реестре
tool_registry.CAD_TOOL_REGISTRY при импорте — автосканер пакета tools
загружает этот файл при старте приложения. Файл является МОДУЛЕМ: он не
запускается как скрипт (это исключает двойную регистрацию); самодиагностика
кубиков живёт в main_router.py (__main__).

Архитектурные правила (Канон 3, 8.1, 8.2, 16.5):
  - связь с САПР — исключительно через pyautocad (мост tools._shared);
  - операции выполняются нативными свойствами ActiveX (Freeze, LayerOn);
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
# Общий служебный слой пакета: мост связи с САПР и строитель JSON-ошибок.
from tools._shared import build_error, ensure_connection


@register_cad_tool(name="set_layer_status")
def set_layer_status(layer_name: str = "0", status: str = "create") -> str:
    """Layer status control cube for AutoCAD layers.

    Performs an operation on a layer through the ActiveX Layers collection:

      - "create" — creates a new layer if it does not exist yet;
      - "freeze" — freezes the layer (the ACTIVE layer cannot be frozen —
                   AutoCAD raises a COM error converted into a readable status);
      - "thaw"   — unfreezes a previously frozen layer;
      - "on"     — turns layer visibility on;
      - "off"    — turns layer visibility off (objects become hidden).

    The layer is searched by name case-insensitively (no duplicates like
    "LAYER_1" / "layer_1"). All operations use native ActiveX properties
    (Freeze, LayerOn) — no raw win32com or Variant packing.

    Args:
        layer_name: Target layer name (e.g. "0" or "ОСИ").
        status: One of the operations: "create", "freeze", "thaw", "on", "off".

    Returns:
        Strictly valid JSON string:
          - success: {"status": "success", "message": "Result description"}
          - error:   {"status": "error", "message": "Error description"}
    """
    # ---- Этап 0: Валидация входных данных (Пока-ёкэ) ----
    # Список разрешённых операций фиксирован — любое другое значение
    # отклоняется ДО обращения к САПР (принцип Пока-ёкэ).
    _ALLOWED_STATUSES = ("create", "freeze", "thaw", "on", "off")
    layer_name = (layer_name or "").strip()
    if not layer_name:
        return build_error("Имя слоя не может быть пустым.")
    status = (status or "").strip().lower()
    if status not in _ALLOWED_STATUSES:
        return build_error(
            "Неизвестный статус слоя '{}'. Допустимые значения: {}.".format(
                status, ", ".join(_ALLOWED_STATUSES)
            )
        )

    # ---- Этап 1: Получение живого моста связи с САПР ----
    acad = ensure_connection()
    if acad is None:
        return build_error(
            "Не удалось установить соединение с AutoCAD. "
            "Проверьте, что САПР запущена, и повторите попытку."
        )

    try:
        # ---- Этап 2: Поиск слоя в коллекции Layers (без учёта регистра) ----
        layer_obj = None
        for layer in acad.doc.Layers:
            if str(layer.Name).strip().lower() == layer_name.lower():
                layer_obj = layer
                break

        # Слой не найден: создаём его только по явному запросу "create".
        if layer_obj is None:
            if status != "create":
                return build_error(
                    "Слой '{}' не найден на чертеже. Сначала создайте его "
                    "статусом 'create'.".format(layer_name)
                )
            # Нативный ActiveX-вызов создания нового слоя (Канон 3).
            layer_obj = acad.doc.Layers.Add(layer_name)

        # ---- Этап 3: Применение выбранной операции над слоем ----
        if status == "create":
            message = "Слой '{}' успешно создан.".format(layer_name)
        elif status == "freeze":
            try:
                # Активный слой заморозить нельзя — AutoCAD бросает COM-ошибку,
                # которую превращаем в понятный статус (Пока-ёкэ).
                layer_obj.Freeze = True
            except Exception:
                return build_error(
                    "Слой '{}' является текущим активным слоем и не может "
                    "быть заморожен. Сначала переключите активный слой.".format(
                        layer_name
                    )
                )
            message = "Слой '{}' успешно заморожен.".format(layer_name)
        elif status == "thaw":
            layer_obj.Freeze = False
            message = "Слой '{}' успешно разморожен.".format(layer_name)
        elif status == "on":
            layer_obj.LayerOn = True
            message = "Слой '{}' успешно включён.".format(layer_name)
        else:  # status == "off"
            layer_obj.LayerOn = False
            message = "Слой '{}' успешно выключен.".format(layer_name)

        # ---- Этап 4: Формирование ответа (Пока-ёкэ) ----
        return json.dumps(
            {"status": "success", "message": message}, ensure_ascii=False
        )
    except Exception as exc:
        # Любая COM-ошибка коллекции Layers перехватывается и возвращается в JSON.
        return build_error(
            "Ошибка управления слоем '{}': {}".format(layer_name, exc)
        )