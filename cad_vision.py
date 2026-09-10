import base64
import time
from io import BytesIO
from typing import Optional, Tuple

import tkinter as tk
from PIL import ImageGrab
import win32gui


def _find_cad_window():
    """Находит главное окно AutoCAD.

    Возвращает словарь с дескриптором окна (hwnd) и прямоугольником графического
    пространства (bbox) формата (left, top, right, bottom), отсекая ленту
    инструментов сверху (~140px) и боковые рамки."""
    result = {"hwnd": None, "bbox": None}

    def enum_windows_callback(hwnd, extra):
        window_text = win32gui.GetWindowText(hwnd)
        if "AutoCAD" in window_text and win32gui.IsWindowVisible(hwnd):
            rect = win32gui.GetWindowRect(hwnd)
            result["hwnd"] = hwnd
            # Отсекаем ленту инструментов сверху (140px) и боковые рамки
            result["bbox"] = (rect[0] + 10, rect[1] + 140, rect[2] - 10, rect[3] - 40)

    win32gui.EnumWindows(enum_windows_callback, None)
    return result


def get_cad_viewport_bbox():
    """Возвращает прямоугольник графического пространства AutoCAD.

    Сохранено для обратной совместимости; рекомендуется использовать
    _find_cad_window() для доступа к дескриптору окна."""
    return _find_cad_window()["bbox"]


def _normalize_bbox(bbox):
    """Приводит координаты к валидному виду (left, top, right, bottom).

    Нормализует инвертированные значения (когда right <= left или bottom <= top,
    что бывает из-за DPI), а при вырожденной геометрии принудительно задаёт
    минимальный размер кадра. Возвращает None, если bbox нельзя интерпретировать."""
    try:
        left, top, right, bottom = (int(v) for v in bbox)
    except Exception:
        return None

    # Корректируем перевёрнутую геометрию: меняем местами пары осей
    if left > right:
        left, right = right, left
    if top > bottom:
        top, bottom = bottom, top

    # Если ширина/высота всё равно нулевые или отрицательные — принудительный размер
    if right - left < 2:
        right = left + 800
    if bottom - top < 2:
        bottom = top + 600

    return (left, top, right, bottom)


def capture_and_encode_base64():
    """Делает точечный снимок чертежа и кодирует его в строку base64 для API.

    Защищена от ValueError 'Coordinate lower is less than upper':
    1. Если окно AutoCAD свёрнуто — восстанавливает и активирует его, затем ждёт
       0.3 сек, пока Windows отрисует развернутое окно.
    2. Нормализует bbox (инвертированные координаты из-за DPI / свёрнутого окна).
    3. Оборачивает ImageGrab.grab(bbox=...) в try...except и при сбое делает
       резервный захват всего экрана, чтобы поток не падал."""
    window_info = _find_cad_window()
    hwnd = window_info.get("hwnd")

    # ---- 1. Проверка состояния окна: если свёрнуто — разворачиваем и активируем ----
    try:
        if hwnd and win32gui.IsIconic(hwnd):
            print("⚠️ Окно AutoCAD свёрнуто. Разворачиваю и активирую перед снимком...")
            win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.3)  # ждём, пока Windows дорисует окно
    except Exception as e:
        print(f"⚠️ Не удалось восстановить окно AutoCAD: {e}")

    # ---- 2. Валидация и нормализация координат bbox ----
    bbox = _normalize_bbox(window_info.get("bbox"))

    # ---- 3. Безопасная обёртка захвата с резервным вариантом ----
    try:
        screenshot = ImageGrab.grab(bbox=bbox) if bbox else ImageGrab.grab()
    except Exception as e:
        print(f"⚠️ Ошибка захвата по координатам {bbox}: {e}. Делаю снимок всего экрана...")
        screenshot = ImageGrab.grab()

    buffered = BytesIO()
    screenshot.save(buffered, format="JPEG", quality=85)
    return base64.b64encode(buffered.getvalue()).decode('utf-8')


