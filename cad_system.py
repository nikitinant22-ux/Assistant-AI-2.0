import os
import json
import re
import traceback
import threading
import array as _array_mod
import math as _math_mod
import psutil
import tkinter as tk
from pyautocad import APoint

# Динамически определяем папку запуска (профессиональный относительный путь)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE_FILE = os.path.join(BASE_DIR, "cad_knowledge.json")
MEMORY_FILE = os.path.join(BASE_DIR, "cad_memory.json")

def get_api_context(user_query):
    """Высокоскоростной Dynamic RAG 2.0. Сканирует ключевые слова геометрии.
    Возвращает контрактные шаблоны (транзакция + рекомендации) для генерации кода."""
    if not os.path.exists(KNOWLEDGE_FILE):
        return ""
    try:
        with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
            knowledge_base = json.load(f)
        query_lower = user_query.lower()
        matched_templates = []
        for key, data in knowledge_base.items():
            for keyword in data.get("keywords", []):
                if keyword in query_lower:
                    tt = data.get("transaction_type", "QUERY")
                    tpl = data.get("template", "")
                    matched_templates.append(f"[Тип транзакции: {tt}]\n{tpl}")
                    break
        if matched_templates:
            return "\n--- ЭТАЛОННЫЙ КОНТРАКТНЫЙ ШАБЛОН РЕШЕНИЯ ---\n" + "\n\n".join(matched_templates) + "\n-----------------------------------\n"
    except Exception:
        pass
    return ""

def normalize_code_signature(code_str):
    """Возвращает очищенную сигнатуру кода для сопоставления синонимов, удаляя комментарии и пробелы"""
    # Удаляем маркеры markdown блоков
    code_clean = code_str.replace("```python", "").replace("```", "")
    # Удаляем однострочные комментарии Python
    code_clean = re.sub(r'#.*', '', code_clean)
    # Сжимаем все пробелы, табы и переносы строк в один монолитный массив букв
    return "".join(code_clean.split()).strip().lower()

def calculate_code_weight(code_str):
    """Вычисляет инженерный вес архитектуры кода (Приоритет исполнения)"""
    code_lower = code_str.lower()
    # Высший приоритет — использование нативных оптимизированных инструментов обертки
    if "cad_tools." in code_lower:
        return 100
    # Средний приоритет — прямые лаконичные COM-вызовы
    if "active_doc.activelayout =" in code_lower:
        return 50
    # Низший приоритет — тяжелые сырые переборы и циклы (код-костыль)
    if "for " in code_lower or "range(" in code_lower:
        return 20
    return 10

def check_saved_experience(user_query):
    """Извлекает проверенный рабочий код функции execute_agent_task из памяти опыта.

    Формат памяти (cad_memory.json): ключ = нормализованный промпт,
    значение = {transaction_type, code, weight, prompts}.
    Поддерживается и устаревший формат (ключ = строка кода) для обратной совместимости."""
    if not os.path.exists(MEMORY_FILE):
        return None
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            memory_base = json.load(f)

        query_clean = user_query.strip().lower()

        # Перебираем все готовые решения в базе
        for key, data in memory_base.items():
            if isinstance(data, dict):
                code = data.get("code")
                prompts = [str(p).strip().lower() for p in data.get("prompts", [])]
                if query_clean == key.strip().lower() or query_clean in prompts:
                    if code:
                        return str(code)
            elif isinstance(data, str):
                # Устаревший формат: ключ уже является кодом
                if query_clean == key.strip().lower():
                    return data
    except Exception:
        pass
    return None


