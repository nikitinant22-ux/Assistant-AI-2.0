import traceback
import pythoncom
from pyautocad import Autocad

# Семантическая классификация и защита от зацикливания
import cad_system


def get_object_points(obj):
    """Жесткая функция ядра, которая гарантированно распакует координаты любого примитива"""
    try:
        if obj.ObjectName == "AcDbLine":
            return [list(obj.StartPoint)[:2], list(obj.EndPoint)[:2]]
        elif obj.ObjectName == "AcDbPolyline":
            coords = list(obj.Coordinates)
            return [(coords[i], coords[i+1]) for i in range(0, len(coords), 2)]
    except Exception:
        pass
    return []


def execute_cad_command(
    ai_response,
    callback_log,
    callback_error_handler,
    user_original_prompt,
    on_loop_blocked=None,
    on_success=None,
):
    """КОНТРАКТНЫЙ ИСПОЛНИТЕЛЬ (Уровень 3).

    Движок больше НЕ парсит примитивные JSON-команды. Его логика:
    1. Вырезает тело функции execute_agent_task из тегов ```python ... ```.
    2. Запускает его в безопасной песочнице cad_system.run_code_sandbox() через exec(),
       передавая активный объект acad (подключение к AutoCAD).
    3. Перехватывает любые исключения (полный стек traceback) и уводит их
       в контур рефлексии Feedback Loop через callback_error_handler.
    4. При успехе передаёт декларативный контракт результата Оценщику через on_success(contract).

    on_success (необязательно): колбэк, принимающий словарь-контракт {transaction_type, status, ...}.
    """
    ai_response = (ai_response or "").strip()

    pythoncom.CoInitialize()
    acad = None
    contract = None
    try:
        # Активное подключение к AutoCAD из cad_api-совместимого pyautocad-объекта
        acad = Autocad(create_if_not_exists=True)
        # Изолированное исполнение сгенерированного кода в песочнице
        contract = cad_system.run_code_sandbox(ai_response, acad)
    except Exception as e:
        stack = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        contract = {"transaction_type": "QUERY", "status": "ERROR", "data": stack}
    finally:
        pythoncom.CoUninitialize()

    if contract is None:
        contract = {"transaction_type": "QUERY", "status": "ERROR", "data": "Песочница не вернула контракт."}

    # ---- Сбой Python/COM -> контур рефлексии (Self-Healing / RLHF) ----
    if contract.get("status") == "ERROR":
        callback_log("Исполняемый код завершился ошибкой Python/COM.", "system")
        callback_error_handler(str(contract.get("data", "")), user_original_prompt)
        return

    # ---- Успех: декларативный контракт уходит Оценщику ----
    if on_success:
        on_success(contract)
        return

    # Запасной вывод, если колбэк успеха не передан
    if contract.get("transaction_type") == "QUERY":
        callback_log(f"\nИИ-Ассистент: {contract.get('data', '')}\n", "ai")
    else:
        affected = contract.get("affected_objects", [])
        detail = (" Затронуто объектов: " + ", ".join(
            f"{o.get('type', '?')}#{o.get('handle', '?')}" for o in affected[:10])) if affected else ""
        callback_log(f"Сценарий успешно выполнен.{detail}", "system")
        try:
            if acad is not None:
                acad.doc.Utility.Prompt("ИИ выполнил инженерную задачу.\n")
        except Exception:
            pass
