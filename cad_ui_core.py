import tkinter as tk
from tkinter import scrolledtext
import os
import json
import psutil
import time

import cad_system 
import cad_ui_styles

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
HISTORY_FILE = os.path.join(BASE_DIR, "chat_history.json")


class CanvasModernDropButton(tk.Canvas):
    """Кастомная скруглённая кнопка выбора агента в стиле Windows 11.

    Полностью отрисована на tkinter.Canvas: чистый холст без системных рамок
    (highlightthickness=0, bd=0), скруглённый прямоугольник с плавными углами
    (радиус 10px) через полигон со сглаживанием, белый текст агента и белая
    галочка-уголок ˅ справа. Эффект Hover меняет цвет фона на более светлый
    графит, клик вызывает переданную команду (показ меню выбора агентов).
    """

    def __init__(self, parent, text, command, **kwargs):
        # Инициализируем чистый холст без системных рамок
        super().__init__(parent, highlightthickness=0, bd=0, bg=parent["bg"],
                         cursor="hand2", **kwargs)
        self.text = text
        self.command = command
        self.base_color = "#2d2f34"
        self.hover_color = "#3d4046"

        # Размеры кнопки берутся из конфигурации Canvas
        self.bind("<Configure>", self._draw)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", lambda e: self.command())

    def _draw(self, event=None):
        """Рисует скруглённый прямоугольник, белый текст и уголок-галочку."""
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        r = 10  # Радиус скругления углов по стандарту Windows 11

        # Математическое рисование идеального скруглённого прямоугольника.
        points = [r, 0, w - r, 0, w, 0, w, r, w, h - r, w, h,
                  w - r, h, r, h, 0, h, 0, h - r, 0, r, 0, 0]
        self.btn_shape = self.create_polygon(
            points, fill=self.base_color, smooth=True, splinesteps=32)

        # Выводим белый текст агента по центру со смещением влево.
        self.create_text(15, h / 2, text=self.text, fill="white",
                         font=("Segoe UI", 10), anchor="w")
        # Рисуем изящный уголок-галочку ˅ справа.
        self.create_text(w - 20, h / 2, text=" ˅ ", fill="white",
                         font=("Segoe UI", 10, "bold"), anchor="center")

    def _on_enter(self, event):
        """При наведении меняет цвет фигуры на более светлый графит."""
        self.itemconfig(self.btn_shape, fill=self.hover_color)

    def _on_leave(self, event):
        """При уходе мыши возвращает исходный цвет фигуры."""
        self.itemconfig(self.btn_shape, fill=self.base_color)

    def set_text(self, new_text):
        """Обновляет текст выбранного агента и перерисовывает кнопку."""
        self.text = new_text
        self._draw()


class RoundedButton:
    """Кастомная скруглённая кнопка, отрисованная на tk.Canvas.

    Обеспечивает аккуратное скругление углов (8px), недоступное в стандартном
    tk.Button, без тяжёлой библиотеки customtkinter. Используется для кнопок
    «Буфер», «Снимок» и «Ввод» в нижней панели ввода.
    """

    CORNER = 8              # радиус скругления углов (px)
    HEIGHT = 36             # высота кнопки (px)

    def __init__(self, master, text, command, bg, fg, width=86, side="left"):
        """Создаёт скруглённую кнопку.

        Аргументы:
            master: родительский контейнер.
            text: текст на кнопке.
            command: вызываемая функция при клике.
            bg: базовый цвет фона кнопки.
            fg: цвет текста.
            width: ширина кнопки (px).
            side: сторона упаковки (left/right) внутри контейнера.
        """
        self.text = text
        self.command = command
        self.bg = bg
        self.fg = fg
        self.width = width

        self.canvas = tk.Canvas(master, width=width, height=self.HEIGHT,
                                bg=master["bg"], highlightthickness=0, bd=0)
        self.canvas.pack(side=side, padx=4)
        self.canvas.bind("<Button-1>", lambda e: self._click())
        self._redraw()

    def _click(self):
        """Вызывает зарегистрированную команду при нажатии."""
        if self.command:
            self.command()

    def _redraw(self):
        """Рисует скруглённый прямоугольник с текстом по центру."""
        c = self.canvas
        w, h = self.width, self.HEIGHT
        r = self.CORNER
        c.delete("all")
        pts = [r, 0, w - r, 0, w, 0, w, r, w, h - r, w, h,
               w - r, h, r, h, 0, h, 0, h - r, 0, r, 0, 0]
        c.create_polygon(pts, fill=self.bg, smooth=True)
        c.create_text(w / 2, h / 2, text=self.text, fill=self.fg,
                      font=("Segoe UI Semibold", 10))