def remove_experience(user_query):
    """Удаляет ошибочный опыт (промпт и его синонимы) из памяти опыта.
    Используется RLHF-Оценщиком при отбраковке неудовлетворительного решения."""
    if not os.path.exists(MEMORY_FILE):
        return
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            memory_base = json.load(f)
        query_clean = user_query.strip().lower()
        to_remove = [k for k in memory_base if k.strip().lower() == query_clean]
        if not to_remove:
            # Ищем по синонимам
            for k, data in memory_base.items():
                if isinstance(data, dict) and query_clean in [str(p).strip().lower() for p in data.get("prompts", [])]:
                    to_remove.append(k)
        for k in to_remove:
            memory_base.pop(k, None)
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory_base, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def save_successful_experience(user_query: str, working_code: str, transaction_type: str = "COMMAND") -> None:
    """Сохраняет пару [Промпт инженера] -> [Финальный рабочий код] как долгосрочный навык.

    Унифицированный формат памяти: каждый сеанс хранит исходный промпт пользователя,
    тип транзакции (COMMAND или QUERY) и сгенерированный рабочий Python-код функции.
    Код очищается от фенсов ```python и текстового Блока размышлений (extract_python_code),
    чтобы в базе лежала только чистая функция execute_agent_task(acad).

    Промпт записывается ключом, синонимы копятся в поле prompts."""
    try:
        memory_base: dict = {}
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                memory_base = json.load(f)

        query_clean: str = user_query.strip().lower()
        # Очищаем код до тела чистой функции (без reasoning и markdown-фенсов)
        new_code: str = extract_python_code(working_code).strip()
        if not new_code:
            new_code = working_code.strip()
        new_weight: int = calculate_code_weight(new_code)
        tt: str = transaction_type if transaction_type in ("COMMAND", "QUERY") else "COMMAND"

        # Аудит весов: если для этого же промпта уже сохранён более сильный код — не перезаписываем
        old_key = None
        for existing_key, data in list(memory_base.items()):
            if not isinstance(data, dict):
                continue
            prompts = [str(p).strip().lower() for p in data.get("prompts", [])]
            if query_clean == existing_key.strip().lower() or query_clean in prompts:
                old_weight = data.get("weight", calculate_code_weight(existing_key))
                if new_weight < old_weight:
                    return
                old_key = existing_key

        if old_key and old_key in memory_base:
            # Склеиваем синонимы: переносим уже накопленные промпты
            old_prompts = memory_base[old_key].get("prompts", [])
            merged = old_prompts + [query_clean]
            dedup = list(dict.fromkeys(merged))
            memory_base[query_clean] = {
                "transaction_type": tt,
                "code": new_code,
                "weight": new_weight,
                "prompts": dedup,
            }
            if old_key != query_clean:
                del memory_base[old_key]
        else:
            memory_base[query_clean] = {
                "transaction_type": tt,
                "code": new_code,
                "weight": new_weight,
                "prompts": [query_clean],
            }

        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory_base, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ========================================================================
# БЕЗОПАСНАЯ ПЕСОЧНИЦА КОНТРАКТНЫХ ИСПОЛНЯЕМЫХ СЦЕНАРИЕВ (Уровень 3)
# ========================================================================

def extract_python_code(ai_response: str) -> str:
    """Вырезает тело Python-функции execute_agent_task из тегов ```python ... ```.

    Устойчив к формату «двух блоков» (Chain-of-Thought): весь текст ДО открывающего
    тега ```python (Блок А — размышления модели на русском языке) полностью
    игнорируется и НЕ вызывает сбоев парсера. Возвращается только содержимое
    первого блока ```python ... ```. Если модель не обернула код в фенсы —
    функция возвращает ответ как есть (на случай голого кода)."""
    resp = (ai_response or "").strip()
    if not resp:
        return ""
    low = resp.lower()
    # Приоритет: первый блок между ```python и следующими ```
    if "```python" in low:
        tail = resp.split("```python", 1)[1]
        if "```" in tail:
            return tail.split("```", 1)[0].strip()
        return tail.strip()
    # Запасной вариант: любой другой фенс вида ```...\nкод...```
    m = re.search(r"```[^\n`]*\n(.*?)```", resp, flags=re.DOTALL)
    if m:
        return m.group(1).strip()
    # Экстремальный запасной вариант: ```...``` без переноса строки
    if resp.startswith("```"):
        body = resp.lstrip("`").strip()
        if "```" in body:
            body = body.split("```", 1)[0]
        return body.strip()
    return resp


def extract_reasoning_text(ai_response: str) -> str:
    """Извлекает Блок А (текстовые размышления) из ответа модели.

    Возвращает весь текст, расположенный ДО открывающего тега ```python.
    Если тега нет — весь ответ считается размышлением. Используется интерфейсом
    для вывода «мыслей вслух» модели в UI-чат до исполнения кода."""
    resp = (ai_response or "").strip()
    if not resp:
        return ""
    low = resp.lower()
    idx = low.find("```python")
    if idx >= 0:
        return resp[:idx].strip()
    # Общие фенсы любого вида
    fence = resp.find("```")
    if fence >= 0:
        return resp[:fence].strip()
    return resp