class ScreenSnipper:
    """Прозрачный полноэкранный оверлей для ручного выделения области.

    ВАЖНО: в Tkinter НЕЛЬЗЯ одновременно использовать overrideredirect(True) и
    attributes("-fullscreen", True) — это вызывает TclError
    'can't set fullscreen attribute for ".": override-redirect flag is set'.
    Поэтому размер окна на весь экран задаётся ПРИНУДИТЕЛЬНО через geometry()
    по фактическому разрешению пользователя (winfo_screenwidth/height).

    Аналог штатной функции Windows «Ножницы»: пользователь зажимает левую кнопку
    мыши, тянет рамку и отпускает — оверлей закрывается, а координаты выделенной
    области сохраняются."""

    def __init__(self) -> None:
        self._root = tk.Tk()
        # Скрываем рамки окна; размер на весь экран задаём геометрией ниже
        self._root.overrideredirect(True)

        # Принудительная геометрия на весь экран (без "-fullscreen")
        self._screen_w = self._root.winfo_screenwidth()
        self._screen_h = self._root.winfo_screenheight()
        self._root.geometry(f"{self._screen_w}x{self._screen_h}+0+0")

        # Базовое затемнение всего экрана на 30% (мягкая маска)
        self._root.attributes("-alpha", 0.3)
        self._root.attributes("-topmost", True)
        self._root.configure(bg="black")

        # «Цвет-невидимка»: всё, что закрашено этим цветом, Windows физически
        # «прорежет» насквозь до рабочего стола — там будет 100% исходная яркость.
        self._transparent_color: str = "#FF00FF"
        try:
            self._root.attributes("-transparentcolor", self._transparent_color)
        except Exception:
            pass  # если ключ не поддержан системой — остаёмся с базовым затемнением

        # Холст заполняет все 100% окна; собственный фон чёрный, системных рамок нет
        self._canvas = tk.Canvas(
            self._root,
            bg="black",
            highlightthickness=0,
            cursor="crosshair",
        )
        self._canvas.pack(fill="both", expand=True)

        self._x1: int = 0
        self._y1: int = 0
        self._x2: int = 0
        self._y2: int = 0
        self._rect_id: Optional[int] = None
        self._bbox: Optional[Tuple[int, int, int, int]] = None

        # Три события мыши: клик, движение с зажатой кнопкой, отпускание
        self._canvas.bind("<ButtonPress-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        # Esc — отмена выделения
        self._root.bind("<Escape>", self._on_cancel)

    def _close(self) -> None:
        """Мягко скрывает оверлей с экрана и останавливает локальный цикл событий.

        ВАЖНО: метод НЕ вызывает self._root.destroy(), так как принудительное
        уничтожение общего Tcl/Tk-контекста прямо здесь может вызвать каскадный
        «тихий вылет» всего приложения. Физическое удаление окна (.destroy())
        выполняется позже — в finally функции run_snipper_select()."""
        try:
            self._root.withdraw()  # Мягко скрываем оверлей с экрана
            self._root.quit()      # Останавливаем локальный mainloop()
        except Exception:
            pass

    def _on_press(self, event) -> None:
        """Фиксирует начальную координату (x1, y1) и создаёт «прозрачное окно» яркости.

        Прямоугольник заливается цветом-невидимкой #FF00FF: Windows делает такие
        пиксели полностью прозрачными, поэтому внутри рамки виден чистый экран
        с исходной яркостью. По краю остаётся тонкая синяя рамка-контур."""
        self._x1 = self._x2 = int(event.x)
        self._y1 = self._y2 = int(event.y)
        self._rect_id = self._canvas.create_rectangle(
            self._x1, self._y1, self._x2, self._y2,
            outline="#2196F3", width=2, fill="#FF00FF",
        )

    def _on_drag(self, event) -> None:
        """При движении мыши просто обновляет координаты «прозрачного окна».

        Никаких пересчётов 4-х прямоугольников и очисток холста — обычное
        обновление coords работает молниеносно и без мерцания."""
        self._x2 = int(event.x)
        self._y2 = int(event.y)
        if self._rect_id is not None:
            self._canvas.coords(self._rect_id, self._x1, self._y1, self._x2, self._y2)

    def _on_release(self, event) -> None:
        """Фиксирует конечную координату, нормализует прямоугольник и закрывает оверлей."""
        self._x2 = int(event.x)
        self._y2 = int(event.y)
        left = min(self._x1, self._x2)
        top = min(self._y1, self._y2)
        right = max(self._x1, self._x2)
        bottom = max(self._y1, self._y2)
        self._bbox = (left, top, right, bottom)
        self._close()

    def _on_cancel(self, event) -> None:
        """Отменяет выделение (без результата) и закрывает оверлей."""
        self._bbox = None
        self._close()

    def run(self) -> None:
        """Поднимает оверлей на передний план и запускает цикл обработки событий."""
        self._root.lift()
        self._root.focus_force()
        self._root.mainloop()

    def get_bbox(self) -> Optional[Tuple[int, int, int, int]]:
        """Возвращает выделенный прямоугольник (left, top, right, bottom) или None."""
        return self._bbox


def run_snipper_select() -> Optional[Tuple[int, int, int, int]]:
    """Запускает режим «Ножницы»: ждёт, пока пользователь выделит область мышью.

    Возвращает нормализованный кортеж (left, top, right, bottom) либо None,
    если выделение было отменено (Esc).

    Физическое уничтожение окна (.destroy()) выполняется СТРОГО в блоке finally,
    уже после безопасного извлечения координат. Это исключает каскадный
    «тихий вылет» интерфейса из-за удаления общего Tcl/Tk-контекста внутри
    обработчиков мыши."""
    snipper: Optional[ScreenSnipper] = None
    try:
        snipper = ScreenSnipper()
        snipper.run()
        return snipper.get_bbox()
    except Exception as e:
        print(f"⚠️ Ошибка режима «Ножницы»: {e}")
        return None
    finally:
        # Физически очищаем память Tcl/Tk только здесь, после безопасного извлечения bbox
        if snipper is not None:
            try:
                snipper._root.destroy()
            except Exception:
                pass


def grab_region_base64(bbox) -> Optional[str]:
    """Захватывает выделенную область экрана и кодирует её в Base64.

    Нормализует координаты, при сбое делает резервный захват всего экрана.
    Возвращает None, если снимок получить не удалось."""
    bbox = _normalize_bbox(bbox)
    try:
        screenshot = ImageGrab.grab(bbox=bbox) if bbox else ImageGrab.grab()
    except Exception as e:
        print(f"⚠️ Ошибка захвата области {bbox}: {e}. Делаю снимок всего экрана...")
        try:
            screenshot = ImageGrab.grab()
        except Exception as e2:
            print(f"⚠️ Ошибка захвата всего экрана: {e2}")
            return None

    buffered = BytesIO()
    screenshot.save(buffered, format="JPEG", quality=85)
    return base64.b64encode(buffered.getvalue()).decode('utf-8')
