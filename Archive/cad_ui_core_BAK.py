import tkinter as tk
from tkinter import ttk, scrolledtext
import os
import json
import psutil
import time

import cad_system 
import cad_ui_styles

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
HISTORY_FILE = os.path.join(BASE_DIR, "chat_history.json")

class CadAiAssistantUI:
    def __init__(self, root, on_submit_callback=None, on_vision_callback=None, on_reject_callback=None):
        self.root = root
        self.root.title("AutoCAD AI Assistant")
        self.root.geometry("520x780")
        self.root.minsize(450, 500)
        
        self.bg_color, self.dark_box, self.text_color = "#1E1E24", "#2A2A32", "#FFFFFF"
        self.current_user_prompt, self.start_time, self.timer_active = "", 0.0, False
        self.learning_enabled = True
        self.active_timer_line = 0
        # Контекст «Ножниц»: текстовое описание/координаты выделенной области экрана.
        # Заполняется после срабатывания инструмента выделения и передаётся в
        # route_request как аргумент scissors_context при отправке сообщения.
        self.scissors_context = None
        
        # Регистрация внешних сигналов управления (Декоплеры)
        self.on_submit = on_submit_callback
        self.on_vision = on_vision_callback
        self.on_reject = on_reject_callback
        
        self.max_history_len, self.max_ram_limit_mb = self.load_settings()
        self.history = self.load_chat_history()
        
        self.root.configure(bg=self.bg_color)
        self.root.attributes("-topmost", True)  
        cad_ui_styles.setup_styles(self.bg_color, self.dark_box, self.text_color)
        
        self.setup_ui()
        self.restore_chat_view()
        
        # Запускаем первый стартовый цикл проверки связи с AutoCAD
        self.update_active_drawing_status()

    def tick_live_timer_safely(self):
        """БЕЗОПАСНЫЙ СЧЕТЧИК: Работает строго в главном потоке графики"""
        if self.timer_active and self.active_timer_line > 0:
            elapsed = time.perf_counter() - self.start_time
            try:
                self.chat_area.configure(state="normal")
                self.chat_area.delete(f"{self.active_timer_line}.0", f"{self.active_timer_line}.end")
                self.chat_area.insert(f"{self.active_timer_line}.0", f" ⏱ Идет вычисление: {elapsed:.1f} сек.", "timer")
                self.chat_area.configure(state="disabled")
                self.chat_area.see(tk.END)
                self.root.after(100, self.tick_live_timer_safely)
            except: pass

    def start_live_timer(self):
        """Создает изолированную строку и запускает плавный отсчет секунд"""
        self.active_timer_line = self.log("", "timer")
        self.timer_active = True
        self.start_time = time.perf_counter()
        self.tick_live_timer_safely()

    def stop_live_timer(self):
        """Полностью останавливает часы транзакции"""
        self.timer_active = False

    def clear_chat_context(self):
        self.timer_active = False
        self.history, self.current_user_prompt = [], ""
        if os.path.exists(HISTORY_FILE):
            try: os.remove(HISTORY_FILE)
            except: pass
        self.chat_area.configure(state="normal")
        self.chat_area.delete("1.0", tk.END)
        self.chat_area.configure(state="disabled")
        self.input_entry.delete(0, tk.END)
        self.input_entry.focus_set()
        self.log("[Система]: ... Контекст памяти ИИ полностью сброшен. Директивы очищены.", "system")

    def load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    history_len = int(data.get("max_history_len", 10))
                    ram_limit_mb = int(data.get("max_ram_percent", 4096))
                    self.learning_enabled = bool(data.get("learning_enabled", True))
                    if ram_limit_mb <= 100:
                        ram_limit_mb = int((psutil.virtual_memory().total / (1024 * 1024)) * 0.25)
                    return history_len, ram_limit_mb
            except: pass
        return 10, 4096

    def save_settings(self, history_len, ram_limit_mb, learning_enabled=True):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "max_history_len": history_len, 
                    "max_ram_percent": ram_limit_mb,
                    "learning_enabled": learning_enabled
                }, f, ensure_ascii=False, indent=2)
            self.max_history_len, self.max_ram_limit_mb, self.learning_enabled = history_len, ram_limit_mb, learning_enabled
        except Exception as e: print(f"Ошибка сохранения настроек: {e}")

    def load_chat_history(self):
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f: return json.load(f)
            except: pass
        return []

    def save_chat_history(self):
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f: json.dump(self.history, f, ensure_ascii=False, indent=2)
        except Exception as e: print(f"Ошибка сохранения истории: {e}")

    def open_settings_window(self):
        cad_system.open_settings_window(self)

    def paste_from_clipboard(self):
        try:
            text = self.root.clipboard_get()
            if text: self.input_entry.insert(self.input_entry.index(tk.INSERT), text)
        except tk.TclError: pass

    def trigger_reject(self):
        """Обработчик кнопки «Плохое решение (Переделать кодом)».

        Собирает необязательный текстовый комментарий пользователя из поля ввода
        и передаёт сигнал неудовлетворённости в мозг (on_reject) для отбраковки кода."""
        feedback = self.input_entry.get().strip()
        if feedback:
            self.input_entry.delete(0, tk.END)
        if self.on_reject:
            # Передаём комментарий (или None, если просто нажали кнопку без текста)
            self.on_reject(self, feedback or None)
    def log(self, text, tag="ai"):
        """Печатает текст в чат и гарантированно возвращает точный номер строки, на которой он напечатан"""
        self.chat_area.configure(state="normal")
        self.chat_area.insert(tk.END, text + "\n", tag)
        
        # ИСПРАВЛЕНИЕ ОШЕПАТКИ С ИНДЕКСОМ: Берем строго первый элемент списка строк [0]
        end_idx = self.chat_area.index("end-1c")
        line_num = int(end_idx.split(".")[0])
        
        self.chat_area.configure(state="disabled")
        self.chat_area.see(tk.END)
        return line_num

    def restore_chat_view(self):
        if not self.history: return
        self.chat_area.configure(state="normal")
        for msg in self.history:
            role, content = msg.get("role"), msg.get("content")
            if "[ИНФОРМАЦИЯ: Активный файл" in content: content = content.split("Запрос: ")[-1]
            if role == "user": self.chat_area.insert(tk.END, f"\nВы: {content}\n", "user")
            elif role == "assistant" and not ("```python" in content or "import win32com" in content):
                self.chat_area.insert(tk.END, f"\nИИ: {content}\n", "ai")
        self.chat_area.configure(state="disabled")
        self.chat_area.see(tk.END)

    def update_active_drawing_status(self):
        """ФОНОВЫЙ ЦИКЛ САПР: Каждые 2 секунды опрашивает открытую вкладку AutoCAD"""
        try:
            import win32com.client
            acad_app = win32com.client.GetActiveObject("AutoCAD.Application")
            self.lbl_status.configure(text=f"● Connected: {acad_app.ActiveDocument.Name}", fg="#4CAF50")
        except Exception as ce:
            if "занят" in str(ce) or "Busy" in str(ce) or "-2147418111" in str(ce):
                self.lbl_status.configure(text="● AutoCAD Busy (Processing...)", fg="#FF9800")
            else: 
                self.lbl_status.configure(text="○ AutoCAD disconnected", fg="#EF5350")
        
        # Рекурсивный бесконечный перезапуск тика через 2000 миллисекунд
        self.root.after(2000, self.update_active_drawing_status)

    def setup_ui(self):
        top_frame = tk.Frame(self.root, bg=self.bg_color)
        top_frame.pack(fill="x", padx=20, pady=(20, 10))
        
        self.lbl_status = tk.Label(top_frame, text="Checking connection...", fg="#B0B0B5", bg=self.bg_color, font=("Segoe UI Semibold", 10))
        self.lbl_status.pack(side="left")
        
        tk.Button(top_frame, text="⚙", bg=self.bg_color, fg=self.text_color, font=("Segoe UI", 12), bd=0, activebackground=self.bg_color, activeforeground="#2196F3", command=self.open_settings_window).pack(side="right", padx=(10, 0))
        tk.Button(top_frame, text="🗑 Очистить", bg=self.bg_color, fg="#EF5350", font=("Segoe UI Semibold", 9), bd=0, activebackground=self.bg_color, activeforeground="#FF8A80", command=self.clear_chat_context).pack(side="right", padx=(10, 0))
        
        self.model_var = tk.StringVar(value="qwen3-coder:30b")
        model_combo = ttk.Combobox(top_frame, textvariable=self.model_var, state="readonly", width=18, font=("Segoe UI", 9))
        model_combo['values'] = ("qwen3-coder:30b", "qwen2.5-coder:32b", "qwen2.5-coder:7b")
        model_combo.pack(side="right")

        chat_container = tk.Frame(self.root, bg=self.bg_color, bd=0, highlightthickness=0)
        chat_container.pack(fill="both", expand=True, padx=20, pady=10)
        
        self.chat_canvas = tk.Canvas(chat_container, bg=self.bg_color, bd=0, highlightthickness=0)
        self.chat_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.chat_canvas.bind("<Configure>", lambda e: cad_ui_styles.draw_round_rect(self.chat_canvas, e.width, e.height, 14, self.dark_box, "round_chat"))
        
        self.chat_area = scrolledtext.ScrolledText(chat_container, wrap=tk.WORD, state="disabled", bg=self.dark_box, fg=self.text_color, bd=0, highlightthickness=0, font=("Segoe UI", 10))
        self.chat_area.pack(fill="both", expand=True, padx=12, pady=12)
        
        self.chat_area.tag_configure("user", foreground="#64B5F6", font=("Segoe UI Semibold", 10))
        self.chat_area.tag_configure("ai", foreground=self.text_color)
        self.chat_area.tag_configure("system", foreground="#B0B0B5", font=("Segoe UI Italic", 9))
        self.chat_area.tag_configure("timer", foreground="#00BCD4", font=("Consolas Italic", 10))
        self.chat_area.tag_configure("error_raw", foreground="#FF8A80", font=("Consolas", 9))

        action_panel = tk.Frame(self.root, bg=self.bg_color)
        action_panel.pack(fill="x", padx=20, pady=(0, 5))
        
        tk.Button(action_panel, text="🛑 Плохое решение (Переделать кодом)", bg=self.bg_color, fg="#EF5350",
                  font=("Segoe UI Semibold", 9), bd=0, activebackground=self.bg_color, activeforeground="#FF8A80",
                  command=self.trigger_reject).pack(side="left")

        bottom_frame = tk.Frame(self.root, bg=self.bg_color)
        bottom_frame.pack(fill="x", padx=20, pady=(0, 20))
        
        entry_wrapper = tk.Frame(bottom_frame, bg=self.bg_color, height=38)
        entry_wrapper.pack(side="left", fill="x", expand=True)
        entry_wrapper.pack_propagate(False)
        
        self.entry_canvas = tk.Canvas(entry_wrapper, bg=self.bg_color, bd=0, highlightthickness=0)
        self.entry_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.entry_canvas.bind("<Configure>", lambda e: cad_ui_styles.draw_round_rect(self.entry_canvas, e.width, e.height, 12, self.dark_box, "round_entry"))
        
        self.input_entry = tk.Entry(entry_wrapper, bg=self.dark_box, fg=self.text_color, insertbackground=self.text_color, bd=0, highlightthickness=0, font=("Segoe UI", 10))
        self.input_entry.pack(fill="x", padx=12, pady=9)
        
        tk.Button(bottom_frame, text="Буфер", bg="#3E3E4A", fg=self.text_color, font=("Segoe UI Semibold", 9), bd=0, command=self.paste_from_clipboard).pack(side="left", padx=(8, 2), ipady=5)
        
        tk.Button(bottom_frame, text="👁 Снимок", bg="#3E3E4A", fg=self.text_color, font=("Segoe UI Semibold", 9), bd=0, 
                  command=lambda: self.on_vision(self) if self.on_vision else None).pack(side="left", padx=2, ipady=5)
        
        tk.Button(bottom_frame, text="Ввод", bg="#2196F3", fg=self.text_color, font=("Segoe UI Semibold", 9), bd=0, width=12, 
                  command=lambda: self.on_submit(self) if self.on_submit else None).pack(side="right", padx=(2, 0), ipady=5)
        
        self.input_entry.bind("<Return>", lambda event: self.on_submit(self) if self.on_submit else None)


def _launch_application():
    """Запускает графический интерфейс приложения из этого файла.

    Создаёт корневое окно Tk, экземпляр UI и маршрутизатор, связывает их через
    main_router.attach_ui_callbacks() и запускает главный цикл обработки событий.
    Весь запуск обёрнут в try-except с выводом ошибки в консоль, чтобы причина
    падения была видна в cmd, а не молча проглатывалась.
    """
    # Ленивый импорт маршрутизатора выполняется здесь, чтобы избежать
    # циклической зависимости между UI и оркестратором.
    import main_router
    from main_router import MainRouter

    root = tk.Tk()
    app_ui = CadAiAssistantUI(root)
    router = MainRouter(app_ui)
    main_router.attach_ui_callbacks(app_ui, router)
    root.mainloop()


if __name__ == "__main__":
    # Точка входа при запуске `python cad_ui_core.py` из командной строки.
    try:
        _launch_application()
    except Exception as e:
        print(f" Ошибка при запуске интерфейса: {e}")