def has_code_block(ai_response: str) -> bool:
    """Проверяет, содержит ли ответ модели исполняемый блок кода ```python ... ```.

    Используется для отличия НАСТОЯЩЕГО исполняемого ответа от уточняющего диалога:
    если модель вернула только текстовый вопрос (без тегов ```python), система НЕ
    запускает песочницу и не рапортует «Сценарий успешно выполнен»."""
    low = (ai_response or "").lower()
    # Явный тег ```python
    if "```python" in low:
        return True
    # Любой другой фенс вида ```<слово>\n... (например ```py или просто ```)
    return bool(re.search(r"```[^\n`]*\n", low))


def collect_affected_objects(objs: object) -> list:
    """Собирает декларативный список affected_objects из переданных COM-объектов AutoCAD.

    Принимает любой итерируемый набор COM-объектов (list, tuple, selection set).
    Каждый элемент результата: {"handle": <хендл>, "type": <тип примитива>}."""
    result: list = []
    for o in (objs or []):
        try:
            result.append({
                "handle": str(getattr(o, "Handle", "") or ""),
                "type": str(getattr(o, "ObjectName", "") or ""),
            })
        except Exception:
            pass
    return result


def validate_contract(contract) -> tuple:
    """Проверяет структуру декларативного контракта результата.

    Возвращает кортеж (корректен_ли, сообщение_об_ошибке)."""
    if not isinstance(contract, dict):
        return False, f"Функция execute_agent_task должна вернуть словарь-контракт, получено: {type(contract).__name__}"
    tt = contract.get("transaction_type")
    if tt not in ("COMMAND", "QUERY"):
        return False, "Контракт обязан содержать поле transaction_type со значением COMMAND или QUERY."
    status = contract.get("status")
    if status != "SUCCESS":
        return False, "Контракт должен иметь status=SUCCESS (ошибки перехватывает песочница)."
    if tt == "COMMAND" and not isinstance(contract.get("affected_objects"), list):
        return False, "Контракт COMMAND обязан содержать список affected_objects."
    if tt == "QUERY" and not isinstance(contract.get("data"), str):
        return False, "Контракт QUERY обязан содержать текстовое поле data."
    return True, "ok"


def run_code_sandbox(ai_response: str, acad: object) -> dict:
    """БЕЗОПАСНАЯ ПЕСОЧНИЦА: изолированно исполняет сгенерированный моделью Python-код.

    Исполнительный движок Уровня 3. Больше НЕ парсит примитивные JSON-команды:
    1. Надёжно вырезает тело функции execute_agent_task из тегов ```python ... ```,
       полностью игнорируя текстовый Блок размышлений до тегов.
    2. Выполняет её через exec() в изолированном пространстве имён, передавая объект acad.
    3. Оборачивает выполнение в try...except: перехватывает полную трассировку
       (traceback.format_exception) и возвращает контракт ERROR, который уходит обратно
       в Qwen через автоматический Feedback Loop (Петля А, до 3 попыток).
    4. При успехе валидирует и возвращает декларативный контракт результата (COMMAND или QUERY).

    Возвращает всегда словарь-контракт."""
    # Защита от «ложного успеха»: если модель вернула только текст (уточняющий вопрос
    # или отказ) без блока ```python, песочницу НЕ запускаем и успех не рапортуем.
    if not has_code_block(ai_response):
        return {
            "transaction_type": "QUERY",
            "status": "ERROR",
            "data": "Модель вернула уточняющий текст без исполняемого кода — выполнение пропущено.",
        }

    # Надёжная вырезка тела функции: любой обычный текст/размышления ДО тега ```python
    # не влияет на результат, так как extract_python_code берёт только первый python-блок.
    code: str = extract_python_code(ai_response)
    if not code:
        return {
            "transaction_type": "QUERY",
            "status": "ERROR",
            "data": "Модель не вернула Python-код внутри тегов ```python ... ```.",
        }

    # Изолированное пространство имён: внутрь кладём активный acad и полезные модули.
    # __builtins__ сознательно НЕ подменяется (модели нужны циклы/математика), но внешний
    # контекст (глобалы вызывающего модуля) в песочницу не протекает.
    namespace: dict = {
        "acad": acad,
        "APoint": APoint,
        "math": _math_mod,
        "array": _array_mod,
        "json": json,
        "collect_affected_objects": collect_affected_objects,
        "cad_tools": __import__("cad_tools", fromlist=["*"]),
    }

    # ---- Этап компиляции и импорта тела функции ----
    try:
        exec(compile(code, "<agent_generated_code>", "exec"), namespace)
    except Exception as e:
        stack: str = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        return {"transaction_type": "QUERY", "status": "ERROR", "data": stack}

    fn = namespace.get("execute_agent_task")
    if not callable(fn):
        return {
            "transaction_type": "QUERY",
            "status": "ERROR",
            "data": "Сгенерированный код не содержит вызываемую функцию execute_agent_task(acad).",
        }

    # ---- Этап вызова функции ----
    try:
        result: dict = fn(acad)
    except Exception as e:
        stack = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        return {"transaction_type": "QUERY", "status": "ERROR", "data": stack}

    ok, msg = validate_contract(result)
    if not ok:
        return {"transaction_type": "QUERY", "status": "ERROR", "data": msg}
    return result

