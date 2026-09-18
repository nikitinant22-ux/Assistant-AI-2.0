# -*- coding: utf-8 -*-
"""Парящий интерфейс Fluent Design на классическом QMainWindow.

Часть 1: Импорты, спойлер рассуждений и строки сообщений чата.
"""

from __future__ import annotations
import json
import os
import re
import time
from html import escape
from typing import Optional
from PyQt6.QtCore import (
    QByteArray, QEasingCurve, QEvent, QPoint, QPropertyAnimation,
    QRect, QRectF, QSize, Qt, QThread, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QCursor, QColor, QFontMetricsF, QGuiApplication, QIcon,
    QPainter, QPainterPath, QPalette, QPen, QPixmap,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRubberBand,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    FluentIcon, IconWidget, InfoBar, InfoBarPosition, ProgressRing,
    SegmentedWidget, SmoothScrollArea, ToolButton, setTheme, Theme
)
import cad_ui_styles as styles

# Ядро связи: одномодельный фоновый поток стриминга llama-server, разбор блока
# рассуждений <thinking>, прогрев кэша лимитов контекста (n_ctx) и оценка
# токенов для статус-бара капсулы. Старая цепочка DeepSeek/Mistral ликвидирована.
from core_core import (
    LlamaWorker, estimate_tokens, fetch_server_limits, get_cached_n_ctx,
    prewarm_server_limits, split_thinking,
)
# Пакет атомарных кубиков САПР «пакет пакетов» (Этап 6, Шаг 1). Единый
# сквозной импорт: автосканер tools/__init__.py при старте приложения
# загружает все кубики из подпапок и регистрирует их в реестре. Мост
# ensure_connection() опрашивается фоновым потоком CadStatusPoller для
# светодиода подключения и имени активного DWG-чертежа (Зона 3 капсулы).
import tools
# Единая точка входа логики ИИ-суждений (Канон 11.1) — стерильный роутер.
# execute_tool_commands — исполнительный контур: по завершении стриминга
# извлекает JSON-команды из ответа модели и вызывает кубики через реестр.
from main_router import (
    CAD_FUSE_MARKER, execute_tool_commands, route_request,
    set_cad_connection_state,
)
# Локальный менеджер сессий чатов: чтение/запись JSON-файлов в папке history/
# корня проекта, автоименование новых диалогов по первому запросу (Этап 3).
from history_manager import HistoryManager


# Порог «прилипания» чата к нижнему краю (в пикселях): пока пользователь не
# прокрутил ленту вверх дальше этого расстояния, чат автоматически следует за
# стримом ИИ вниз. Как только ручная прокрутка уводит ползунок за порог —
# автоследование отключается, чтобы чтение истории не прерывалось рывками.
CHAT_SCROLL_PIN_THRESHOLD_PX: int = 100

# Период фонового опроса связи с AutoCAD для светодиода и имени чертежа (сек).
# Опрос живёт в отдельном потоке, поэтому даже при потерянном COM-указателе
# интерфейс не зависает: повторное подключение выполняется за кадром.
CAD_STATUS_POLL_INTERVAL_SEC: float = 3.0

# Регулярное выражение блока скрытых размышлений Qwen3 (без учёта регистра,
# с допуском пробелов вокруг имени тега и DOTALL для многострочного черновика).
# Используется функцией clean_response_for_history при записи реплик ассистента
# в память чата — черновик мыслей намертво вырезается из истории (Пока-ёкэ
# зацикливания), а JSON-пакеты команд и текстовые итоги остаются нетронутыми.
_THINK_TAGS_RE = re.compile(
    r"<\s*thinking\s*>.*?<\s*/\s*thinking\s*>",
    re.IGNORECASE | re.DOTALL,
)


def clean_response_for_history(raw_text: str) -> str:
    """Стерилизует ответ ассистента перед записью в память чата (Пока-ёкэ).

    Намертво вырезает всё, что находится внутри тегов <thinking>...</thinking>,
    чтобы при повторной подаче истории в контекст llama-server модель не
    «зацикливалась» на собственном черновике рассуждений. Прошлые JSON-блоки
    команд кубиков и текстовые итоги при этом остаются в строке нетронутыми —
    модель сохраняет контекст прошлых геометрических построений САПР.

    Аргументы:
        raw_text: сырой текст ответа ассистента (может содержать теги мыслей).

    Возвращает:
        str — очищенный текст, пригодный для сохранения в chat_history.
    """
    clean_text = _THINK_TAGS_RE.sub("", raw_text)
    return clean_text.strip()