class CadAiAssistantUI:
    def __init__(self, root, on_submit_callback=None, on_vision_callback=None):
        self.root = root
        self.root.title("AutoCAD AI Assistant")
        self.root.geometry("520x780")
        self.root.minsize(450, 500)
        
        self.bg_color, self.dark_box, self.text_color = "#1E1E24", "#2A2A32", "#FFFFFF"
        self.current_user_prompt, self.start_time, self.timer_active = "", 0.0, False
        self.active_timer_line = 0
        # Контекст «Ножниц»: текстовое описание/координаты выделенной области экрана.
        # Заполняется после срабатывания инструмента выделения и передаётся в
        # route_request как аргумент scissors_context при отправке сообщения.
        self.scissors_context = None
        
        # Регистрация внешних сигналов управления (Декоплеры)
        self.on_submit = on_submit_callback
        self.on_vision = on_vision_callback
        
        self.max_history_len, self.max_ram_limit_mb = self.load_settings()
        self.history = self.load_chat_history()
        
        self.root.configure(bg=self.bg_color)
        self.root.attributes("-topmost", True)  
        cad_ui_styles.setup_styles(self.bg_color, self.dark_box, self.text_color)
        
        self.setup_ui()
        self.restore_chat_view()
        # Синхронизируем показ приветствия с наличием истории чата.
        self._sync_welcome()
        
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
        # После очистки окно чата снова показывает приветственный блок.
        self.show_welcome()

    def _submit(self):
        """Отправляет запрос: сначала скрывает приветствие, затем вызывает колбэк.

        Приветственный блок мгновенно исчезает, уступая место обычному логу
        сообщений чата и секундомеру, как только пользователь нажимает «Ввод».
        """
        self.hide_welcome()
        if self.on_submit:
            self.on_submit(self)

    def show_welcome(self):
        """Показывает отцентрированный приветственный блок поверх пустого чата."""
        if getattr(self, "welcome_frame", None) is not None:
            self.welcome_frame.place(relx=0.5, rely=0.5, anchor="center")

    def hide_welcome(self):
        """Скрывает приветственный блок, освобождая место для лога чата."""
        if getattr(self, "welcome_frame", None) is not None:
            self.welcome_frame.place_forget()

    def _sync_welcome(self):
        """Синхронизирует показ приветствия с наличием истории чата.

        Если история пуста — блок показывается, иначе (есть сообщения) — скрыт.
        """
        if self.history:
            self.hide_welcome()
        else:
            self.show_welcome()

    def load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    history_len = int(data.get("max_history_len", 10))
                    ram_limit_mb = int(data.get("max_ram_percent", 4096))
                    if ram_limit_mb <= 100:
                        ram_limit_mb = int((psutil.virtual_memory().total / (1024 * 1024)) * 0.25)
                    return history_len, ram_limit_mb
            except: pass
        return 10, 4096

    def save_settings(self, history_len, ram_limit_mb):
        """Сохраняет настройки истории и лимита памяти в конфигурационный файл.

        Аргументы:
            history_len: максимальная длина истории чата.
            ram_limit_mb: максимальный лимит оперативной памяти в мегабайтах.
        """
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "max_history_len": history_len,
                    "max_ram_percent": ram_limit_mb,
                }, f, ensure_ascii=False, indent=2)
            self.max_history_len, self.max_ram_limit_mb = history_len, ram_limit_mb
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

    def show_agent_menu(self):
        """Показывает стилизованное под тёмную тему меню выбора агентов.

        Клик по Canvas-кнопке вызывает это стандартное tk.Menu, оформленное в
        той же графитовой палитре, что и сама кнопка. При выборе агента текст
        на кнопке обновляется через set_text(), а agent_var фиксирует выбор.
        """
        # Стандартное меню, оформленное под тёмную тему (без белых уголков).
        menu = tk.Menu(self.root, tearoff=0,
                       bg="#2d2f34", fg="#FFFFFF",
                       activebackground="#3d4046", activeforeground="#FFFFFF",
                       bd=1, relief="solid", font=("Segoe UI", 10))
        for name in ("Ассистент-Чертёжник", "Ассистент-Аналитик",
                     "Ассистент-Оператор", "Ассистент-Справочник"):
            menu.add_radiobutton(
                label=name, value=name, variable=self.agent_var,
                command=lambda n=name: self._select_agent(n))
        # Показываем меню у левого нижнего края кнопки.
        x = self.agent_button.winfo_rootx()
        y = self.agent_button.winfo_rooty() + self.agent_button.winfo_height()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _select_agent(self, name):
        """Обновляет текст выбранного агента на Canvas-кнопке."""
        self.agent_button.set_text(name)

    def paste_from_clipboard(self):
        try:
            text = self.root.clipboard_get()
            if text: self.input_entry.insert(self.input_entry.index(tk.INSERT), text)
        except tk.TclError: pass

    def _redraw_entry(self):
        """Перерисовывает скруглённую подложку поля ввода (заливка + рамка).

        Заливка всегда отрисовывается в глубоком графите dark_box, а тонкая
        тёмно-серая рамка появляется только когда поле находится в фокусе.
        """
        w = self.entry_canvas.winfo_width()
        h = self.entry_canvas.winfo_height()
        if w <= 1 or h <= 1:
            return
        cad_ui_styles.draw_round_rect(self.entry_canvas, w, h, 10, self.dark_box, "round_entry")
        if self._entry_focused:
            cad_ui_styles.draw_round_rect_outline(
                self.entry_canvas, w, h, 10, "#5A6270", "round_entry_outline")
        else:
            self.entry_canvas.delete("round_entry_outline")

    def _set_entry_focus(self, active):
        """Включает/выключает рамку фокуса у поля ввода и перерисовывает его."""
        self._entry_focused = active
        self._redraw_entry()

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
        
        # Список доступных агентов (лаконичные названия для конечного пользователя).
        # Кнопка выбора агента — чистый Canvas (Fluent), клик открывает тёмное
        # меню, поскольку стандартный tk.OptionMenu не умеет скруглять углы.
        agent_row = tk.Frame(self.root, bg=self.bg_color)
        agent_row.pack(fill="x", padx=20, pady=(0, 10))

        self.agent_var = tk.StringVar(value="Ассистент-Чертёжник")
        # Кнопка выбора агента — чистый Canvas без системных рамок (Fluent).
        self.agent_button = CanvasModernDropButton(
            agent_row, text="Ассистент-Чертёжник",
            command=self.show_agent_menu, width=220, height=36,
        )
        self.agent_button.pack(side="left")

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

        # Приветственный блок (Welcome Screen) для пустого окна чата.
        # Отображается по центру текстовой области и помогает пользователю
        # сориентироваться при старте программы или после очистки истории.
        self.welcome_frame = tk.Frame(chat_container, bg=self.dark_box)
        welcome_title = tk.Label(
            self.welcome_frame, text="Привет! Я твой ИИ-Ассистент AutoCAD",
            bg=self.dark_box, fg="#E6E6EA", font=("Segoe UI Semibold", 14),
            justify="center",
        )
        welcome_title.pack(pady=(0, 10))
        welcome_sub = tk.Label(
            self.welcome_frame,
            text="Выбери нужного агента вверху и задай задачу. Например:",
            bg=self.dark_box, fg="#9AA0AA", font=("Segoe UI", 10),
            justify="center",
        )
        welcome_sub.pack(pady=(0, 14))
        # Примеры запросов для разных агентов (мелкий аккуратный шрифт).
        examples = (
            "• «Начерти круг радиусом 50 в точке 0,0» — для Чертёжника",
            "• «Покажи точки пересечения этих прямых» — для Аналитика",
            "• «Поменяй цвет слою на зеленый» — для Оператора",
        )
        for line in examples:
            tk.Label(self.welcome_frame, text=line, bg=self.dark_box,
                     fg="#7C828D", font=("Segoe UI", 9), justify="left"
                     ).pack(anchor="w", pady=2)

        bottom_frame = tk.Frame(self.root, bg=self.bg_color)
        bottom_frame.pack(fill="x", padx=20, pady=(0, 20))
        
        entry_wrapper = tk.Frame(bottom_frame, bg=self.bg_color, height=38)
        entry_wrapper.pack(side="left", fill="x", expand=True)
        entry_wrapper.pack_propagate(False)
        
        self.entry_canvas = tk.Canvas(entry_wrapper, bg=self.bg_color, bd=0, highlightthickness=0)
        self.entry_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self._entry_focused = False
        self.entry_canvas.bind("<Configure>", lambda e: self._redraw_entry())

        self.input_entry = tk.Entry(entry_wrapper, bg=self.dark_box, fg=self.text_color, insertbackground=self.text_color, bd=0, highlightthickness=0, font=("Segoe UI", 10))
        self.input_entry.pack(fill="x", padx=12, pady=9)
        # Лёгкая тёмно-серая рамка поля ввода, сигнализирующая о фокусе.
        self.input_entry.bind("<FocusIn>", lambda e: self._set_entry_focus(True))
        self.input_entry.bind("<FocusOut>", lambda e: self._set_entry_focus(False))
        
        # Кнопки нижней панели ввода на кастомном Canvas со скруглением 8px.
        # «Ввод» — приглушённый благородный неоново-синий (#1a73e8), а не
        # ядовито-синий, чтобы не выбиваться из дорогой тёмной темы.
        RoundedButton(bottom_frame, "Буфер", self.paste_from_clipboard,
                      bg="#3E3E4A", fg=self.text_color, width=80)
        RoundedButton(bottom_frame, "👁 Снимок",
                      lambda: self.on_vision(self) if self.on_vision else None,
                      bg="#3E3E4A", fg=self.text_color, width=90)
        RoundedButton(bottom_frame, "Ввод", self._submit,
                      bg="#1a73e8", fg=self.text_color, width=120, side="right")
        
        self.input_entry.bind("<Return>", lambda event: self._submit())


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