def open_settings_window(parent_ui):
    """Создает оригинальное окно настроек с двумя векторными синими слайдерами на Canvas."""
    settings_win = tk.Toplevel(parent_ui.root)
    settings_win.title("Настройки")
    settings_win.geometry("380x430") 
    settings_win.configure(bg=parent_ui.bg_color)
    settings_win.resizable(False, False)
    settings_win.attributes("-topmost", True)
    
    global canvas_w, canvas_h, padding_x, line_y, track_w, r_knob, saved_vals
    global min_ram_mb, absolute_max_ram_mb, total_ram_mb, required_reserve_mb
    
    canvas_w, canvas_h = 240, 30
    padding_x = 15
    line_y = canvas_h // 2
    track_w = canvas_w - (padding_x * 2)
    r_knob = 7  

    total_mem_bytes = psutil.virtual_memory().total
    total_ram_mb = int(total_mem_bytes / (1024 * 1024)) 
    
    min_ram_mb = int(total_ram_mb * 0.10)
    required_reserve_mb = max(5120, min_ram_mb)
    absolute_max_ram_mb = total_ram_mb - required_reserve_mb
    
    if absolute_max_ram_mb <= min_ram_mb:
        min_ram_mb = 1024
        absolute_max_ram_mb = max(2048, total_ram_mb - 5120)

    saved_vals = {"hist": parent_ui.max_history_len, "ram": parent_ui.max_ram_limit_mb}

    if saved_vals['ram'] > absolute_max_ram_mb: saved_vals['ram'] = absolute_max_ram_mb
    if saved_vals['ram'] < min_ram_mb: saved_vals['ram'] = min_ram_mb

    tk.Label(settings_win, text="Глубина истории сообщений", fg=parent_ui.text_color, bg=parent_ui.bg_color, font=("Segoe UI Semibold", 10)).pack(pady=(15, 2))
    hist_val_lbl = tk.Label(settings_win, text=f"{saved_vals['hist']}", fg="#2196F3", bg=parent_ui.bg_color, font=("Segoe UI Semibold", 14))
    hist_val_lbl.pack()

    hist_frame = tk.Frame(settings_win, bg=parent_ui.bg_color)
    hist_frame.pack(fill="x", padx=20)
    tk.Label(hist_frame, text="2", fg="#B0B0B5", bg=parent_ui.bg_color, font=("Segoe UI Semibold", 9)).pack(side="left", padx=(10, 0))
    tk.Label(hist_frame, text="50", fg="#B0B0B5", bg=parent_ui.bg_color, font=("Segoe UI Semibold", 9)).pack(side="right", padx=(0, 10))

    global cv_hist, cv_ram, warn_lbl, ram_val_lbl
    cv_hist = tk.Canvas(hist_frame, width=canvas_w, height=canvas_h, bg=parent_ui.bg_color, bd=0, highlightthickness=0)
    cv_hist.pack(side="left", fill="x", expand=True, padx=5)
    def redraw_hist_slider():
        cv_hist.delete("all")
        ratio = (saved_vals['hist'] - 2) / (50 - 2)
        cx = padding_x + (ratio * track_w)
        cv_hist.create_line(cx, line_y, canvas_w - padding_x, line_y, fill=parent_ui.dark_box, width=4, capstyle="round")
        cv_hist.create_line(padding_x, line_y, cx, line_y, fill="#2196F3", width=4, capstyle="round")
        cv_hist.create_oval(cx - r_knob, line_y - r_knob, cx + r_knob, line_y + r_knob, fill="#2196F3", outline="#2196F3")

    def on_hist_action(event):
        x = max(padding_x, min(event.x, canvas_w - padding_x))
        ratio = (x - padding_x) / track_w
        saved_vals['hist'] = int(round(2 + (ratio * (50 - 2))))
        hist_val_lbl.config(text=str(saved_vals['hist']))
        redraw_hist_slider()

    cv_hist.bind("<Button-1>", on_hist_action)
    cv_hist.bind("<B1-Motion>", on_hist_action)
    redraw_hist_slider()

    # Слайдер 2: RAM в МБ
    tk.Label(settings_win, text="Доступный лимит RAM для Ollama", fg=parent_ui.text_color, bg=parent_ui.bg_color, font=("Segoe UI Semibold", 10)).pack(pady=(20, 2))
    ram_val_lbl = tk.Label(settings_win, text=f"{saved_vals['ram']} МБ", fg="#2196F3", bg=parent_ui.bg_color, font=("Segoe UI Semibold", 14))
    ram_val_lbl.pack()

    ram_frame = tk.Frame(settings_win, bg=parent_ui.bg_color)
    ram_frame.pack(fill="x", padx=20)
    tk.Label(ram_frame, text=f"{min_ram_mb} МБ", fg="#B0B0B5", bg=parent_ui.bg_color, font=("Segoe UI Semibold", 8)).pack(side="left", padx=(5, 0))
    tk.Label(ram_frame, text=f"{total_ram_mb} МБ", fg="#B0B0B5", bg=parent_ui.bg_color, font=("Segoe UI Semibold", 8)).pack(side="right", padx=(0, 5))

    cv_ram = tk.Canvas(ram_frame, width=canvas_w, height=canvas_h, bg=parent_ui.bg_color, bd=0, highlightthickness=0)
    cv_ram.pack(side="left", fill="x", expand=True, padx=5)

    warn_lbl = tk.Label(settings_win, text="", fg="#B0B0B5", bg=parent_ui.bg_color, font=("Segoe UI Italic", 9))
    warn_lbl.pack(pady=(5, 0))

    def redraw_ram_slider():
        cv_ram.delete("all")
        max_safe_ratio = (absolute_max_ram_mb - min_ram_mb) / (total_ram_mb - min_ram_mb)
        safe_limit_x = padding_x + (max_safe_ratio * track_w)
        ratio = (saved_vals['ram'] - min_ram_mb) / (total_ram_mb - min_ram_mb)
        cx = padding_x + (ratio * track_w)
        
        cv_ram.create_line(safe_limit_x, line_y, canvas_w - padding_x, line_y, fill="#40404C", width=4, capstyle="round")
        if cx < safe_limit_x:
            cv_ram.create_line(cx, line_y, safe_limit_x, line_y, fill=parent_ui.dark_box, width=4, capstyle="butt")
        cv_ram.create_line(padding_x, line_y, cx, line_y, fill="#2196F3", width=4, capstyle="round")
        cv_ram.create_oval(cx - r_knob, line_y - r_knob, cx + r_knob, line_y + r_knob, fill="#2196F3", outline="#2196F3")
        
        reserved_gb = (total_ram_mb - saved_vals['ram']) / 1024
        warn_lbl.config(text=f"Оставлено системе и AutoCAD: {reserved_gb:.2f} ГБ", fg="#B0B0B5")

    def on_ram_action(event):
        x = max(padding_x, min(event.x, canvas_w - padding_x))
        ratio = (x - padding_x) / track_w
        calculated_mb = int(round(min_ram_mb + (ratio * (total_ram_mb - min_ram_mb))))
        
        if calculated_mb > absolute_max_ram_mb:
            saved_vals['ram'] = absolute_max_ram_mb
            ram_val_lbl.config(text=f"{saved_vals['ram']} МБ")
            warn_lbl.config(text=f"🛑 Предохранитель! {required_reserve_mb} МБ удержано для защиты AutoCAD.", fg="#EF5350")
        else:
            saved_vals['ram'] = calculated_mb
            ram_val_lbl.config(text=f"{saved_vals['ram']} МБ")
        redraw_ram_slider()

    cv_ram.bind("<Button-1>", on_ram_action)
    cv_ram.bind("<B1-Motion>", on_ram_action)
    redraw_ram_slider()

    def save_and_close():
        # Сохраняем только настройки истории и памяти (самообучение удалено).
        parent_ui.save_settings(saved_vals['hist'], saved_vals['ram'])
        settings_win.destroy()

    btn_save = tk.Button(settings_win, text="Сохранить", bg="#2196F3", fg=parent_ui.text_color, font=("Segoe UI Semibold", 10),
                         bd=0, width=15, height=1, relief="flat", activebackground="#1E88E5", activeforeground=parent_ui.text_color,
                         command=save_and_close)
    btn_save.pack(pady=(15, 0), ipady=4)