def load_svg_icon(
    filename: str, color: str = "#e8e8e8", size: int = 24
) -> Optional[QIcon]:
    """Загружает SVG-иконку из папки icons.

    Иконки Fluent используют fill="currentColor", которую Qt отрисует чёрным,
    поэтому currentColor программно подменяется на светлый цвет (по умолчанию
    #e8e8e8), чтобы иконка была видна на тёмном фоне интерфейса.

    size — разрешение результирующего пиксмапа. Для крупных кнопок передавай
    целевой размер, чтобы иконка рендерилась чётко, а не растягивалась с 24px.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            svg = f.read().replace("currentColor", color)
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        return QIcon(pixmap)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Мини-конвертер Markdown → HTML для QLabel.
#
# Модель пишет разметку звёздочками (**полужирный**, *курсив*), заголовки ###,
# списки, цитаты и таблицы, а QLabel сам её не понимает — поэтому превращаем
# её в подмножество HTML, которое Qt умеет рендерить как Rich Text
# (Канон 16.5: пояснения на русском, код — ASCII).
# ---------------------------------------------------------------------------

_BLOCK_RE = re.compile(r"```(\w*)\n?(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_ITALIC_RE = re.compile(r"\*\*\*(?!\s)(.+?)(?<!\s)\*\*\*", re.DOTALL)
_BOLD_RE = re.compile(r"\*\*(?!\s)(.+?)(?<!\s)\*\*", re.DOTALL)
_ITALIC_RE = re.compile(r"(?<!\w)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\w)")
_STRIKE_RE = re.compile(r"(?<!\w)~~(?!\s)(.+?)(?<!\s)~~(?!\w)", re.DOTALL)
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s\"]+)\)")
_PLACEHOLDER_RE = re.compile(r"\x01(BLOCK|CODE)(\d+)\x01")

# Структурная разметка на уровне строк. Проверяется ДО применения звёздочек,
# чтобы маркеры списков (-, *, +) не путались с курсивом, а | в таблицах —
# с обычным текстом. Инлайн-код к этому моменту уже спрятан в плейсхолдеры
# \x01CODE..\x01, поэтому трубы внутри `кода` таблицы не ломают.
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_HR_RE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")
_QUOTE_RE = re.compile(r"^>\s?(.*)$")
_TASK_RE = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(.+)$")
_UL_ITEM_RE = re.compile(r"^\s*[-*+]\s+(.+)$")
_OL_ITEM_RE = re.compile(r"^\s*(\d+)[.)]\s+(.+)$")
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")


def _split_table_row(line: str) -> list[str]:
    """Делит строку таблицы |a|b|c| на ячейки (обрезает пробелы)."""
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def md_to_html(text: str) -> str:
    """Преобразует подмножество Markdown в HTML для QLabel (Rich Text).

    Поддерживается то, что модель реально использует в ответах:

    * **жирный текст**, *курсив*, ***жирный курсив***, ~~зачёркнутый~~
    * [текст](https://...)
    * `инлайн-код` и ```блок кода``` (JSON и т.п.)
    * # .. ###### заголовки, --- горизонтальные черты
    * - * + ненумерованные списки, 1. нумерованные, - [x] чек-листы
    * > цитаты и |...| таблицы

    Весь остальной текст экранируется от HTML, поэтому «сырые» теги модели
    (например, `<фигура>`) не ломают вёрстку, а показываются как обычный текст.
    Внутри блоков и инлайн-кода звёздочки НЕ превращаются в разметку.
    """
    if not text:
        return ""

    code_blocks: list[str] = []

    def _capture(m: re.Match) -> str:
        # Первый проход: блочный код ```...``` изолируем плейсхолдером.
        body = m.group(2).rstrip("\n")
        code_blocks.append(body)
        return f"\x01BLOCK{len(code_blocks) - 1}\x01"

    text = _BLOCK_RE.sub(_capture, text)

    # Инлайн-разметка применяется ПОСЛЕ разбора структуры, к «сырому»
    # содержимому каждого блока: экранируем HTML, прячем `инлайн-код`,
    # затем подставляем звёздочки, ссылки и зачёркивание.
    def _render_inline(s: str) -> str:
        """Экранирует строку и применяет инлайн-разметку Markdown."""

        def _capture_inline(m: re.Match) -> str:
            code_blocks.append(m.group(1))
            return f"\x01CODE{len(code_blocks) - 1}\x01"

        s = escape(s, quote=False)
        s = _INLINE_CODE_RE.sub(_capture_inline, s)
        s = _LINK_RE.sub(r'<a href="\2" style="color:#4fc3f7;">\1</a>', s)
        s = _STRIKE_RE.sub(r"<s>\1</s>", s)
        # Сначала тройные звёздочки, потом двойные, потом одинарные.
        s = _BOLD_ITALIC_RE.sub(r"<b><i>\1</i></b>", s)
        s = _BOLD_RE.sub(r"<b>\1</b>", s)
        s = _ITALIC_RE.sub(r"<i>\1</i>", s)
        return _PLACEHOLDER_RE.sub(_restore, s)

    # Возвращаем изолированные куски кода на место.
    def _restore(m: re.Match) -> str:
        kind, idx = m.group(1), int(m.group(2))
        body = escape(code_blocks[idx])
        if kind == "BLOCK":
            return (
                '<pre style="font-family: Consolas, monospace; font-size: 13px; '
                'background-color: #141414; color: #d4d4d4; border-radius: 6px; '
                'padding: 8px 10px;">' + body + "</pre>"
            )
        return (
            '<code style="font-family: Consolas, monospace; background-color: #2a2a2a; '
            'color: #9cdcfe; border-radius: 4px; padding: 1px 4px;">' + body + "</code>"
        )

    def _render_table(header: list[str], rows: list[list[str]]) -> str:
        """Собирает <table> из заголовка и строк-тела (Qt Rich Text)."""
        parts = ['<table border="1" cellspacing="0" cellpadding="4" '
                 'style="border-collapse: collapse; margin: 6px 0;">']
        head = "".join(
            '<th bgcolor="#141414" style="color: #e8e8e8; padding: 4px 8px;">'
            + _render_inline(c) + "</th>"
            for c in header
        )
        parts.append("<tr>" + head + "</tr>")
        for row in rows:
            cells = "".join(
                '<td style="color: #d4d4d4; padding: 4px 8px;">' + _render_inline(c) + "</td>"
                for c in row
            )
            parts.append("<tr>" + cells + "</tr>")
        parts.append("</table>")
        return "".join(parts)

    # Структуру разбираем на «сырых» строках: символы >, |, #, -, * ещё не
    # экранированы, поэтому цитаты, таблицы, заголовки и списки распознаются
    # корректно. Экранирование и звёздочки применяются только к содержимому.
    lines = text.split("\n")
    html_parts: list[str] = []
    pending: list[str] = []          # накопленные строки обычного абзаца
    i, n = 0, len(lines)

    def flush_paragraph() -> None:
        """Сбрасывает накопленный абзац в <p> с переносами <br/>."""
        if not pending:
            return
        body = "<br/>".join(_render_inline(p) for p in pending)
        html_parts.append(f'<p style="margin:0 0 6px 0;">{body}</p>')
        pending.clear()

    while i < n:
        line = lines[i]

        # Таблица: строка-заголовок |...| + строка-разделитель |---|---|.
        if _TABLE_ROW_RE.match(line) and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            flush_paragraph()
            header = _split_table_row(line)
            rows: list[list[str]] = []
            j = i + 2
            while j < n and _TABLE_ROW_RE.match(lines[j]) and not _TABLE_SEP_RE.match(lines[j]):
                rows.append(_split_table_row(lines[j]))
                j += 1
            html_parts.append(_render_table(header, rows))
            i = j
            continue

        # Горизонтальная черта --- (кроме хвостовой декорации в конце сообщения).
        if _HR_RE.match(line) and any(l.strip() for l in lines[i + 1:]):
            flush_paragraph()
            html_parts.append(
                '<hr style="border: none; border-top: 1px solid #2a2a2a; margin: 8px 0;"/>'
            )
            i += 1
            continue

        # Заголовки # .. ######.
        hm = _HEADER_RE.match(line)
        if hm:
            flush_paragraph()
            level = len(hm.group(1))
            size = {1: 22, 2: 19, 3: 16, 4: 14, 5: 13, 6: 12}[level]
            title = _render_inline(hm.group(2))
            html_parts.append(
                f'<h{level} style="margin: 10px 0 6px 0; font-size: {size}px; '
                f'font-weight: 700; color: #e8e8e8;">{title}</h{level}>'
            )
            i += 1
            continue

        # Цитата > текст (подряд идущие строки склеиваются в один блок).
        qm = _QUOTE_RE.match(line)
        if qm:
            flush_paragraph()
            quote_lines: list[str] = []
            while i < n:
                inner = _QUOTE_RE.match(lines[i])
                if not inner:
                    break
                quote_lines.append(inner.group(1))
                i += 1
            body = "<br/>".join(_render_inline(q) for q in quote_lines)
            html_parts.append(
                '<div style="border-left: 3px solid #2196F3; padding: 4px 10px; '
                'margin: 6px 0; color: #9a9a9a; font-style: italic;">' + body + "</div>"
            )
            continue

        # Ненумерованный список - * + (внутри распознаются и чек-листы).
        if _UL_ITEM_RE.match(line):
            flush_paragraph()
            items: list[str] = []
            while i < n:
                task = _TASK_RE.match(lines[i])
                if task:
                    box = "✅" if task.group(1).strip().lower() == "x" else "⬜"
                    items.append(
                        "<li>" + box + " " + _render_inline(task.group(2)) + "</li>"
                    )
                    i += 1
                    continue
                inner = _UL_ITEM_RE.match(lines[i])
                if not inner:
                    break
                items.append("<li>" + _render_inline(inner.group(1)) + "</li>")
                i += 1
            html_parts.append(
                '<ul style="margin: 4px 0 8px 0; padding-left: 20px;">'
                + "".join(items) + "</ul>"
            )
            continue

        # Нумерованный список 1. / 1).
        if _OL_ITEM_RE.match(line):
            flush_paragraph()
            items: list[str] = []
            while i < n:
                inner = _OL_ITEM_RE.match(lines[i])
                if not inner:
                    break
                items.append("<li>" + _render_inline(inner.group(2)) + "</li>")
                i += 1
            html_parts.append(
                '<ol style="margin: 4px 0 8px 0; padding-left: 20px;">'
                + "".join(items) + "</ol>"
            )
            continue

        # Обычный текст: пустая строка завершает абзац, остальное копится.
        if not line.strip():
            flush_paragraph()
        else:
            pending.append(line)
        i += 1

    flush_paragraph()
    return "".join(html_parts)


class HubIconButton(QPushButton):
    """Кнопка-иконка с двумя состояниями: обычным и при наведении курсора.

    Позволяет подменять иконку на hover (например, ChatSparkle Regular -> Filled),
    сохраняя прозрачный фон и собственный клик.
    """
    def __init__(
        self,
        normal: Optional[QIcon],
        hover: Optional[QIcon],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._normal_icon = normal
        self._hover_icon = hover
        if normal is not None:
            self.setIcon(normal)

    def set_hover_state(self, hovered: bool) -> None:
        """Программно включает/выключает состояние наведения (для строк-кнопок).

        Нужен контейнеру HubActionRow: его внутренняя иконка прозрачна для
        событий мыши, поэтому переключение Regular -> Filled выполняет сам
        контейнер при наведении курсора на строку хаба.
        """
        if hovered and self.isEnabled() and self._hover_icon is not None:
            self.setIcon(self._hover_icon)
        elif self._normal_icon is not None:
            self.setIcon(self._normal_icon)

    def enterEvent(self, event) -> None:
        # Отключённая кнопка не должна подсвечиваться (менять иконку на hover).
        self.set_hover_state(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.set_hover_state(False)
        super().leaveEvent(event)

class HubActionRow(QFrame):
    """Монолитная кликабельная строка «иконка + подпись» верхней панели хаба.

    Иконка и текст упакованы в ЕДИНЫЙ кликабельный виджет: клик по любой точке
    строки — по иконке, по тексту или по пустому месту — испускает РОВНО один
    сигнал ``clicked``. Дубли исключены конструктивно: внутренняя иконка
    (оригинальный HubIconButton со сменой Regular -> Filled) и подпись
    прозрачны для событий мыши, поэтому все нажатия обрабатывает сама строка.
    В свёрнутом виде хаба подпись скрывается — остаётся только иконка.
    """
    clicked = pyqtSignal()

    def __init__(
        self,
        normal_icon: Optional[QIcon],
        hover_icon: Optional[QIcon],
        text: str,
        parent: Optional[QWidget] = None,
        button_object_name: str = "",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("hubActionRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Внутренняя иконка переиспользует оригинальный класс HubIconButton —
        # визуальный стиль иконок хаба не меняется.
        self._icon = HubIconButton(normal_icon, hover_icon, self)
        if button_object_name:
            # Возвращаем оригинальный objectName (hubAddBtn/hubHistBtn): без
            # него на кнопку не действуют стили «background-color: transparent»
            # из глобальной таблицы, и Qt рисует её «родным» светлым фоном
            # Windows вместо иконки (белый квадрат).
            self._icon.setObjectName(button_object_name)
        self._icon.setFixedSize(24, 24)
        self._icon.setIconSize(QSize(20, 20))
        self._label = QLabel(text, self)
        # Иконка и текст прозрачны для мыши: события уходят родителю-строке,
        # благодаря чему клик по тексту срабатывает наравне с кликом по иконке.
        self._icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._lay = QHBoxLayout(self)
        self._lay.setSpacing(8)
        self._lay.addWidget(self._icon)
        self._lay.addWidget(self._label)
        self._lay.addStretch(1)
        # По умолчанию строка живёт в свёрнутом хабе — симметричные поля.
        self._apply_margins(False)

    def _apply_margins(self, expanded: bool) -> None:
        """Переключает поля строки под состояние хаба.

        В свёрнутом виде свободная ширина рейки невелика: при широких полях
        (6+6) под 24px-иконку не оставалось места, и она смещалась вбок с
        клиппингом. Симметричные поля 4+4 дают ровно 24px — иконка встаёт
        строго по центру бокса. В развёрнутом виде возвращается воздух под
        подпись текста (6+6).
        """
        if expanded:
            self._lay.setContentsMargins(6, 4, 6, 4)
        else:
            self._lay.setContentsMargins(4, 4, 4, 4)

    def set_expanded(self, expanded: bool) -> None:
        """Показывает/скрывает подпись и переключает поля строки."""
        self._label.setVisible(expanded)
        self._apply_margins(expanded)

    def enterEvent(self, event) -> None:
        # Передаём состояние наведения иконке (подмена Regular -> Filled).
        self._icon.set_hover_state(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._icon.set_hover_state(False)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        # Срабатывание только по левой кнопке, отпущенной внутри строки:
        # перетаскивание за пределы строки кликом не считается.
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

class ChatListItemWidget(QFrame):
    """Строка реального чата в списке Инженерного хаба.

    Слева — название чата (клик загружает сессию в окно переписки), справа —
    минималистичная кнопка ✕ (QToolButton), появляющаяся ТОЛЬКО при наведении
    курсора и запрашивающая удаление физического JSON-файла сессии с диска.
    """
    session_clicked = pyqtSignal(str)
    delete_requested = pyqtSignal(str)

    def __init__(
        self,
        session_id: str,
        title: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("chatItem")
        self.session_id = session_id
        self.title = title
        self.setMinimumHeight(40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 4, 0)
        lay.setSpacing(4)
        self._label = QLabel(title, self)
        # Подпись прозрачна для мыши: клик по названию обрабатывает сам QFrame.
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._label.setToolTip(title)
        lay.addWidget(self._label, 1)

        self._del_btn = QToolButton(self)
        self._del_btn.setObjectName("chatItemDelete")
        self._del_btn.setText("✕")
        self._del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._del_btn.setToolTip("Удалить чат")
        self._del_btn.hide()
        # Кнопка удаления перехватывает события мыши у родительского QFrame,
        # поэтому клик по ✕ НЕ загружает сессию и не дублирует сигналы.
        self._del_btn.clicked.connect(
            lambda: self.delete_requested.emit(self.session_id)
        )
        lay.addWidget(self._del_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    def enterEvent(self, event) -> None:
        # Кнопка ✕ появляется только при наведении курсора на строку.
        self._del_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._del_btn.hide()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        # Клик по названию чата загружает его сессию в окно переписки
        # (левый клик, отпущен внутри строки).
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.session_clicked.emit(self.session_id)
        super().mouseReleaseEvent(event)

class ThinkingSpoiler(QWidget):
    """Сворачиваемый подзаголовок «Размышления...» модели DeepSeek-R1.

    Показывает подзаголовок «Размышления...», сразу после которого расположен
    шеврон Fluent (ChevronDown20/ChevronUp20 из папки icons). Тело рассуждений
    по умолчанию скрыто; раскрыть его можно, нажав на стрелку — тогда шеврон
    динамически меняется наверх, а текст мыслей показывается.
    """
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 4, 0, 2)
        self._layout.setSpacing(2)
        # Строка заголовка: фраза «Размышления...» и стрелка-шеврон после неё.
        self._header = QHBoxLayout()
        self._header.setSpacing(2)
        self._label = QLabel("Размышления...", self)
        self._label.setObjectName("thinkingLabel")
        # Заголовок спойлера — текстовое содержимое чата: курсор IBeam.
        self._label.setCursor(Qt.CursorShape.IBeamCursor)
        # Стрелка-шеврон Fluent (ChevronUp20/ChevronDown20 из папки icons):
        # вниз — спойлер свёрнут, вверх — развёрнут. Иконка подменяется
        # динамически при переключении, а на hover Regular сменяется Filled
        # и светлеет (тот же паттерн, что и в HubIconButton).
        icon_size = 16
        self._arrow = QPushButton(self)
        self._arrow.setObjectName("thinkingArrow")
        self._arrow.setCursor(Qt.CursorShape.PointingHandCursor)
        self._arrow.setCheckable(True)
        self._arrow.setIconSize(QSize(icon_size, icon_size))
        # Кортежи (обычная, hover) для каждого направления шеврона.
        dim = styles.Palette.TEXT_DIM
        bright = styles.Palette.TEXT
        self._icons_down = (
            load_svg_icon("ChevronDown20Regular.svg", color=dim, size=icon_size),
            load_svg_icon("ChevronDown20Filled.svg", color=bright, size=icon_size),
        )
        self._icons_up = (
            load_svg_icon("ChevronUp20Regular.svg", color=dim, size=icon_size),
            load_svg_icon("ChevronUp20Filled.svg", color=bright, size=icon_size),
        )
        self._hovered = False
        self._apply_arrow_icon()
        self._header.addWidget(self._label)
        self._header.addWidget(self._arrow)
        self._header.addStretch(1)
        self._body = QLabel("", self)
        self._body.setObjectName("thinkingBody")
        self._body.setWordWrap(True)
        # Нативный QLabel в PyQt6 сам IBeam не ставит — задаём явно, заодно
        # разрешаем выделение текста мыши (флаг честно соответствует курсору).
        self._body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._body.setCursor(Qt.CursorShape.IBeamCursor)
        self._body.hide()
        self._layout.addLayout(self._header)
        self._layout.addWidget(self._body)
        self._arrow.clicked.connect(self._on_toggle)

    def _apply_arrow_icon(self) -> None:
        """Актуализирует иконку шеврона под состояние спойлера и курсора."""
        icons = self._icons_up if self._arrow.isChecked() else self._icons_down
        self._arrow.setIcon(icons[1] if self._hovered else icons[0])

    def _on_toggle(self, checked: bool) -> None:
        self._body.setVisible(checked)
        self._apply_arrow_icon()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self._apply_arrow_icon()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self._apply_arrow_icon()
        super().leaveEvent(event)

    def set_thinking(self, text: str) -> None:
        self._body.setText(text)
        self._arrow.setChecked(False)
        self._on_toggle(False)

    def update_thinking(self, text: str) -> None:
        """Обновляет текст мыслей без сброса состояния раскрытия спойлера.

        Используется во время потоковой генерации (стриминга), когда ход мыслей
        модели прибывает по кусочкам. В отличие от set_thinking, не сворачивает
        спойлер принудительно, а лишь актуализирует стрелку под текущее состояние
        видимости тела.
        """
        self._body.setText(text)
        self._apply_arrow_icon()

class CardThumbnail(QLabel):
    """Миниатюра в карточке сообщения.

    Изображение заполняет всю отведённую ему область без деформации
    (аналог CSS `object-fit: cover`): масштабируется с сохранением пропорций
    и обрезает лишнее. Верхние углы скругляются под форму карточки
    (токен `--borderRadiusLarge` = 12px), низ остаётся прямым.
    """
    RADIUS = 12  # совпадает с border-radius карточки (Fluent borderRadiusLarge)

    def __init__(self, pixmap: QPixmap, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("userCardThumb")
        self._src = pixmap
        self.setMinimumSize(1, 1)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._hover = False          # курсор над миниатюрой
        self._icon_filled = False    # активна ли иконка увеличения
        # Без mouseTracking mouseMoveEvent не срабатывает при обычном наведении,
        # а только при зажатой кнопке — поэтому иконка/курсор не обновлялись.
        self.setMouseTracking(True)
        # Центральная кнопка «увеличить», появляющаяся при наведении.
        self._zoom = QPushButton(self)
        self._zoom.setObjectName("cardZoomBtn")
        self._zoom.setIcon(load_svg_icon("ZoomInRegular.svg", size=32))
        self._zoom.setFixedSize(48, 48)
        self._zoom.setIconSize(QSize(32, 32))
        self._zoom.setCursor(Qt.CursorShape.PointingHandCursor)
        self._zoom.setStyleSheet(
            "QPushButton#cardZoomBtn {"
            "background-color: rgba(15,15,15,0.55); border: none;"
            "border-radius: 24px; }"
            "QPushButton#cardZoomBtn:hover {"
            "background-color: rgba(15,15,15,0.75); }"
        )
        # Прозрачна для мыши: наведение отслеживает сам виджет, а клик по
        # кнопке определяется по координатам (разворачивание — только по ней).
        self._zoom.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._zoom.hide()

    def _center_zoom(self) -> None:
        self._zoom.move(
            (self.width() - self._zoom.width()) // 2,
            (self.height() - self._zoom.height()) // 2,
        )

    def _set_zoom_icon(self, filled: bool) -> None:
        name = "ZoomInFilled.svg" if filled else "ZoomInRegular.svg"
        self._zoom.setIcon(load_svg_icon(name, size=32))

    def enterEvent(self, event) -> None:
        self._hover = True
        self._zoom.show()
        self._zoom.raise_()
        self._center_zoom()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self._zoom.hide()
        self._set_zoom_icon(False)
        self.unsetCursor()
        self.update()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event) -> None:
        # Наведение на саму иконку меняет её на Filled и включает «руку».
        over = self._zoom.geometry().contains(event.position().toPoint())
        if over != self._icon_filled:
            self._icon_filled = over
            self._set_zoom_icon(over)
            if over:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.unsetCursor()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        # Разворачивание ТОЛЬКО по кнопке увеличения; клик в любом другом
        # месте миниатюры ничего не делает.
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self._src.isNull()
            and self._zoom.geometry().contains(event.position().toPoint())
        ):
            ScreenshotViewer(self._src).exec()
        super().mousePressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._center_zoom()

    def _rounded_path(self) -> QPainterPath:
        """Путь со скруглением всех четырёх углов (под форму карточки)."""
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self.RADIUS, self.RADIUS)
        return path

    def paintEvent(self, event) -> None:
        """Рисует скриншот как cover-обложку, скругляя верхние углы карточки."""
        if self._src.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        scaled = self._src.scaled(
            self.width(), self.height(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        painter.setClipPath(self._rounded_path())
        painter.drawPixmap(x, y, scaled)
        # Лёгкое приглушение при наведении курсора.
        if self._hover:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 90))
        painter.end()


def _wrap_words(text: str, fm: QFontMetricsF, width: float) -> list:
    """Переносит текст по словам под заданную ширину."""
    lines: list = []
    current = ""
    for word in text.split():
        trial = (current + " " + word).strip()
        if not current or fm.horizontalAdvance(trial) <= width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


class ExpandableCaption(QLabel):
    """Сворачиваемая подпись под миниатюрой сообщения.

    По умолчанию текст показывается максимум в ``MAX_LINES`` (15) строк. Если
    он длиннее, внизу появляется кликабельный индикатор «…» — по нажатию на
    него сообщение раскрывается полностью (повторный клик сворачивает обратно).
    """

    MAX_LINES = 15        # максимальное число строк в свёрнутом виде
    PAD_X = 8             # горизонтальный внутренний отступ
    PAD_Y = 5             # вертикальный внутренний отступ

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("userCardCaption")
        self._text = text
        self._expanded = False
        self.setMinimumWidth(1)
        # Курсор управляется динамически в _reflow: «рука» при переполнении,
        # иначе IBeam (обычный текст).
        self.setCursor(Qt.CursorShape.IBeamCursor)

    # ------------------------------------------------------------------ данные
    def text(self) -> str:
        """Возвращает исходный текст подписи."""
        return self._text

    def setText(self, text: str) -> None:
        """Устанавливает текст подписи, сбрасывая развёрнутое состояние."""
        self._text = text
        self._expanded = False
        self._reflow()
        self.update()

    # ------------------------------------------------------------- геометрия
    def _line_height(self) -> int:
        return self.fontMetrics().lineSpacing()

    def _lines(self) -> list:
        """Разбивает текст на строки по словам под текущую ширину."""
        width = max(1, self.width() - 2 * self.PAD_X)
        return _wrap_words(self._text, self.fontMetrics(), width)

    @property
    def _overflow(self) -> bool:
        """True, если текст длиннее лимита строк."""
        return len(self._lines()) > self.MAX_LINES

    def _visible_lines(self) -> int:
        if self._expanded:
            return len(self._lines())
        if self._overflow:
            # лишняя строка для кликабельного «…»
            return self.MAX_LINES + 1
        return len(self._lines())

    def _reflow(self) -> None:
        """Пересчитывает и применяет фиксированную высоту под видимые строки."""
        h = self._visible_lines() * self._line_height() + 2 * self.PAD_Y
        if h != self.height():
            self.setFixedHeight(h)
        # Актуализируем курсор: переполнение => есть что развернуть («рука»).
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if self._overflow else Qt.CursorShape.IBeamCursor
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._reflow()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflow()

    # ------------------------------------------------------------- интерактив
    def mouseReleaseEvent(self, event) -> None:
        if self._overflow:
            self._expanded = not self._expanded
            self._reflow()
            self.update()
        super().mouseReleaseEvent(event)

    # --------------------------------------------------------------- отрисовка
    def paintEvent(self, event) -> None:
        if not self._text:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        fm = self.fontMetrics()
        width = max(1, self.width() - 2 * self.PAD_X)
        line_h = self._line_height()
        top = self.PAD_Y
        lines = self._lines()
        limit = len(lines) if self._expanded else min(len(lines), self.MAX_LINES)

        painter.setPen(self.palette().color(QPalette.ColorRole.Text))
        for i in range(limit):
            line = lines[i]
            if not self._expanded and self._overflow and i == limit - 1:
                line = fm.elidedText(
                    line, Qt.TextElideMode.ElideRight, width
                )
            painter.drawText(
                QRect(self.PAD_X, top + i * line_h, width, line_h),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                line,
            )

        # Кликабельный индикатор «…» для свёрнутого переполненного текста.
        if self._overflow and not self._expanded:
            painter.setPen(self.palette().color(QPalette.ColorRole.Highlight))
            painter.drawText(
                QRect(self.PAD_X, top + self.MAX_LINES * line_h, width, line_h),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                "…",
            )
        painter.end()


class ChatTextLabel(QLabel):
    """QLabel сообщения с честным курсором: I-образный над текстом.

    Нативный QLabel в текущей сборке PyQt6 НЕ показывает IBeam даже с флагом
    TextSelectableByMouse (проверено эмпирически диагностическим зондом):
    курсор остаётся стрелкой. Этот подкласс сам отслеживает мышь: над обычным
    текстом — IBeam, над кликабельной ссылкой — «рука», при уходе курсора
    возвращает IBeam вместо стрелки.

    Ссылка под курсором определяется сигналом linkHovered, который Qt шлёт
    самостоятельно: методов linkAt()/document() в этой версии PyQt6 нет, а
    ловить исключения при каждом движении мыши нельзя (падало приложение).
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # Без mouseTracking mouseMoveEvent приходит только при зажатой кнопке,
        # а нам нужно обновлять курсор при простом наведении.
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self._over_link = False          # курсор сейчас над ссылкой?
        self.linkHovered.connect(self._on_link_hovered)

    def _on_link_hovered(self, link: str) -> None:
        """Сигнал Qt: мышь вошла в зону ссылки rich-text или покинула её."""
        self._over_link = bool(link)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if link else Qt.CursorShape.IBeamCursor
        )

    def mouseMoveEvent(self, event) -> None:
        # Состояние уже поддерживается сигналом linkHovered; здесь лишь
        # применяем его к курсору — без исключений и геометрических поисков.
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if self._over_link else Qt.CursorShape.IBeamCursor
        )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._over_link = False
        self.setCursor(Qt.CursorShape.IBeamCursor)
        super().leaveEvent(event)