# ========================================================================
# ИНСТРУМЕНТЫ БОРЬБЫ С ЗАЦИКЛИВАНИЕМ И СЕМАНТИЧЕСКОЙ КЛАССИФИКАЦИИ
# ========================================================================

# Маркеры, однозначно указывающие на ИНФОРМАЦИОННЫЙ (read-only) запрос:
# пользователю нужна строка/значение/свойство документа, а не геометрическое действие.
INFORMATIONAL_MARKERS = [
    "имя чертежа", "имя документа", "название чертежа", "название документа",
    "какое имя", "как называется", "что за чертеж", "что за чертёж",
    "документ называется", "чертеж называется", "чертёж называется",
    "сколько объектов", "количество объектов", "кол-во объектов", "число объектов",
    "типы объектов", "виды объектов", "список объектов", "какие объекты",
    "типы примитивов", "что начерчено", "какие примитивы",
    "свойства документа", "свойства чертежа", "текущий лист", "какой лист",
    "какая вкладка", "активный лист", "путь к чертежу", "путь к файлу",
    "полный путь", "где лежит", "имя файла", "размер файла",
]

def is_informational_query(user_query):
    """Определяет, является ли запрос информационным (чтение данных/свойств через COM),
    а не геометрической операцией (zoom, create, switch и т.п.)."""
    q = (user_query or "").lower()
    for marker in INFORMATIONAL_MARKERS:
        if marker in q:
            return True
    return False