class MessageRow(QWidget):
    """Строка сообщения: баблы справа для инженера, чистый текст слева для ИИ."""
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(8)
        self._left_stretch = QWidget(self)
        self._right_stretch = QWidget(self)
        # Метка ответа ИИ создаётся заранее, чтобы её можно было обновлять на
        # лету во время потоковой генерации (в отличие от статичного show_ai).
        # Кастомная метка: IBeam над текстом, «рука» над ссылками Markdown.
        self._label = ChatTextLabel(self)
        self._label.setObjectName("aiAnswer")
        self._label.setWordWrap(True)
        # Метка рендерит Markdown модели (**жирный**, *курсив*, ### заголовки,
        # списки, таблицы) как Rich Text: весь текст ответа пропускается через
        # md_to_html() перед setText(). Ссылки открываются в браузере по клику.
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setOpenExternalLinks(True)
        self._label.setMaximumWidth(700)
        # Горизонтально метка занимает всю доступную ширину (до 700px), а не
        # схлопывается в узкий столбец под свой sizeHint. Высота при wordWrap
        # вычисляется через heightForWidth и растёт под полный текст ответа.
        self._label.setMinimumWidth(0)
        self._label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        # TextSelectableByMouse — выделение текста, LinksAccessibleByMouse —
        # обязательный флаг для сигнала linkHovered (наведение на ссылки).
        self._label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self._spoiler = None          # спойлер мыслей создаётся лениво
        self._ai_wrap = QWidget(self)
        self._ai_layout = QVBoxLayout(self._ai_wrap)
        self._ai_layout.setContentsMargins(0, 0, 0, 0)
        self._ai_layout.addWidget(self._label)
        self._ai_mounted = False      # добавлены ли виджеты в общий лейаут

    def show_user(self, text: str, pixmap: Optional[QPixmap] = None) -> None:
        """Показывает сообщение инженера.

        При наличии скриншота строится карточка Fluent 2: миниатюра 160×160
        сверху с обрезкой `object-fit: cover` и скруглением 12px, а текст —
        сворачиваемая подпись снизу (до 15 строк; при переполнении появляется
        кликабельный «…», раскрывающий всё сообщение). Без скриншота
        показывается обычный пузырь.
        """
        has_img = pixmap is not None and not pixmap.isNull()

        if has_img:
            # Карточка: миниатюра 160×160 сверху + сворачиваемая подпись снизу.
            card = QWidget(self)
            card.setObjectName("userCard")
            card.setFixedWidth(160)
            cv = QVBoxLayout(card)
            cv.setContentsMargins(0, 0, 0, 0)
            cv.setSpacing(0)
            thumb = CardThumbnail(pixmap, card)
            thumb.setFixedSize(160, 160)
            caption = ExpandableCaption(text, card)
            if not text.strip():
                # Без текста подпись не нужна: миниатюра занимает всю карточку.
                caption.hide()
            # Курсор подписи управляется самой подписью (см. _reflow):
            # «рука» при переполнении, IBeam для обычного текста.
            cv.addWidget(thumb, 0)
            cv.addWidget(caption, 0)

            self._layout.addWidget(self._left_stretch, 1)
            self._layout.addWidget(card, 0)
            self._layout.addWidget(self._right_stretch, 0)
            return

        # Обычный пузырь с текстом (без скриншота).
        bubble = QWidget(self)
        bubble.setObjectName("userBubble")
        bubble.setMaximumWidth(500)
        v = QVBoxLayout(bubble)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        txt = ChatTextLabel(bubble)
        txt.setObjectName("userBubbleText")
        txt.setWordWrap(True)
        txt.setTextFormat(Qt.TextFormat.RichText)
        txt.setOpenExternalLinks(True)
        txt.setText(md_to_html(text))
        # Те же флаги взаимодействия, что у ответа ИИ: выделение + ссылки.
        txt.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        v.addWidget(txt, 0, Qt.AlignmentFlag.AlignLeft)

        self._layout.addWidget(self._left_stretch, 1)
        self._layout.addWidget(bubble, 0)
        self._layout.addWidget(self._right_stretch, 0)

    def _mount_ai(self) -> None:
        """Добавляет AI-блок (растяжки + обёртку) в общий лейаут один раз."""
        if not self._ai_mounted:
            self._layout.addWidget(self._left_stretch, 0)
            self._layout.addWidget(self._ai_wrap, 1)
            self._layout.addWidget(self._right_stretch, 0)
            self._ai_mounted = True
        self._ai_wrap.show()

    def begin_ai_stream(self):
        """Готовит строку к потоковой генерации ответа ИИ.

        Возвращает:
            MessageRow — сам объект строки, чтобы вызывающий код мог обновлять
            текст ответа и блок мыслей методами set_ai_stream.
        """
        self._mount_ai()
        return self

    def _refresh_ai_geometry(self) -> None:
        """Пересчитывает геометрию растущего при стриминге ответа ИИ.

        НЕ вызываем adjustSize(): для метки с wordWrap он схлопывает её в узкий
        столбец по собственному sizeHint. Вместо этого просто помечаем обёртку и
        строку как «изменившиеся», чтобы раскладка пересчитала высоту через
        heightForWidth — так ответ занимает всю ширину и показывается целиком.
        """
        self._ai_wrap.updateGeometry()
        self.updateGeometry()

    def set_ai_stream(self, answer: str, thinking: str = "") -> None:
        """Обновляет текст ответа и (при наличии) блок мыслей на лету.

        Вызывается на каждый новый фрагмент стрима: метка ответа перезаписывается
        актуальным видимым текстом, а спойлер мыслей обновляется без сброса
        состояния раскрытия. Спойлер создаётся лениво при первом появлении мыслей.
        """
        self._mount_ai()
        self._label.setText(md_to_html(answer))
        if thinking:
            if self._spoiler is None:
                self._spoiler = ThinkingSpoiler(self)
                self._ai_layout.insertWidget(0, self._spoiler)
            self._spoiler.update_thinking(thinking)
        self._refresh_ai_geometry()

    def set_thinking_only(self, thinking: str) -> None:
        """Обновляет ТОЛЬКО блок мыслей модели, не трогая текст ответа.

        Используется LlamaWorker (одномодельная архитектура): англоязычные
        рассуждения Qwen3 внутри <thinking> показываются в раскрывающемся
        спойлере, а видимый русскоязычный финальный ответ рисуется отдельно.
        """
        self._mount_ai()
        if thinking:
            if self._spoiler is None:
                self._spoiler = ThinkingSpoiler(self)
                self._ai_layout.insertWidget(0, self._spoiler)
            self._spoiler.update_thinking(thinking)
        self._refresh_ai_geometry()

    def finish_ai_stream(self) -> None:
        """Завершает потоковую генерацию (сейчас — декоративная точка расширения)."""
        pass

    def show_ai(self, text: str, thinking: str = "") -> None:
        """Показывает готовый (не стриминговый) ответ ИИ одной операцией."""
        self.begin_ai_stream()
        self.set_ai_stream(text, thinking)
class WelcomeHost(QWidget):
    """Эстетичные центрированные приветственные экраны трех режимов."""
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("welcomeHost")
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        inner = QVBoxLayout()
        inner.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._title = QLabel("", self)
        self._title.setObjectName("welcomeTitle")
        self._subtitle = QLabel("", self)
        self._subtitle.setObjectName("welcomeSubtitle")
        inner.addWidget(self._title, 0, Qt.AlignmentFlag.AlignHCenter)
        inner.addWidget(self._subtitle, 0, Qt.AlignmentFlag.AlignHCenter)
        self._examples = [QLabel(self) for _ in range(2)]
        for ex in self._examples:
            ex.setObjectName("welcomeExample")
            inner.addWidget(ex, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addLayout(inner)
        outer.addStretch(1)

    def set_mode(self, mode: str) -> None:
        """Наполняет приветственный экран данными из стилей (Канон 16.5).

        Метод заполняет исключительно свои внутренние лейблы и не обращается
        к каким-либо внешним атрибутам окна — это чистый локальный наполнитель.
        """
        data = styles.WELCOME_SCREENS[mode]
        self._title.setText(data["title"])
        self._subtitle.setText(data["subtitle"])
        for lbl, txt in zip(self._examples, data["examples"]):
            lbl.setText(txt)

class HubDividerLine(QWidget):
    """Линия-разделитель с градиентным угасанием по краям."""
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(2)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        from PyQt6.QtGui import QPainter
        painter = QPainter(self)
        styles.paint_hub_fade_line(self, painter)
        painter.end()

class FadeOutOverlay(QWidget):
    """Маска градиентного угасания текста при скроллинге чата."""
    # Высота задаёт плавность перехода: чем больше, тем мягче уход в «0».
    HEIGHT = 56
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        from PyQt6.QtGui import QPainter
        painter = QPainter(self)
        styles.paint_fade_out(self, painter, self.height())
        painter.end()

class ScreenshotViewer(QDialog):
    """Полноэкранный просмотр скриншота.

    Показывает исходное изображение (в своём размере, не более 75% экрана),
    приглушая фон до 20% прозрачности. Закрывается ТОЛЬКО по кнопке с крестиком
    в правом верхнем углу или по клавише Esc — клик в любом другом месте ничего
    не делает.
    """
    def __init__(self, pixmap: QPixmap, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Скриншот")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        # Прозрачный фон: сквозь подложку виден рабочий стол, как в «Ножницах».
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.setGeometry(geo)
        # Размер — исходный, но не более 75% экрана. KeepAspectRatio только
        # уменьшает (не растягивает), поэтому малые скриншоты остаются в своём
        # размере, а большие ужимаются до 75% рабочего стола.
        target_w = int(geo.width() * 0.75)
        target_h = int(geo.height() * 0.75)
        self._scaled = pixmap.scaled(
            target_w, target_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        # Кнопка закрытия в правом верхнем углу (динамическая иконка DismissCircle48).
        self._close_btn = HubIconButton(
            load_svg_icon("DismissCircle48Regular.svg"),
            load_svg_icon("DismissCircle48Filled.svg"),
            self,
        )
        self._close_btn.setFixedSize(40, 40)
        self._close_btn.setIconSize(QSize(26, 26))
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setToolTip("Закрыть предпросмотр")
        self._close_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none;"
            "border-radius: 20px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.22); }"
        )
        self._close_btn.clicked.connect(self.accept)
        self._place_close_btn()

    def _place_close_btn(self) -> None:
        """Крестик закрытия прижат к правому верхнему углу."""
        margin = 12
        size = self._close_btn.width()
        self._close_btn.move(self.width() - size - margin, margin)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_close_btn()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # Приглушение фона на 90% (0.9 * 255 ≈ 230).
        painter.fillRect(self.rect(), QColor(0, 0, 0, 230))
        x = (self.width() - self._scaled.width()) // 2
        y = (self.height() - self._scaled.height()) // 2
        painter.drawPixmap(x, y, self._scaled)
        painter.end()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.accept()
        else:
            super().keyPressEvent(event)


class ScreenshotPreview(QWidget):
    """Превью скриншота 64×64 с закруглением по стандарту Fluent Design.

    Логика та же, что у скриншотов в сообщениях: по наведению бокс слегка
    приглушается тёмным оверлеем, по центру появляется кнопка увеличения
    (ZoomIn24, Regular -> Filled при наведении на иконку). Разворачивание —
    ТОЛЬКО по этой кнопке; клик в другом месте ничего не делает. В правом
    верхнем углу внутри бокса — кнопка удаления (DismissCircle24).
    """
    closed = pyqtSignal()
    _RADIUS = 8   # скругление бокса (стандарт Fluent)
    _IMG_SIZE = 64
    _TOP = 26     # высота полосы НАД боксом, где висит кнопка удаления

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("screenshotPreview")
        self.setFixedSize(self._IMG_SIZE, self._IMG_SIZE + self._TOP)
        self._src = QPixmap()
        self._hover = False          # курсор над боксом
        self._icon_filled = False    # активна ли иконка увеличения
        # Без mouseTracking mouseMoveEvent не срабатывает при наведении.
        self.setMouseTracking(True)

        # Кнопка удаления — всегда видима, в правом верхнем углу внутри бокса.
        self._close_btn = HubIconButton(
            load_svg_icon("DismissCircle24Regular.svg"),
            load_svg_icon("DismissCircle24Filled.svg"),
            self,
        )
        self._close_btn.setFixedSize(24, 24)
        self._close_btn.setIconSize(QSize(20, 20))
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setToolTip("Убрать скриншот")
        self._close_btn.setStyleSheet(
            "QPushButton { background: rgba(15,15,15,0.45); border: none;"
            "border-radius: 12px; }"
            "QPushButton:hover { background: rgba(15,15,15,0.75); }"
        )
        self._close_btn.clicked.connect(self.closed)

        # Кнопка увеличения — появляется по наведению, по центру.
        self._zoom = QPushButton(self)
        self._zoom.setObjectName("previewZoomBtn")
        self._zoom.setIcon(load_svg_icon("ZoomIn24Regular.svg", size=32))
        self._zoom.setFixedSize(44, 44)
        self._zoom.setIconSize(QSize(32, 32))
        self._zoom.setCursor(Qt.CursorShape.PointingHandCursor)
        self._zoom.setStyleSheet(
            "QPushButton#previewZoomBtn {"
            "background-color: rgba(15,15,15,0.55); border: none;"
            "border-radius: 22px; }"
            "QPushButton#previewZoomBtn:hover {"
            "background-color: rgba(15,15,15,0.75); }"
        )
        # Прозрачна для мыши: наведение отслеживает сам виджет, а клик по
        # кнопке определяется по координатам (разворачивание — только по ней).
        self._zoom.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._zoom.hide()

        self._place_widgets()
        self.hide()

    def _image_rect(self) -> QRect:
        """Прямоугольник бокса с изображением (ниже полосы с кнопкой удаления)."""
        return QRect(0, self._TOP, self._IMG_SIZE, self._IMG_SIZE)

    def _place_widgets(self) -> None:
        """Кнопка удаления — в полосе НАД боксом справа; увеличение — по центру бокса."""
        margin = 6
        self._close_btn.move(
            self.width() - self._close_btn.width() - margin, margin
        )
        img = self._image_rect()
        self._zoom.move(
            img.x() + (img.width() - self._zoom.width()) // 2,
            img.y() + (img.height() - self._zoom.height()) // 2,
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_widgets()

    def _set_zoom_icon(self, filled: bool) -> None:
        name = "ZoomIn24Filled.svg" if filled else "ZoomIn24Regular.svg"
        self._zoom.setIcon(load_svg_icon(name, size=32))

    def enterEvent(self, event) -> None:
        self._hover = True
        self._zoom.show()
        self._zoom.raise_()
        self._place_widgets()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self._zoom.hide()
        self._set_zoom_icon(False)
        self.unsetCursor()
        self.update()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event) -> None:
        # Наведение на саму иконку меняет её на Filled и включает «руку».
        over = self._zoom.geometry().contains(event.position().toPoint())
        if over != self._icon_filled:
            self._icon_filled = over
            self._set_zoom_icon(over)
            if over:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.unsetCursor()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        # Разворачивание ТОЛЬКО по кнопке увеличения; другое место — ничего.
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self._src.isNull()
            and self._zoom.geometry().contains(event.position().toPoint())
        ):
            self._launch_viewer()
        super().mousePressEvent(event)

    def _rounded_path(self, rect: QRect) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), self._RADIUS, self._RADIUS)
        return path

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = self._image_rect()
        painter.setClipPath(self._rounded_path(rect))
        if self._src.isNull():
            painter.fillRect(rect, QColor("#2a2d31"))
        else:
            # Cover-обрезка: миниатюра заполняет весь бокс без деформации.
            scaled = self._src.scaled(
                rect.width(), rect.height(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = rect.x() + (rect.width() - scaled.width()) // 2
            y = rect.y() + (rect.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        # Лёгкое приглушение при наведении курсора.
        if self._hover:
            painter.fillRect(rect, QColor(0, 0, 0, 90))
        painter.end()

    def _launch_viewer(self, *args) -> None:
        """Полноэкранный просмотр скриншота (только по кнопке увеличения)."""
        if not self._src.isNull():
            ScreenshotViewer(self._src).exec()

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._src = pixmap
        self.update()
        self.show()

class ScreenSnipperOverlay(QDialog):
    """Оверлей выделения экрана «Ножницами», порт на PyQt6.

    Полностью покрывает экран, тонирует его полупрозрачной маской и позволяет
    «вырезать» прямоугольную область: внутри рамки экран остаётся чистым
    (дырка в маске), по краю рисуется тонкий синий контур. Аналог штатных
    «Ножниц» Windows. Курсор-прицел действует по всей площади окна.
    """
    captured = pyqtSignal(QPixmap, tuple)

    # Полупрозрачная чёрная маска (~30%) и синий контур выделения.
    _MASK: QColor = QColor(0, 0, 0, 76)
    _FRAME: QColor = QColor("#2196F3")

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        # Прозрачный фон окна: сквозь маску виден рабочий стол, а «дырка»
        # выделения показывает экран без затемнения (как в Archive).
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # Прицел доступен на всей области полноэкранного оверлея.
        self.setCursor(Qt.CursorShape.CrossCursor)
        # Покрываем ВЕСЬ виртуальный рабочий стол (все подключённые мониторы),
        # объединяя их геометрии, а не только первичный экран.
        screens = QGuiApplication.screens()
        if screens:
            combined = QRect(screens[0].geometry())
            for s in screens[1:]:
                combined = combined.united(s.geometry())
            self.setGeometry(combined)
        self._origin = QPoint()
        self._current = QPoint()
        self._selecting = False

    def _selection_rect(self) -> QRect:
        """Возвращает нормализованный прямоугольник текущего выделения."""
        return QRect(self._origin, self._current).normalized()

    def paintEvent(self, event) -> None:
        """Рисует полупрозрачную маску всего экрана и вырезает «дырку» под выделение."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Сначала тонируем весь экран полупрозрачной маской (~30%).
        p.fillRect(self.rect(), self._MASK)
        rect = self._selection_rect() if self._selecting else QRect()
        if rect.isEmpty():
            # Пока выделение не начато — оставляем равномерную тонировку.
            return
        # «Вырезаем» прозрачную дырку внутри выделения: эта область остаётся
        # с исходной яркостью рабочего стола, без артефактных линий по краям.
        hole = QPainterPath()
        # QPainterPath.addRect принимает QRectF (а не QRect) — приводим тип.
        hole.addRect(QRectF(rect))
        p.save()
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.fillPath(hole, Qt.GlobalColor.transparent)
        p.restore()
        # Тонкий синий контур по периметру выделения.
        p.setPen(QPen(self._FRAME, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._current = self._origin
            self._selecting = True
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._selecting:
            self._current = event.position().toPoint()
            self.update()

    def _capture_selection(self, rect: QRect) -> Optional[QPixmap]:
        """Захватывает выделенную область всего виртуального рабочего стола.

        Весь рабочий стол снимается ОДНИМ вызовом первичного экрана (единая
        система координат, без выбора «нужного» монитора), после чего из кадра
        вырезается нужный фрагмент. Это устойчиво к отрицательным координатам
        дополнительных мониторов и к большим разрешениям (2K/4K): изображение
        не масштабируется и не уезжает по DPI.
        """
        screens = QGuiApplication.screens()
        if not screens:
            return None
        bbox = QRect(screens[0].geometry())
        for s in screens[1:]:
            bbox = bbox.united(s.geometry())
        primary = QGuiApplication.primaryScreen()
        # Снимаем весь виртуальный стол целиком в глобальных координатах bbox.
        full = primary.grabWindow(
            0, bbox.x(), bbox.y(), bbox.width(), bbox.height()
        )
        if full.isNull():
            return None
        # Правый верхний угол full соответствует bbox.topLeft() в виртуальных
        # координатах, поэтому смещаем область выделения на этот вектор.
        crop = QRect(rect).translated(-bbox.topLeft())
        crop = crop.intersected(full.rect())
        if crop.isEmpty():
            return None
        return full.copy(crop)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self._selecting:
            return
        self._selecting = False
        local_rect = self._selection_rect()
        self.accept()
        # Локальные координаты оверлея -> глобальные координаты виртуального
        # стола (оверлей покрывает весь виртуальный стол целиком).
        origin = self.geometry().topLeft()
        rect = local_rect.translated(origin)
        if rect.width() > 4 and rect.height() > 4:
            pixmap = self._capture_selection(rect)
            if pixmap is not None and not pixmap.isNull():
                self.captured.emit(
                    pixmap,
                    (rect.left(), rect.top(), rect.right(), rect.bottom()),
                )

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)

class MessageInput(QPlainTextEdit):
    """Поле ввода сообщения с боевыми сочетаниями клавиш.

    Enter (без модификаторов) — отправка сообщения: событие поглощается,
    чтобы QPlainTextEdit не вставлял перевод строки.
    Shift+Enter — классический перенос каретки на следующую строку.
    """
    submit_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

    def keyPressEvent(self, event) -> None:
        # Enter без Shift (в т.ч. цифровой клавиатуры) — отправляем сообщение
        # и блокируем вставку перевода строки штатным обработчиком.
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        ):
            self.submit_requested.emit()
            event.accept()
            return
        # Shift+Enter и все остальные клавиши — стандартное поведение (перенос).
        super().keyPressEvent(event)

class CadStatusPoller(QThread):
    """Фоновый опрос связи с AutoCAD для статус-строки капсулы (Зона 3).

    Опрос ensure_connection() выполняется в отдельном потоке, поэтому даже
    при потере COM-указателя (перезапуск AutoCAD) интерфейс не зависает:
    повторное подключение с паузами живёт в фоне, а в главный поток уходит
    только компактный сигнал с результатом проверки.

    Сигналы:
        status_changed(bool, str, str): (подключено, имя_документа, полный_путь).
    """
    status_changed = pyqtSignal(bool, str, str)

    def run(self) -> None:
        # Интервал дробится на короткие шаги, чтобы поток быстро завершался
        # по requestInterruption при закрытии окна приложения.
        steps = max(1, int(CAD_STATUS_POLL_INTERVAL_SEC * 2))
        while not self.isInterruptionRequested():
            connected = False
            name = ""
            full_path = ""
            try:
                acad = tools.ensure_connection()
                if acad is not None:
                    doc = acad.ActiveDocument
                    name = str(getattr(doc, "Name", "") or "")
                    full_path = str(getattr(doc, "FullName", "") or "")
                    connected = True
            except Exception:
                connected = False
            self.status_changed.emit(connected, name, full_path)
            for _ in range(steps):
                if self.isInterruptionRequested():
                    return
                time.sleep(0.5)


# Период heartbeat-опроса лимитов llama-server для прогрессбара контекста (мс).
SERVER_LIMITS_POLL_INTERVAL_MS: int = 10000


class ServerLimitsPoller(QThread):
    """Фоновый heartbeat-опрос параметров llama-server (прогрессбар контекста).

    Каждые 10 секунд форсированно перечитывает n_ctx с локального C++ сервера
    (GET /props с фолбэком на /v1/models). Это снимает главную проблему
    статичного индикатора: однократный прогрев при старте мог закэшировать
    безопасный дефолт 4096, даже когда сервер реально поднят с окном 16384.
    Теперь максимум прогрессбара динамически перестраивается на лету, как
    только связь с llama-server восстанавливается (Пока-ёкэ рассинхронизации).

    Сетевой вызов выполняется в этом потоке, поэтому главный поток GUI никогда
    не блокируется; в интерфейс уходит только компактный сигнал с числом.

    Сигналы:
        limits_updated(int): актуальный размер контекстного окна n_ctx.
    """
    limits_updated = pyqtSignal(int)

    def run(self) -> None:
        # Интервал дробится на короткие шаги, чтобы поток быстро завершался
        # по requestInterruption при закрытии окна приложения.
        steps = max(1, int(SERVER_LIMITS_POLL_INTERVAL_MS / 500))
        while not self.isInterruptionRequested():
            # Форсированный опрос: кэш core_core перезаписывается живым
            # значением (16384 и т.п.); при таймауте сохраняется последний
            # известный лимит либо безопасный дефолт 4096 (не блокируя UI).
            n_ctx = fetch_server_limits(force=True)
            self.limits_updated.emit(int(n_ctx))
            for _ in range(steps):
                if self.isInterruptionRequested():
                    return
                time.sleep(0.5)


class ContextRing(ProgressRing):
    """Кольцо контекста с кастомным тултипом-полоской (Fluent Design).

    Наведение на кольцо раскрывает маленькую карточку-подсказку: текст
    в тысячах токенов с процентом и НАСТОЯЩАЯ тонкая акцентная полоска
    прогресса — вместо ASCII-квадратиков стандартного QToolTip (Пока-ёкэ
    читаемости заполненности контекста).
    """
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(26, 26)
        self._strokeWidth = 4          # тонкая дуга флюент-кольца
        self.setTextVisible(False)
        self.setRange(0, 4096)

        # Карточка-подсказка: текст токенов + тонкая полоска прогресса.
        self._tip = QFrame(None)
        self._tip.setObjectName("contextRingTip")
        # Отдельное окно без рамок поверх всех окон (как штатный QToolTip).
        self._tip.setWindowFlags(
            Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
        )
        self._tip.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        tip_layout = QVBoxLayout(self._tip)
        tip_layout.setContentsMargins(10, 8, 10, 8)
        tip_layout.setSpacing(6)
        self._tip_label = QLabel("Использовано: 0к / 4к токенов (0%)", self._tip)
        self._tip_label.setObjectName("contextTipLabel")
        self._tip_bar = QProgressBar(self._tip)
        self._tip_bar.setObjectName("contextBar")
        self._tip_bar.setFixedSize(140, 6)
        self._tip_bar.setTextVisible(False)
        self._tip_bar.setRange(0, 4096)
        tip_layout.addWidget(self._tip_label)
        tip_layout.addWidget(self._tip_bar)
        self._tip.adjustSize()
        self._tip.hide()

    def update_progress(self, used: int, max_tokens: int, percent: int) -> None:
        """Синхронно обновляет кольцо, полоску тултипа и текст токенов."""
        self.setRange(0, max_tokens)
        self.setValue(used)
        self._tip_bar.setRange(0, max_tokens)
        self._tip_bar.setValue(used)
        self._tip_label.setText(
            "Использовано: {}к / {}к токенов ({}%)".format(
                used // 1000, max_tokens // 1000, percent
            )
        )
        self._tip.adjustSize()

    def _position_tip(self) -> None:
        top_left = self.mapToGlobal(QPoint(0, 0))
        x = top_left.x() + (self.width() - self._tip.width()) // 2
        y = top_left.y() - self._tip.height() - 8
        # У верхней кромки экрана карточку показываем под кольцом.
        screen = QGuiApplication.screenAt(QPoint(x, y))
        if screen is None or y < screen.availableGeometry().top():
            y = top_left.y() + self.height() + 8
        self._tip.move(x, y)

    def enterEvent(self, event) -> None:
        # Показываем карточку над кольцом по центру при наведении курсора.
        self._position_tip()
        self._tip.show()
        self._tip.raise_()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._tip.hide()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        # Кольцо скрыто (например, при схлопывании зон) — прячем тултип.
        self._tip.hide()
        super().hideEvent(event)


class InputCapsule(QFrame):
    """Монолитная трёхзонная парящая капсула ввода.

    Зона 1 (верх): панель медиа-контекста — готовое превью скриншота.
        Схлопывается по высоте (setVisible(False)), пока скриншота нет.
    Зона 2 (центр): свободное текстовое поле на 100% ширины капсулы,
        внутри поля больше нет встроенных кнопок.
    Зона 3 (низ): панель параметров и действий — скрепка, светодиод
        AutoCAD, имя DWG-чертежа с многострочным тултипом, бар контекста
        токенов, кнопки «Ножницы» и отправки.
    """
    send_requested = pyqtSignal(str)
    plus_clicked = pyqtSignal()
    camera_clicked = pyqtSignal()
    preview_closed = pyqtSignal()
    input_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("inputCapsule")
        # Динамическая высота Зоны 2 — растёт до MAX_LINES строк ввода.
        self.MAX_LINES: int = 6
        # Минимальная высота поля ввода: одна строка + вертикальные отступы.
        self.FIELD_MIN_HEIGHT: int = 44
        # Есть ли прикреплённый скриншот, ожидающий отправки.
        self._has_attachment = False

        # ------------------------------------------------------------------
        # ЗОНА 2: свободное текстовое поле (центр, 100% ширины капсулы).
        # ------------------------------------------------------------------
        self.input = MessageInput(self)
        self.input.setObjectName("messageInput")
        self.input.setPlaceholderText("Введите сообщение…")
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Enter без Shift — отправка сообщения через штатный обработчик капсулы.
        self.input.submit_requested.connect(self._on_send_clicked)
        self.input.textChanged.connect(self._update_send_state)
        self.input.textChanged.connect(self._resize_to_content)
        # Проброс изменения текста наружу (обновление бара контекста в Зоне 3).
        self.input.textChanged.connect(self.input_changed)
        # Явно задаём светлый цвет текста и каретки: иначе мигающий курсор
        # наследует тёмный цвет и становится невидимым на графитовом фоне.
        input_pal = self.input.palette()
        input_pal.setColor(QPalette.ColorRole.Text, QColor("#e8e8e8"))
        input_pal.setColor(QPalette.ColorRole.WindowText, QColor("#e8e8e8"))
        input_pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#6b6b6b"))
        input_pal.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
        self.input.setPalette(input_pal)
        # Каретка сразу получает фокус и видна (мигание обеспечивается Qt).
        self.input.setFocus()
        self.input.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Чуть толще, чтобы светлая каретка была хорошо заметна.
        self.input.setCursorWidth(2)
        # Гарантируем мигание вне зависимости от точки запуска приложения.
        if QApplication.cursorFlashTime() <= 0:
            QApplication.setCursorFlashTime(1000)

        # ------------------------------------------------------------------
        # ЗОНА 1: панель медиа-контекста (верх) — превью скриншота.
        # Готовый виджет ScreenshotPreview сохраняет всю нативную логику:
        # кнопку удаления, кнопку раскрытия на весь экран и hover-оверлей.
        # ------------------------------------------------------------------
        self.preview = ScreenshotPreview(self)
        self.preview.closed.connect(self.preview_closed)
        self._preview_zone = QWidget(self)
        self._preview_zone.setObjectName("mediaZone")
        media_layout = QHBoxLayout(self._preview_zone)
        media_layout.setContentsMargins(0, 0, 0, 0)
        media_layout.setSpacing(0)
        media_layout.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignLeft)
        media_layout.addStretch(1)

        # ------------------------------------------------------------------
        # ЗОНА 3: нижняя панель параметров и действий.
        # ------------------------------------------------------------------
        # Кнопка-скрепка: обычное состояние — Attach24Regular, при наведении —
        # более жирная Attach24Filled (левое крыло).
        self.plus_btn = HubIconButton(
            load_svg_icon("Attach24Regular.svg"),
            load_svg_icon("Attach24Filled.svg"),
            self,
        )
        self.plus_btn.setObjectName("plusBtn")
        self.plus_btn.setFixedSize(36, 36)
        self.plus_btn.setIconSize(QSize(20, 20))
        self.plus_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.plus_btn.clicked.connect(self.plus_clicked)

        # Светодиод-индикатор подключения к AutoCAD (🟢/🔴 по ensure_connection).
        self.status_led = QLabel("🔴", self)
        self.status_led.setObjectName("statusLed")
        self.status_led.setToolTip("AutoCAD не подключён")

        # Имя текущего DWG-чертежа с многострочным тултипом (путь + файл).
        self.dwg_label = QLabel("Нет подключения", self)
        self.dwg_label.setObjectName("dwgNameLabel")

        # Кольцо заполненности контекста (Fluent Design, правое крыло).
        # Круговой индикатор ProgressRing — «спинер» прогресса: тонкое кольцо
        # с акцентной дугой, а наведение раскрывает тултип с полоской прогресса.
        self.context_ring = ContextRing(self)
        self.context_ring.setObjectName("contextRing")

        # Кнопка фотоаппарата «Ножницы»: обычное состояние — CameraAdd24Regular,
        # при наведении — жирная CameraAdd24Filled (правое крыло).
        self.camera_btn = HubIconButton(
            load_svg_icon("CameraAdd24Regular.svg"),
            load_svg_icon("CameraAdd24Filled.svg"),
            self,
        )
        self.camera_btn.setObjectName("cameraBtn")
        self.camera_btn.setFixedSize(36, 36)
        self.camera_btn.setIconSize(QSize(20, 20))
        self.camera_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.camera_btn.clicked.connect(self.camera_clicked)

        # Кнопка отправки: ArrowUp (обычное состояние — Regular, hover — Filled).
        # Отключена, пока в боксе нет текста, чтобы не подсвечивать её как кликабельную.
        self.send_btn = HubIconButton(
            load_svg_icon("ArrowUp24Regular.svg"),
            load_svg_icon("ArrowUp24Filled.svg"),
            self,
        )
        self.send_btn.setObjectName("sendBtn")
        # Главная кнопка отправки — крупнее остальных (38px).
        self.send_btn.setFixedSize(38, 38)
        self.send_btn.setIconSize(QSize(22, 22))
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self._on_send_clicked)

        # Горизонтальный слой Зоны 3: левое крыло и правое крыло, в котором
        # кольцо контекста прижато к правому краю рядом с кнопками действий.
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(8)
        bottom_bar.addWidget(self.plus_btn)       # скрепка (левое крыло)
        bottom_bar.addWidget(self.status_led)     # светодиод AutoCAD
        bottom_bar.addWidget(self.dwg_label)      # имя чертежа (левое крыло)
        bottom_bar.addStretch(1)                  # пружина — правое крыло к краю
        bottom_bar.addWidget(self.context_ring)   # кольцо контекста (правое крыло)
        bottom_bar.addWidget(self.camera_btn)     # «Ножницы» (правое крыло)
        bottom_bar.addWidget(self.send_btn)       # отправка (правое крыло)

        # Сборка монолитной капсулы из трёх вертикальных зон.
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 10)
        root.setSpacing(6)
        root.addWidget(self._preview_zone)   # Зона 1 (скрыта до скриншота)
        root.addWidget(self.input)           # Зона 2 (поле ввода)
        root.addLayout(bottom_bar)           # Зона 3 (панель параметров)

        # Зона 1 изначально схлопнута: скриншота в памяти ещё нет.
        self._preview_zone.hide()

        # ВАЖНО: НЕ переопределяем focusInEvent/focusOutEvent экземпляра лямбдами —
        # это ломает стандартную отрисовку и мигание каретки QPlainTextEdit.
        # Подсветку капсулы и масок отслеживаем через eventFilter поля ввода:
        # это надёжнее глобального focusChanged, т.к. привязано к самому input.
        self.input.installEventFilter(self)

        self._resize_to_content()

    def _resize_to_content(self) -> None:
        """Подгоняет высоту поля ввода под число строк (не более MAX_LINES).

        Высота всей капсулы складывается layout'ом из трёх зон автоматически;
        здесь фиксируется только высота самого текстового поля (Зона 2).
        Используем lineCount() — он учитывает переносы длинного текста,
        а не только абзацы (Enter).
        """
        metrics = QFontMetricsF(self.input.font())
        line_h = max(1.0, float(metrics.lineSpacing()))
        lines = self.input.document().lineCount()
        target = max(1, min(lines, self.MAX_LINES))
        margin = int(self.input.document().documentMargin())
        # Симметричный вертикальный отступ текста в боксе (padding-top/bottom из QSS),
        # чтобы нижняя строка не прижималась к границе.
        v_pad = 10
        field_h = max(
            self.FIELD_MIN_HEIGHT,
            int(round(line_h * target)) + 2 * margin + 2 * v_pad + 2,
        )
        self.input.setFixedHeight(field_h)
        # Пока строки помещаются — держим прокрутку в начале, чтобы первая
        # строка не «улетала» за верхнюю границу при росте высоты капсулы.
        # Сброс делаем отложенно: Qt сам прокручивает к каретке сразу после
        # ввода, поэтому сбрасываем позицию уже после пересчёта высоты/автоскролла.
        bar = self.input.verticalScrollBar()
        if lines > self.MAX_LINES:
            bar.setValue(bar.maximum())  # переполнение — следуем за кареткой вниз
        else:
            QTimer.singleShot(0, lambda b=bar: b.setValue(0))

    def eventFilter(self, obj: object, event: QEvent) -> bool:
        """Отслеживает фокус поля ввода и перекрашивает маски затухания.

        При получении/потере фокуса немедленно обновляем состояние капсулы
        и принудительно поднимаем + перерисовываем обе маски, чтобы верхняя
        не «зависала» со старым цветом после возврата фокуса (например,
        после закрытия оверлея скриншота).
        """
        if obj is self.input:
            if event.type() == QEvent.Type.FocusIn:
                self.set_focused(True)
            elif event.type() == QEvent.Type.FocusOut:
                self.set_focused(False)
        return super().eventFilter(obj, event)

    def _on_send_clicked(self) -> None:
        text = self.input.toPlainText().strip()
        # Отправлять можно при наличии текста ИЛИ прикреплённого скриншота.
        if text or self._has_attachment:
            self.send_requested.emit(text)

    def set_has_screenshot(self, has: bool) -> None:
        """Учитывает скриншот в отправке и схлопывает/раскрывает Зону 1.

        При отсутствии скриншота Зона 1 полностью скрывается по высоте
        (setVisible(False)) — капсула возвращается к двухзонному виду.
        """
        self._has_attachment = bool(has)
        self._preview_zone.setVisible(has)
        if not has:
            self.preview.hide()
        self._update_send_state()

    def _update_send_state(self) -> None:
        # Кнопка активна, если в боксе есть текст ИЛИ прикреплён скриншот.
        has_text = bool(self.input.toPlainText().strip())
        self.send_btn.setEnabled(has_text or self._has_attachment)

    def clear(self) -> None: self.input.clear()
    def set_focused(self, focused: bool) -> None:
        self.setProperty("focused", focused)
        self.style().unpolish(self)
        self.style().polish(self)

class MainWindow(QMainWindow):
    send_requested = pyqtSignal(str)
    # Ширина Инженерного хаба в свёрнутом (только кнопки) и развёрнутом виде.
    # 48px — рейка с запасом: 24px-иконка с симметричными полями 4px встаёт
    # ровно по центру бокса (при 44px контент вылезал за края строки).
    RAIL_WIDTH: int = 48
    HUB_MAX_WIDTH: int = 264
    PADDING: int = 18

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("CAD Инженерный Ассистент")
        self.setObjectName("mainCanvas")
        self.setMinimumSize(600, 750)
        self.resize(1024, 760)

        # Перекрашиваем стандартную рамку Windows 10 в цвет нашего графита чертежа
        import ctypes
        DWMWA_CAPTION_COLOR = 35
        hwnd = int(self.winId())
        color = 0x1E1E1E  # Тёмно-графитовый HEX-цвет
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_CAPTION_COLOR, ctypes.byref(ctypes.c_int(color)), 4)

        self.setStyleSheet(styles.build_global_stylesheet())
        self._mode = styles.MODE_CHAT
        self._drawer_visible = False
        # Скриншот, ожидающий отправки с очередным сообщением.
        self._pending_screenshot: Optional[QPixmap] = None
        # Буфер сырого текста текущей потоковой генерации (для разбора <thinking>).
        self._stream_raw: str = ""
        # Двухконтурная стерильная память чата: массив реплик {"role", "content"}.
        # Ответы ассистента попадают сюда ТОЛЬКО после чистки clean_response_for_history
        # (теги <thinking> вырезаны), поэтому у модели сохраняется контекст прошлых
        # построений без риска зацикливания на собственном черновике рассуждений.
        self._chat_history: list = []
        # Локальный менеджер сессий: JSON-файлы диалогов лежат в папке history/
        # корня проекта (Этап 3). Папка создаётся при первом обращении.
        self._history_manager = HistoryManager()
        # ID активной сессии (uuid) и её название. None означает дефолтное
        # приветственное состояние: физического файла на диске ещё нет.
        self._active_session_id: Optional[str] = None
        self._session_title: Optional[str] = None
        # Фоновый опрос оборудования llama-server: n_ctx кэшируется в core_core
        # daemon-потоком БЕЗ блокировки GUI, чтобы первый запрос LlamaWorker
        # уже знал фактический размер контекстного окна (дефолт 4096 лишь при
        # недоступности сервера).
        prewarm_server_limits()

        self._build_rail_and_drawer()
        self._build_center_canvas()

        central_widget = QWidget(self)
        root = QHBoxLayout(central_widget)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # Единый Инженерный хаб слева: свёрнут до полосы с кнопками,
        # разворачивается и показывает черту и список чатов под кнопками.
        root.addWidget(self._hub_panel)
        root.addWidget(self._canvas, 1)
        self.setCentralWidget(central_widget)
        self.set_mode(self._mode)

    def _build_rail_and_drawer(self) -> None:
        # Единый Инженерный хаб слева: в свёрнутом виде узкая полоса только
        # с кнопками; при нажатии «истории» разворачивается, показывая
        # под кнопками черту-разделитель, а под чертой — список чатов.
        self._hub_panel = QWidget(self)
        self._hub_panel.setObjectName("drawerBody")
        self._hub_panel.setFixedWidth(self.RAIL_WIDTH)

        hub_layout = QVBoxLayout(self._hub_panel)
        hub_layout.setContentsMargins(8, 12, 8, 8)
        hub_layout.setSpacing(8)

        # Монолитные кликабельные строки верхней панели: иконка и подпись
        # упакованы в ЕДИНЫЙ виджет, поэтому клик по тексту срабатывает наравне
        # с кликом по иконке (ровно одно срабатывание — без дублей сигналов).
        # Иконки — оригинальные (ChatSparkle / LineHorizontal3), стиль не меняем;
        # в свёрнутом виде хаба подписи скрываются, остаются только иконки.
        self._new_chat_row = HubActionRow(
            load_svg_icon("ChatSparkle24Regular.svg"),
            load_svg_icon("ChatSparkle24Filled.svg"),
            "Создать новый чат",
            self._hub_panel,
            button_object_name="hubAddBtn",
        )
        self._new_chat_row.clicked.connect(self._on_new_chat)

        self._history_row = HubActionRow(
            load_svg_icon("LineHorizontal324Regular.svg"),
            load_svg_icon("LineHorizontal324Filled.svg"),
            "Свернуть вкладку",
            self._hub_panel,
            button_object_name="hubHistBtn",
        )
        self._history_row.clicked.connect(self._on_history_click)

        hub_layout.addWidget(self._new_chat_row)
        hub_layout.addWidget(self._history_row)

        # Черта-разделитель идёт сразу под кнопками (дизайн не меняем).
        self._hub_divider = HubDividerLine(self._hub_panel)
        hub_layout.addWidget(self._hub_divider)

        # Список чатов располагается под чертой.
        self._history_scroll = SmoothScrollArea(self._hub_panel)
        self._history_scroll.setObjectName("historyScroll")
        self._history_scroll.setWidgetResizable(True)

        history_body = QWidget(self._history_scroll)
        self._history_list = QVBoxLayout(history_body)
        self._history_list.addStretch(1)
        self._history_scroll.setWidget(history_body)
        # Скролл получает основной вес при развёрнутом хабе.
        hub_layout.addWidget(self._history_scroll, 10)
        # Нижняя растяжка прижимает кнопки к верху: в свёрнутом виде она
        # занимает всю свободную высоту, не давая кнопкам «разъехаться».
        hub_layout.addStretch(1)

        # Регистр реальных чатов из папки history/ — динамические строки
        # ChatListItemWidget вместо старых текстовых заглушек.
        self._history_items: dict = {}
        self._active_history_item: Optional[ChatListItemWidget] = None
        self._rebuild_history_list()

        # В свёрнутом виде черта и чаты полностью скрыты, чтобы из-под узкой
        # полосы не торчали фрагменты названий. Появляются при развороте хаба.
        self._hub_divider.hide()
        self._history_scroll.hide()
        # Подписи верхних кнопок тоже показываются только в развёрнутом виде.
        self._set_drawer_rows_expanded(False)

        # Анимация ширины хаба: свёрнут до RAIL_WIDTH, разворачивается до HUB_MAX_WIDTH.
        self._drawer_anim = QPropertyAnimation(self._hub_panel, b"maximumWidth", self)
        self._drawer_anim.setDuration(200)
        self._drawer_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _on_history_click(self) -> None:
        """Разворачивает/сворачивает Инженерный хаб («Свернуть вкладку»).

        В развёрнутом виде под кнопками появляются черта-разделитель, список
        реальных чатов и подписи самих верхних кнопок; при сворачивании всё это
        прячется, чтобы из-под узкой полосы не торчали фрагменты названий.
        """
        self._drawer_visible = not self._drawer_visible
        # Черта и чаты существуют только в развёрнутом виде — прячем целиком,
        # а не оставляем обрезанными под узкой полосой.
        self._hub_divider.setVisible(self._drawer_visible)
        self._history_scroll.setVisible(self._drawer_visible)
        self._set_drawer_rows_expanded(self._drawer_visible)
        self._drawer_anim.stop()
        self._drawer_anim.setStartValue(self._hub_panel.width())
        self._drawer_anim.setEndValue(
            self.HUB_MAX_WIDTH if self._drawer_visible else self.RAIL_WIDTH
        )
        self._drawer_anim.start()

    def _set_drawer_rows_expanded(self, expanded: bool) -> None:
        """Показывает/скрывает подписи верхних кнопок хаба (иконки не трогаем)."""
        self._new_chat_row.set_expanded(expanded)
        self._history_row.set_expanded(expanded)

    def _on_new_chat(self) -> None:
        """Создаёт новый чат: очищает экран и сбрасывает активную сессию.

        Лента сообщений очищается, ID активной сессии обнуляется, интерфейс
        возвращается в дефолтное приветственное состояние. Физический JSON-файл
        прежней сессии остаётся на диске в папке history/ — он уже сохранён.
        """
        self._clear_chat_view()
        self._chat_history.clear()
        self._active_session_id = None
        self._session_title = None
        self._chat_has_messages = False
        self._capsule.clear()
        self._welcome.show()
        self._welcome.raise_()
        self._set_active_history_item(None)
        self._refresh_context_bar()

    def _clear_chat_view(self) -> None:
        """Полностью очищает ленту сообщений, оставляя нижнюю растяжку."""
        while self._chat_layout.count() > 1:
            item = self._chat_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _load_session(self, session_id: str) -> None:
        """Загружает массив messages выбранной сессии в окно переписки.

        Лента пересобирается из JSON-файла папки history/: пользовательские
        реплики рисуются баблами справа, ответы ассистента — слева. Скриншоты
        в файлах не хранятся (хранится только текст), поэтому карточки
        изображений при загрузке не восстанавливаются.
        """
        data = self._history_manager.load_session(session_id)
        if data is None:
            return
        self._clear_chat_view()
        self._chat_history = [dict(m) for m in data.get("messages", [])]
        self._active_session_id = session_id
        self._session_title = data.get("title", "Новый чат")
        for msg in self._chat_history:
            row = MessageRow(self._scroll)
            content = str(msg.get("content", ""))
            if msg.get("role") == "user":
                row.show_user(content)
            else:
                row.show_ai(content, "")
            self._chat_layout.insertWidget(self._chat_layout.count() - 1, row)
        if self._chat_history:
            self._chat_has_messages = True
            self._welcome.hide()
        else:
            self._chat_has_messages = False
            self._welcome.show()
            self._welcome.raise_()
        self._set_active_history_item(self._history_items.get(session_id))
        self._scroll_to_bottom()
        self._refresh_context_bar()

    def _on_delete_session(self, session_id: str) -> None:
        """Подтверждает и удаляет сессию: физический файл + плавное исчезновение.

        Сначала спрашивает подтверждение у пользователя, затем удаляет
        JSON-файл с диска ПК и анимирует схлопывание строки в списке хаба.
        Если удалена открытая в данный момент сессия — экран возвращается
        в дефолтное приветственное состояние.
        """
        item = self._history_items.get(session_id)
        if item is None:
            return
        reply = QMessageBox.question(
            self,
            "Удаление чата",
            f"Удалить чат «{item.title}»? Это действие нельзя отменить.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # 1) Физическое удаление JSON-файла сессии с диска ПК.
        self._history_manager.delete_session(session_id)
        # 2) Плавное схлопывание строки в интерфейсе.
        self._history_items.pop(session_id, None)
        self._history_list.removeWidget(item)
        start_h = item.height()
        anim = QPropertyAnimation(item, b"maximumHeight", self)
        anim.setDuration(180)
        anim.setStartValue(start_h)
        anim.setEndValue(0)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.valueChanged.connect(lambda v: item.setFixedHeight(int(v)))
        anim.finished.connect(item.deleteLater)
        anim.start()
        # 3) Если удалена открытая сессия — возвращаемся к приветствию.
        if session_id == self._active_session_id:
            self._on_new_chat()

    def _persist_chat(self) -> None:
        """Сохраняет текущую сессию в JSON-файл папки history/ (идемпотентно).

        Вызывается после каждой добавленной реплики (пользователя или
        ассистента). Если активной сессии ещё нет — вызов безопасно игнорируется.
        """
        if not self._active_session_id:
            return
        self._history_manager.save_session(
            self._active_session_id,
            self._session_title or "Новый чат",
            self._chat_history,
        )

    def _rebuild_history_list(self) -> None:
        """Перестраивает список реальных чатов из папки history/ (новые сверху).

        При старте и после создания новой сессии список полностью пересобирается
        из JSON-файлов на диске, гарантируя актуальность названий и порядка.
        """
        for item in list(self._history_items.values()):
            self._history_list.removeWidget(item)
            item.deleteLater()
        self._history_items.clear()
        for session in self._history_manager.list_sessions():
            item = ChatListItemWidget(
                str(session.get("id", "")),
                str(session.get("title", "Новый чат")),
            )
            item.session_clicked.connect(self._load_session)
            item.delete_requested.connect(self._on_delete_session)
            self._history_items[session["id"]] = item
            # Вставка в начало + сортировка «новые сверху» = свежайший диалог
            # оказывается самой верхней строкой списка.
            self._history_list.insertWidget(0, item)
        self._active_history_item = None

    def _set_active_history_item(self, item: Optional[ChatListItemWidget]) -> None:
        """Подсвечивает активную сессию в списке и снимает старую подсветку."""
        if self._active_history_item is not None:
            self._active_history_item.setProperty("active", False)
            self._active_history_item.style().unpolish(self._active_history_item)
            self._active_history_item.style().polish(self._active_history_item)
        self._active_history_item = item
        if item is not None:
            item.setProperty("active", True)
            item.style().unpolish(item)
            item.style().polish(item)

    def _build_center_canvas(self) -> None:
        self._canvas = QWidget(self)
        canvas_layout = QVBoxLayout(self._canvas)
        canvas_layout.setContentsMargins(20, 15, 20, 0)

        self._segment = SegmentedWidget(self)
        for mode, label in styles.MODE_LABELS.items():
            self._segment.addItem(routeKey=mode, text=label)

        # Жёсткий фикс чёрного текста для Windows 10 на уровне палитры Qt6.
        white_color = QColor("#e8e8e8")
        # Безопасно итерируемся напрямую по словарю items: у SegmentedWidget
        # нет ни метода count(), ни метода item(), вкладки живут в структуре items.
        for mode_key, item in self._segment.items.items():
            if item:
                # Назначаем палитру для текста кнопки навигации.
                palette = item.palette()
                palette.setColor(QPalette.ColorRole.WindowText, white_color)
                palette.setColor(QPalette.ColorRole.ButtonText, white_color)
                palette.setColor(QPalette.ColorRole.Text, white_color)
                item.setPalette(palette)
                # Дополнительно перекрашиваем любые вложенные QLabel внутри вкладки.
                for child in item.findChildren(QLabel):
                    child.setStyleSheet(
                        "color: #e8e8e8 !important; background: transparent;"
                    )

        self._segment.currentItemChanged.connect(self.set_mode)

        top_bar = QHBoxLayout()
        top_bar.addStretch(1)
        top_bar.addWidget(self._segment)
        top_bar.addStretch(1)
        canvas_layout.addLayout(top_bar)

        self._scroll = SmoothScrollArea(self._canvas)
        self._scroll.setObjectName("chatScroll")
        self._scroll.setWidgetResizable(True)
        # Автоследование за стримом: пока пользователь находится у нижнего края,
        # лента сама прокручивается вниз при каждом чанке. Любой уход ползунка
        # вверх (колесо, перетаскивание, клавиши) снимает флаг — см. _on_chat_scrolled.
        self._pinned_to_bottom = True
        self._scroll.verticalScrollBar().valueChanged.connect(
            self._on_chat_scrolled
        )

        viewport = QWidget(self._scroll)
        viewport.setObjectName("chatViewport")
        self._chat_layout = QVBoxLayout(viewport)
        self._chat_layout.addStretch(1)
        self._scroll.setWidget(viewport)
        canvas_layout.addWidget(self._scroll, 1)

        self._welcome = WelcomeHost(self._canvas)
        self._welcome.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._chat_has_messages = False

        self._fade_overlay = FadeOutOverlay(self._canvas)
        self._build_input_capsule(canvas_layout)

        # Позиционируем приветствие сразу после первой раскладки layout'ов,
        # а не ждём resizeEvent (иначе текст не виден до изменения размера окна).
        QTimer.singleShot(0, self._refresh_welcome)

    def _build_input_capsule(self, layout: QVBoxLayout) -> None:
        host = QWidget(self._canvas)
        host.setObjectName("inputHost")
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(self.PADDING, 0, self.PADDING, self.PADDING)
        # При изменении размера контейнера ввода (рост капсулы, появление/скрытие
        # Зоны 1 с превью скриншота) виньетка маскировки над боксом должна
        # следовать за его верхней кромкой, иначе она «наедет» на поле ввода.
        self._input_host = host
        host.installEventFilter(self)

        # Монолитная трёхзонная капсула: превью скриншота (Зона 1), свободное
        # текстовое поле (Зона 2) и панель параметров/действий (Зона 3) живут
        # внутри одной парящей капсулы; превью больше не подвешивается рядом.
        self._capsule = InputCapsule(host)
        self._capsule.send_requested.connect(self._on_send)
        self._capsule.camera_clicked.connect(self._launch_snipper)
        self._capsule.plus_clicked.connect(lambda: self._notify_in_dev("Прикрепление файлов"))
        self._capsule.preview_closed.connect(self._clear_screenshot)
        self._capsule.input_changed.connect(self._refresh_context_bar)
        host_layout.addWidget(self._capsule)
        layout.addWidget(host)

        # Фоновый опрос связи с AutoCAD для светодиода и имени DWG (Зона 3).
        self._cad_poller = CadStatusPoller(self)
        self._cad_poller.status_changed.connect(self._on_cad_status)
        self._cad_poller.start()

        # Лёгкий таймер: когда daemon-поток core_core наполнит кэш n_ctx,
        # бар контекста пересчитается с фактическим лимитом llama-server.
        self._last_context_max: Optional[int] = None
        self._context_cache_timer = QTimer(self)
        self._context_cache_timer.setInterval(2000)
        self._context_cache_timer.timeout.connect(self._poll_context_cache)
        self._context_cache_timer.start()

        # Динамический heartbeat-опрос llama-server (10 с): даже если прогрев
        # при старте застал сервер выключенным, кэш n_ctx обновится на лету,
        # как только связь восстановится, и максимум бара контекста мгновенно
        # перестроится (например, с дефолтных 4096 на реальные 16384).
        self._limits_poller = ServerLimitsPoller(self)
        self._limits_poller.limits_updated.connect(self._on_server_limits_updated)
        self._limits_poller.start()

        # Бар контекста заполняется сразу, не дожидаясь первого ввода.
        self._refresh_context_bar()

    def _on_send(self, text: str) -> None:
        pixmap = self._pending_screenshot
        self.send_requested.emit(text)
        # Скриншот отображается графически в одном боксе с текстом сообщения.
        row = MessageRow(self._scroll)
        row.show_user(text, pixmap)
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, row)
        self._capsule.clear()
        # Первый контур памяти: пользовательская реплика уходит в историю целиком,
        # без каких-либо тегов <thinking> (их модель в запросах не использует).
        if text.strip():
            self._chat_history.append({"role": "user", "content": text})
            # Первое сообщение в дефолтном приветственном состоянии порождает
            # реальную сессию: автоматическое именование (3-4 слова запроса,
            # обрезанные до 25 символов) и физическое создание JSON-файла
            # в папке history/ корня проекта.
            if self._active_session_id is None:
                title = HistoryManager.make_title_from_prompt(text)
                self._active_session_id = self._history_manager.create_session(
                    title, []
                )
                self._session_title = title
                self._rebuild_history_list()
                self._set_active_history_item(
                    self._history_items.get(self._active_session_id)
                )
        self._clear_pending_screenshot()
        if not self._chat_has_messages:
            self._chat_has_messages = True
            self._welcome.hide()
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        # Реплика зафиксирована в памяти — синхронизируем JSON-файл сессии.
        self._persist_chat()
        # История изменилась — пересчитываем бар заполненности контекста.
        self._refresh_context_bar()
        # Отправляем запрос в роутер (заглушка зрения / маршрут на стриминг).
        self._dispatch(text, pixmap)

    def _dispatch(self, text: str, pixmap: Optional[QPixmap]) -> None:
        """Выполняет решение роутера: мгновенный ответ или потоковая генерация.

        Спрашивает main_router.route_request с учётом режима интерфейса. Роутер
        возвращает единый маршрут "stream"; скриншот (если прикреплён в Зоне 1)
        передаётся прямо в LlamaWorker, где кодируется в Base64 и упаковывается
        в мультимодальный Vision-контент (Этап 4 — заглушка зрения ликвидирована).
        Возможны два исхода:
          - "direct_reply": мгновенный ответ без обращения к сети (зарезервирован);
          - "stream": запуск ЕДИНОГО фонового потока LlamaWorker со стримингом
            текста одной монолитной модели Qwen3 через llama-server.
        """
        decision = route_request(text, self._mode)

        # Мгновенный ответ (зарезервированный сценарий) — без фонового потока.
        if decision.get("action") == "direct_reply":
            self._append_ai_message(
                decision.get("reply", ""),
                decision.get("thinking", ""),
            )
            return

        # Единый одномодельный маршрут ВО ВСЕХ режимах (Чат / Помощник /
        # Песочница): запрос уходит на монолитную Qwen3 через llama-server.
        # Рассуждения модели внутри <thinking> (английский) стримятся в спойлер
        # «Размышления...», а видимый ответ (русский) плавно рисуется в MessageRow.
        self._stream_raw = ""
        row = self._begin_ai_stream()
        worker = LlamaWorker(
            prompt=decision.get("user_prompt", text),
            model=decision.get("model", "Qwen3.8-27B-IQ3-MIX"),
            mode=self._mode,
            system_prompt=decision.get("system_prompt"),
            # Второй эшелон памяти: стерильная история прошлых реплик (уже без
            # <thinking>) пристыковывается к системному промпту в _build_messages.
            chat_history=self._chat_history,
            # Скриншот из Зоны 1: LlamaWorker закодирует его в Base64 и соберёт
            # мультимодальный контент финального user-сообщения (Этап 4).
            screenshot_pixmap=pixmap,
            parent=self,
        )
        # Рассуждения модели обновляют спойлер мыслей в реальном времени.
        worker.thinking_changed.connect(
            lambda th, r=row: self._on_chain_thinking(r, th)
        )
        # Привязываем каждый сигнал к СВОЕЙ строке через замыкание, чтобы при
        # параллельной генерации куски не попадали в чужое сообщение.
        worker.chunk_received.connect(
            lambda chunk, r=row: self._on_ai_chunk(r, chunk)
        )
        worker.generation_finished.connect(
            lambda ans, th, r=row: self._on_ai_finished(r, ans, th)
        )
        worker.start()

    def _begin_ai_stream(self) -> MessageRow:
        """Создаёт строку ответа ИИ и добавляет её в ленту чата."""
        row = MessageRow(self._scroll)
        row.begin_ai_stream()
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, row)
        return row

    def _scroll_to_bottom(self) -> None:
        """Прокручивает чат вниз и принудительно пересчитывает геометрию вьюпорта.

        При потоковой генерации текст строки растёт быстро. Если не актуализировать
        геометрию вьюпорта на каждом чанке, длинное сообщение клипается по нижнему
        краю области прокрутки и кажется «оборванным». activate() пересобирает
        раскладку, а сама прокрутка откладывается в следующий цикл обработки
        событий (QTimer.singleShot), чтобы раскладка успела завершиться ДО скролла —
        иначе прокрутка опережает рост контента, и низ сообщения остаётся скрытым.

        Прокрутка вниз выполняется ТОЛЬКО когда пользователь «прилип» к нижнему
        краю (_pinned_to_bottom). Если он поднялся вверх читать историю — пересчёт
        геометрии продолжается (контент не клипается), но ползунок больше не
        сбрасывается вниз, и чтение не прерывается стримом.
        """
        widget = self._scroll.widget()
        if widget is not None:
            widget.updateGeometry()
        if hasattr(self, "_chat_layout"):
            self._chat_layout.activate()
        if not self._pinned_to_bottom:
            return
        bar = self._scroll.verticalScrollBar()

        def _do_follow() -> None:
            # Повторная проверка флага в момент срабатывания таймера защищает от
            # гонки: если пользователь успел прокрутить вверх за один цикл событий
            # между планированием и выполнением — рывка вниз не произойдёт.
            if self._pinned_to_bottom:
                bar.setValue(bar.maximum())

        QTimer.singleShot(0, _do_follow)

    def _on_chat_scrolled(self, value: int) -> None:
        """Отслеживает намерение пользователя при ручной прокрутке чата.

        Сигнал valueChanged срабатывает при любом движении ползунка — как от колеса
        мыши, жестов и клавиатуры, так и от программного setValue. Если текущая
        позиция ушла от нижнего края дальше порога CHAT_SCROLL_PIN_THRESHOLD_PX,
        автоследование за стримом отключается; вернувшись к низу, пользователь
        снова «прилипает» и лента продолжает следовать за генерацией.
        """
        bar = self._scroll.verticalScrollBar()
        self._pinned_to_bottom = (
            value >= bar.maximum() - CHAT_SCROLL_PIN_THRESHOLD_PX
        )

    def _on_ai_chunk(self, row: MessageRow, chunk: str) -> None:
        """Обновляет строку ответа на каждый новый фрагмент стрима.

        Накопленный сырой текст разбирается функцией split_thinking: ответ рисуется
        меткой, а блок <thinking> уходит в спойлер мыслей в реальном времени.
        """
        self._stream_raw += chunk
        answer, thinking, _in = split_thinking(self._stream_raw)
        row.set_ai_stream(answer, thinking)
        self._scroll_to_bottom()

    def _on_ai_finished(self, row: MessageRow, answer: str, thinking: str) -> None:
        """Финализирует строку ответа по завершении генерации в фоне.

        Это ТОЧКА ТРИГГЕРА перехвата JSON (Этап 3, Шаг 3): стриминг от
        llama-server полностью завершён (сигнал generation_finished), поэтому
        финальный ответ модели прогоняется через исполнительный контур
        execute_tool_commands. Если модель прислала markdown-блок ```json с
        командами кубиков — парсер извлечёт его, преобразует в словарь Python
        и ФИЗИЧЕСКИ выполнит команды в AutoCAD (например, draw_circle). Любая
        ошибка парсинга (битый JSON из-за квантового сбоя) уходит в скрытый
        системный лог и НЕ роняет интерфейс (принцип Пока-ёкэ).
        """
        row.set_ai_stream(answer, thinking)
        row.finish_ai_stream()
        # Второй контур памяти: финальный ответ стерилизуется — черновик мыслей
        # <thinking> намертво вырезается, а JSON-пакеты команд и текстовые итоги
        # сохраняются, чтобы модель помнила прошлые геометрические построения.
        clean_answer = clean_response_for_history(answer)
        if clean_answer:
            self._chat_history.append({"role": "assistant", "content": clean_answer})
        # Активация исполнительного контура: автономный вызов кубиков из реестра.
        results = execute_tool_commands(answer)
        # Предохранитель индикатора CAD мог заблокировать боевой пакет при
        # закрытой САПР — показываем явную ошибку вместо ложного отчёта об успехе.
        for item in results:
            if item.get("tool_name") == CAD_FUSE_MARKER:
                self._show_cad_fuse_error(item.get("result", ""))
        self._stream_raw = ""
        self._scroll_to_bottom()
        # Ответ зафиксирован в истории — обновляем бар контекста (Зона 3).
        self._refresh_context_bar()
        # Ответ ассистента добавлен — сохраняем сессию в JSON-файл на диск.
        self._persist_chat()

    def _show_cad_fuse_error(self, result: str) -> None:
        """Показывает служебную ошибку блокировки предохранителя CAD в чате.

        Срабатывает, когда роутер вернул маркер CAD_FUSE_MARKER: боевой
        JSON-пакет был отклонён, потому что индикатор сигнализирует об
        отсутствии подключения к AutoCAD (закрытая САПР). Пользователь
        видит понятную ошибку в правом нижнем углу, а не ложный успех.
        """
        try:
            message = json.loads(result or "{}").get(
                "message", "Выполнение заблокировано: AutoCAD не подключён."
            )
        except (ValueError, TypeError):
            message = "Выполнение заблокировано: AutoCAD не подключён."
        InfoBar.error(
            title="AutoCAD не подключён",
            content=message,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.BOTTOM_RIGHT,
            duration=8000,
            parent=self,
        )

    def _on_chain_thinking(self, row: MessageRow, thinking: str) -> None:
        """Обновляет спойлер рассуждений Qwen3 в одномодельном потоке.

        В отличие от _on_ai_chunk, этот обработчик НЕ трогает текст ответа: пока
        модель рассуждает внутри <thinking> (английский язык), спойлер мыслей
        обновляется целиком, а видимый русскоязычный ответ стримится отдельно
        через сигнал chunk_received.
        """
        row.set_thinking_only(thinking)
        self._scroll_to_bottom()

    def _append_ai_message(self, text: str, thinking: str = "") -> None:
        """Добавляет готовое (мгновенное) сообщение ИИ в ленту чата.

        Мгновенные ответы (зарезервированный сценарий direct_reply) тоже
        фиксируются в стерильной памяти чата, чтобы модель помнила исход диалога.
        """
        row = MessageRow(self._scroll)
        row.show_ai(text, thinking)
        clean_text = clean_response_for_history(text)
        if clean_text:
            self._chat_history.append({"role": "assistant", "content": clean_text})
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, row)
        self._scroll_to_bottom()
        self._refresh_context_bar()
        # Мгновенный ответ добавлен в память — сохраняем сессию на диск.
        self._persist_chat()

    def _launch_snipper(self) -> None:
        # Оверлей создаётся без родителя, чтобы охватить весь экран и не
        # ограничиваться геометрией окна чата (курсор и тонировка на весь экран).
        overlay = ScreenSnipperOverlay()
        overlay.captured.connect(self._on_screenshot_captured)
        overlay.exec()

    def _on_screenshot_captured(self, pixmap: QPixmap, bbox: tuple) -> None:
        # Сохраняем скриншот до момента отправки сообщения.
        self._pending_screenshot = pixmap
        # Превью живёт в Зоне 1 капсулы: set_pixmap раскрывает само превью,
        # а set_has_screenshot разворачивает всю Зону 1 (контекст сообщения).
        self._capsule.preview.set_pixmap(pixmap)
        self._capsule.set_has_screenshot(True)
        self._reposition_fade_overlay()

    def _clear_screenshot(self) -> None:
        """Закрывает превью и сбрасывает ожидающий скриншот."""
        self._clear_pending_screenshot()

    def _clear_pending_screenshot(self) -> None:
        """Скрывает превью, схлопывает Зону 1 и очищает сохранённый скриншот."""
        self._pending_screenshot = None
        if hasattr(self, "_capsule"):
            # Зона 1 схлопывается по высоте вместе со скрытием превью.
            self._capsule.set_has_screenshot(False)

    def _on_cad_status(self, connected: bool, name: str, full_path: str) -> None:
        """Обновляет светодиод и имя DWG-чертежа в Зоне 3 капсулы.

        Вызывается сигналом CadStatusPoller из главного потока GUI.
        Правило нарезки имени (ТЗ): максимум 30 символов — целиком; если имя
        длиннее — строго первые 27 символов и троеточие «...».
        """
        # Синхронизируем аппаратный предохранитель роутера с индикатором:
        # флаг False (красный светодиод «Нет подключения») намертво блокирует
        # боевые JSON-команды кубиков ещё ДО их исполнения (анти-фантомное
        # черчение, Пока-ёкэ фронтенда).
        set_cad_connection_state(connected)
        if not hasattr(self, "_capsule"):
            return
        if connected and name:
            self._capsule.status_led.setText("🟢")
            self._capsule.status_led.setToolTip("Подключение к AutoCAD активно")
            display_name = name[:27] + "..." if len(name) > 30 else name
            self._capsule.dwg_label.setText(display_name)
            self._capsule.dwg_label.setToolTip(
                "Путь: {}\nФайл: {}".format(full_path or "(не сохранён)", name)
            )
        else:
            self._capsule.status_led.setText("🔴")
            self._capsule.status_led.setToolTip("AutoCAD не подключён")
            self._capsule.dwg_label.setText("Нет подключения")
            self._capsule.dwg_label.setToolTip(
                "Запустите AutoCAD и откройте чертёж."
            )

    def _refresh_context_bar(self) -> None:
        """Пересчитывает кольцо заполненности контекста (Зона 3, правое крыло).

        Максимум — фактическое контекстное окно llama-server (n_ctx) из кэша
        core_core: сеть в главном потоке не опрашивается, чтобы не замораживать
        GUI. Заполнение — грубая оценка токенов стерильной истории чата плюс
        текущий вводимый текст. Тултип кольца — текстовая строка в тысячах
        токенов с процентом (без ASCII-квадратиков).
        """
        if not hasattr(self, "_capsule"):
            return
        ring = self._capsule.context_ring
        cached = get_cached_n_ctx()
        max_tokens = int(cached) if cached else 4096
        used = sum(
            estimate_tokens(str(item.get("content", "")))
            for item in self._chat_history
        )
        used += estimate_tokens(self._capsule.input.toPlainText())
        used = max(0, min(used, max_tokens))
        percent = int(round(used / max_tokens * 100)) if max_tokens else 0
        # Кольцо и его кастомный тултип-полоска обновляются одним вызовом.
        ring.update_progress(used, max_tokens, percent)

    def _poll_context_cache(self) -> None:
        """Проверяет, наполнился ли кэш n_ctx, и пересчитывает бар один раз."""
        cached = get_cached_n_ctx()
        if cached is not None and cached != self._last_context_max:
            self._last_context_max = cached
            self._refresh_context_bar()

    def _on_server_limits_updated(self, n_ctx: int) -> None:
        """Реактивно перестраивает максимум бара при изменении n_ctx сервера.

        Слот heartbeat-поллера ServerLimitsPoller: новый максимум контекста
        (например, реальные 16384 вместо стартовых 4096) применяется на лету
        через ContextRing.update_progress — без перезапуска GUI (Пока-ёкэ
        рассинхронизации индикатора с фактическим окном llama-server).
        """
        limit = int(n_ctx or 0)
        if limit <= 0:
            return
        if limit != self._last_context_max:
            self._last_context_max = limit
            self._refresh_context_bar()

    def _reposition_fade_overlay(self) -> None:
        if not hasattr(self, "_fade_overlay") or not hasattr(self, "_capsule"): return
        origin = self._capsule.mapTo(self._canvas, QPoint(0, 0))
        h = FadeOutOverlay.HEIGHT
        self._fade_overlay.setGeometry(
            origin.x(), origin.y() - h, self._capsule.width(), h
        )
        self._fade_overlay.raise_()
        # Контейнер ввода (превью скриншота + капсула) должен лежать ПОВЕРХ
        # виньетки: иначе градиентная маска накрывает прикреплённый скриншот.
        if hasattr(self, "_input_host"):
            self._input_host.raise_()

    def _refresh_welcome(self) -> None:
        """Выставляет геометрию и текст приветствия после раскладки интерфейса."""
        if not hasattr(self, "_welcome") or not hasattr(self, "_scroll"):
            return
        self._welcome.setGeometry(self._scroll.geometry())
        self._welcome.set_mode(self._mode)
        self._welcome.raise_()

    def eventFilter(self, obj: object, event: QEvent) -> bool:
        """Пересчитывает виньетку маскировки при изменении размера ввода."""
        if obj is getattr(self, "_input_host", None):
            if event.type() == QEvent.Type.Resize:
                self._reposition_fade_overlay()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_welcome"): self._welcome.setGeometry(self._scroll.geometry())
        self._reposition_fade_overlay()

    def closeEvent(self, event) -> None:
        # Корректно завершаем фоновые опросы: AutoCAD, heartbeat-лимиты
        # llama-server и лёгкий таймер кэша контекста.
        if hasattr(self, "_cad_poller"):
            self._cad_poller.requestInterruption()
            self._cad_poller.wait(2500)
        if hasattr(self, "_limits_poller"):
            self._limits_poller.requestInterruption()
            self._limits_poller.wait(2500)
        if hasattr(self, "_context_cache_timer"):
            self._context_cache_timer.stop()
        super().closeEvent(event)

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        # Синхронизируем активный режим с выбранной вкладкой верхней панели,
        # чтобы при старте автоматически был подсвечен «Чат».
        if hasattr(self, "_segment"):
            self._segment.setCurrentItem(mode)
        if hasattr(self, "_welcome"):
            self._welcome.set_mode(mode)
            self._welcome.raise_()

    def _notify_in_dev(self, feature: str) -> None:
        InfoBar.info("В разработке", f"«{feature}» будет доступно на следующем этапе.",
                     position=InfoBarPosition.TOP, duration=3000, parent=self)

if __name__ == "__main__":
    import sys
    app = QApplication(sys.argv)
    # Гарантируем, что мигающий курсор (каретка) включён и имеет видимый темп.
    if QApplication.cursorFlashTime() <= 0:
        QApplication.setCursorFlashTime(1000)
    # Принудительно включаем тёмную тему QFluentWidgets до построения виджетов:
    # иначе текст вкладок сегмента («Чат / Помощник / Песочница») и значки
    # IconWidget/ToolButton рисуются чёрными цветом светлой темы по умолчанию.
    setTheme(Theme.DARK)
    window = MainWindow()
    window.show()
    # Передаём фокус полю ввода сразу после отображения окна, чтобы каретка
    # появилась мгновенно и начала мигать.
    QTimer.singleShot(0, window._capsule.input.setFocus)
    sys.exit(app.exec())