# Маркер честного отказа: когда запрос выходит за пределы доступных команд,
# движок возвращает сообщение с этим префиксом, и система выводит его как
# финальный ответ ИИ (БЕЗ петли Self-Healing и без зацикливания).
UNSUPPORTED_PREFIX = "[ФУНКЦИЯ НЕДОСТУПНА]"

def is_unsupported_result(text):
    """Определяет, является ли результат движка честным отказом (не поддержка команды)."""
    return bool(text) and UNSUPPORTED_PREFIX in str(text)


# Маркеры текстовой обратной связи: пользователь уточняет, что прошлое действие
# было выполнено неверно / не так, как он имел в виду. Такие сообщения перехватываются
# и учитываются как исправление, а не как новый самостоятельный запрос.
FEEDBACK_MARKERS = [
    "ты сделал", "сделал не то", "не то", "неправильно", "не так", "не верно",
    "неверно", "исправь", "переделай", "я имел в виду", "совсем другое",
    "ты не понял", "ты ошибся", "ошибся", "не это", "не туда", "это не то",
    "я просил", "не надо было", "верни как было", "отмени", "ты не то",
    "каким образом", "каким способом", "как ты выполнил", "что ты сделал",
    "ты реально", "объясни, что", "объясни, как", "ничего не изменилось",
    "не выполнил", "не сработало", "не работает",
]

def is_feedback_query(user_query):
    """Определяет, является ли запрос обратной связью на предыдущее действие
    (пользователь сообщает, что результат был неверным и требует исправления)."""
    q = (user_query or "").lower()
    for marker in FEEDBACK_MARKERS:
        if marker in q:
            return True
    return False


# ========================================================================
# ВАЛИДАЦИЯ ПОЛНОТЫ ИНЖЕНЕРНОГО ЗАПРОСА (слой «зрячей логики»)
# ========================================================================
# Параметры, критически обязательные для геометрических транзакций. Если они
# отсутствуют в запросе, движок НЕ передаёт управление ни модели, ни песочнице,
# а возвращает уточняющий вопрос — тем самым исключая «пустой успех».
def validate_geometry_request(user_query) -> tuple:
    """Проверяет полноту параметров геометрической операции.

    Возвращает кортеж (полон_ли, текст_уточнения).
    Если запрос не является геометрической транзакцией — возвращает (True, "").
    """
    q = (user_query or "").lower()

    def _has_number(s):
        return bool(re.search(r"\d", s))

    is_move = any(w in q for w in [
        "перемести", "передвинь", "сдвинь", "смести", "перенеси", "перетащи",
        "переместить", "передвинуть", "сдвинуть", "сместить", "перенести",
        "сдвиг", "смещение", "перемещение",
    ])
    is_rotate = any(w in q for w in [
        "поверни", "повернуть", "вращай", "разверни", "поворот", "вращение",
    ])
    is_scale = any(w in q for w in [
        "масштабируй", "масштабировать", "увеличь в", "уменьши в",
        "измени масштаб", "изменить масштаб", "масштаб",
    ])

    if is_move:
        has_dir = any(w in q for w in [
            "вправо", "влево", "вверх", "вниз", "в право", "в лево",
            "в верх", "в низ", "по x", "по y", "по оси", "к точке", "до точки",
        ])
        has_amt = _has_number(q)
        if not has_dir and not has_amt:
            return False, ("Вы просите переместить объект, но не указали ни направление, "
                           "ни расстояние. Уточните, пожалуйста: куда и на сколько единиц переместить?")
        if has_amt and not has_dir:
            return False, ("Расстояние понял, но не хватает направления. Куда переместить объект: "
                           "вправо, влево, вверх или вниз?")
        if has_dir and not has_amt:
            return False, ("Направление понял, но не указана величина смещения. "
                           "На сколько единиц переместить объект?")

    if is_rotate and not _has_number(q):
        return False, "Не указан угол поворота. На сколько градусов повернуть объект?"

    if is_scale and not _has_number(q):
        return False, "Не указан коэффициент масштабирования. Во сколько раз изменить масштаб объекта?"

    return True, ""


# Навигационные признаки в теле кода: зум, переключение вкладки/листа, активация
# документа/вида легально НЕ изменяют примитивы, поэтому пустой affected_objects
# для них — нормальный результат, а не «пустой успех».
NAVIGATION_CODE_MARKERS = [
    "zoomextents", "zoomto", "zoomwindow", "zoomall",
    "activelayout", "activedoc", "activeviewport",
    ".activate(", "regen(", "setview",
]

def is_meaningful_success(contract, code="") -> bool:
    """Определяет, является ли контракт реально выполненным полезным действием.

    COMMAND с пустым списком affected_objects считается «пустым успехом», если
    ни в контракте, ни в коде нет навигационных признаков (зум/переключение вкладки).
    Такой результат НЕ рапортуется как «Сценарий успешно выполнен» и НЕ попадает
    в память опыта."""
    if not isinstance(contract, dict):
        return False
    if contract.get("transaction_type") == "COMMAND":
        if contract.get("affected_objects"):
            return True
        if contract.get("navigation"):
            return True
        low = (code or "").lower()
        return any(m in low for m in NAVIGATION_CODE_MARKERS)
    # QUERY с текстовым ответом считается осмысленным
    return bool(contract.get("data"))


class ScenarioLoopGuard:
    """Железобетонная защита от бесконечного цикла.

    Если один и тот же сценарий (например, 'zoom_all') генерируется 2 раза ПОДРЯД
    на один и тот же пользовательский промпт — guard сигнализирует о зацикливании.
    Потокобезопасен (используется в многопоточном движке выполнения).
    """

    def __init__(self, limit=2):
        self._limit = max(2, int(limit))
        self._lock = threading.Lock()
        self._last_command = {}   # prompt -> последняя команда
        self._counter = {}        # prompt -> счётчик повторов подряд

    def check_and_update(self, prompt, command):
        """Регистрирует очередной вызов команды для промпта.
        Возвращает True, если та же команда встретилась 'limit' раз подряд (зацикливание)."""
        if not prompt or not command:
            return False
        key = str(prompt).strip().lower()
        with self._lock:
            prev = self._last_command.get(key)
            if prev == command:
                cnt = self._counter.get(key, 0) + 1
            else:
                cnt = 1
            self._last_command[key] = command
            self._counter[key] = cnt
            return cnt >= self._limit

    def reset(self, prompt):
        """Полностью очищает состояние guard для указанного промпта."""
        if not prompt:
            return
        key = str(prompt).strip().lower()
        with self._lock:
            self._last_command.pop(key, None)
            self._counter.pop(key, None)


