# -*- coding: utf-8 -*-
"""Парящий интерфейс Fluent Design на классическом QMainWindow.

Часть 1: Импорты, спойлер рассуждений и строки сообщений чата.
"""

from __future__ import annotations
import os
from typing import Optional
from PyQt6.QtCore import (
    QByteArray, QEasingCurve, QEvent, QPoint, QPropertyAnimation,
    QRect, QRectF, QSize, Qt, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QCursor, QColor, QFontMetricsF, QGuiApplication, QIcon,
    QPainter, QPainterPath, QPalette, QPen, QPixmap,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QPlainTextEdit, QPushButton, QRubberBand, QSizePolicy, QVBoxLayout, QWidget
)
from qfluentwidgets import (
    FluentIcon, IconWidget, InfoBar, InfoBarPosition,
    SegmentedWidget, SmoothScrollArea, ToolButton, setTheme, Theme
)
import cad_ui_styles as styles

# Ядро связи: фоновый поток стриминга Ollama и разбор блока рассуждений.
from core_core import ChainedChatWorker, OllamaWorker, split_thinking
# Единая точка входа логики ИИ-суждений (Канон 11.1) — стерильный роутер-заглушка.
from main_router import route_request


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

    def enterEvent(self, event) -> None:
        # Отключённая кнопка не должна подсвечиваться (менять иконку на hover).
        if self.isEnabled() and self._hover_icon is not None:
            self.setIcon(self._hover_icon)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self._normal_icon is not None:
            self.setIcon(self._normal_icon)
        super().leaveEvent(event)

class ThinkingSpoiler(QWidget):
    """Сворачиваемый подзаголовок «Размышления...» модели DeepSeek-R1.

    Показывает подзаголовок «Размышления...», сразу после которого расположена
    стрелка вниз (▼). Тело рассуждений по умолчанию скрыто; раскрыть его можно,
    нажав на стрелку — тогда она меняется на «▲», а текст мыслей показывается.
    """
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 4, 0, 2)
        self._layout.setSpacing(2)
        # Строка заголовка: фраза «Размышления...» и стрелка вниз сразу после неё.
        self._header = QHBoxLayout()
        self._header.setSpacing(2)
        self._label = QLabel("Размышления...", self)
        self._label.setObjectName("thinkingLabel")
        self._arrow = QPushButton("▼", self)
        self._arrow.setObjectName("thinkingArrow")
        self._arrow.setCursor(Qt.CursorShape.PointingHandCursor)
        self._arrow.setCheckable(True)
        self._header.addWidget(self._label)
        self._header.addWidget(self._arrow)
        self._header.addStretch(1)
        self._body = QLabel("", self)
        self._body.setObjectName("thinkingBody")
        self._body.setWordWrap(True)
        self._body.hide()
        self._layout.addLayout(self._header)
        self._layout.addWidget(self._body)
        self._arrow.clicked.connect(self._on_toggle)

    def _on_toggle(self, checked: bool) -> None:
        self._body.setVisible(checked)
        self._arrow.setText("▲" if checked else "▼")

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
        self._arrow.setText("▲" if self._body.isVisible() else "▼")

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
        self._label = QLabel(self)
        self._label.setObjectName("aiAnswer")
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(700)
        # Горизонтально метка занимает всю доступную ширину (до 700px), а не
        # схлопывается в узкий столбец под свой sizeHint. Высота при wordWrap
        # вычисляется через heightForWidth и растёт под полный текст ответа.
        self._label.setMinimumWidth(0)
        self._label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
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
            else:
                # Курсор-«рука», когда есть что развернуть/свернуть.
                caption.setCursor(Qt.CursorShape.PointingHandCursor)
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

        txt = QLabel(bubble)
        txt.setObjectName("userBubbleText")
        txt.setWordWrap(True)
        txt.setText(text)
        txt.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
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
        self._label.setText(answer)
        if thinking:
            if self._spoiler is None:
                self._spoiler = ThinkingSpoiler(self)
                self._ai_layout.insertWidget(0, self._spoiler)
            self._spoiler.update_thinking(thinking)
        self._refresh_ai_geometry()

    def set_thinking_only(self, thinking: str) -> None:
        """Обновляет ТОЛЬКО блок мыслей Дипсика, не трогая текст ответа.

        Используется конвейером «Чат» (ChainedChatWorker): рассуждения Ведущего
        Архитектора показываются в раскрывающемся спойлере, а видимый финальный
        ответ параллельно стримит Помощник архитектора (Мистраль).
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
class InputCapsule(QFrame):
    send_requested = pyqtSignal(str)
    plus_clicked = pyqtSignal()
    camera_clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("inputCapsule")
        # Динамическая высота капсулы — растёт до MAX_LINES строк ввода.
        self.MAX_LINES: int = 6
        # Минимальная высота: вмещает самые крупные кнопки капсулы (38px).
        self.MIN_HEIGHT: int = 52
        self.input = QPlainTextEdit(self)
        self.input.setObjectName("messageInput")
        self.input.setPlaceholderText("Введите сообщение…")
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.input.textChanged.connect(self._update_send_state)
        self.input.textChanged.connect(self._resize_to_content)
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

        # Кнопка прикрепления: обычное состояние — Attach24Regular,
        # при наведении — более жирная Attach24Filled.
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

        # Кнопка скриншота: обычное состояние — CameraAdd24Regular,
        # при наведении — жирная CameraAdd24Filled.
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
        # Есть ли прикреплённый скриншот, ожидающий отправки.
        self._has_attachment = False

        # ВАЖНО: НЕ переопределяем focusInEvent/focusOutEvent экземпляра лямбдами —
        # это ломает стандартную отрисовку и мигание каретки QPlainTextEdit.
        # Подсветку капсулы и масок отслеживаем через eventFilter поля ввода:
        # это надёжнее глобального focusChanged, т.к. привязано к самому input.
        self.input.installEventFilter(self)

        self._resize_to_content()

    def _resize_to_content(self) -> None:
        """Подгоняет высоту капсулы под число строк ввода (не более MAX_LINES).

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
        height = max(self.MIN_HEIGHT, int(round(line_h * target)) + 2 * margin + 2 * v_pad + 2)
        self.setFixedHeight(height)
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
        """Учитывает прикреплённый скриншот при расчёте доступности отправки."""
        self._has_attachment = bool(has)
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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        self.input.setGeometry(0, 0, w, h)
        # Кнопки прижаты к низу бокса и не смещаются при росте высоты.
        self.plus_btn.move(6, h - 36 - 6)
        self.camera_btn.move(w - 86, h - 36 - 6)
        self.send_btn.move(w - 45, h - 38 - 6)

class MainWindow(QMainWindow):
    send_requested = pyqtSignal(str)
    # Ширина Инженерного хаба в свёрнутом (только кнопки) и развёрнутом виде.
    RAIL_WIDTH: int = 44
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

        # Кнопка создания чата: обычное состояние — Regular, при наведении — Filled.
        self._new_chat_btn = HubIconButton(
            load_svg_icon("ChatSparkle24Regular.svg"),
            load_svg_icon("ChatSparkle24Filled.svg"),
            self._hub_panel,
        )
        self._new_chat_btn.setObjectName("hubAddBtn")
        self._new_chat_btn.setFixedSize(24, 24)
        self._new_chat_btn.setIconSize(QSize(20, 20))
        self._new_chat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_chat_btn.clicked.connect(lambda: self._notify_in_dev("Новый чат"))

        # Кнопка истории чатов: обычное состояние — Regular, при наведении — Filled.
        self._history_btn = HubIconButton(
            load_svg_icon("LineHorizontal324Regular.svg"),
            load_svg_icon("LineHorizontal324Filled.svg"),
            self._hub_panel,
        )
        self._history_btn.setObjectName("hubHistBtn")
        self._history_btn.setFixedSize(24, 24)
        self._history_btn.setIconSize(QSize(20, 20))
        self._history_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._history_btn.clicked.connect(self._on_history_click)

        hub_layout.addWidget(self._new_chat_btn)
        hub_layout.addWidget(self._history_btn)

        # Черта-разделитель идёт сразу под кнопками.
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

        # В свёрнутом виде черта и чаты полностью скрыты, чтобы из-под узкой
        # полосы не торчали фрагменты названий. Появляются при развороте хаба.
        self._hub_divider.hide()
        self._history_scroll.hide()

        for title in ["Чертёж фундамента", "Спецификация кабеля", "План освещения"]:
            item = QFrame(self._history_scroll)
            item.setObjectName("chatItem")
            item.setFixedHeight(40)
            l = QHBoxLayout(item)
            l.setContentsMargins(12, 0, 6, 0)
            l.addWidget(QLabel(title, item))
            self._history_list.insertWidget(0, item)

        # Анимация ширины хаба: свёрнут до RAIL_WIDTH, разворачивается до HUB_MAX_WIDTH.
        self._drawer_anim = QPropertyAnimation(self._hub_panel, b"maximumWidth", self)
        self._drawer_anim.setDuration(200)
        self._drawer_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _on_history_click(self, event) -> None:
        self._drawer_visible = not self._drawer_visible
        # Черта и чаты существуют только в развёрнутом виде — прячем целиком,
        # а не оставляем обрезанными под узкой полосой.
        self._hub_divider.setVisible(self._drawer_visible)
        self._history_scroll.setVisible(self._drawer_visible)
        self._drawer_anim.stop()
        self._drawer_anim.setStartValue(self._hub_panel.width())
        self._drawer_anim.setEndValue(
            self.HUB_MAX_WIDTH if self._drawer_visible else self.RAIL_WIDTH
        )
        self._drawer_anim.start()

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
        # При изменении размера контейнера ввода (рост бокса, появление/скрытие
        # превью скриншота) виньетка маскировки над боксом должна следовать за
        # его верхней кромкой, иначе она «наедет» на поле ввода.
        self._input_host = host
        host.installEventFilter(self)

        self._preview = ScreenshotPreview(host)
        self._preview.closed.connect(self._clear_screenshot)
        host_layout.addWidget(self._preview, 0, Qt.AlignmentFlag.AlignLeft)

        self._capsule = InputCapsule(host)
        self._capsule.send_requested.connect(self._on_send)
        self._capsule.camera_clicked.connect(self._launch_snipper)
        self._capsule.plus_clicked.connect(lambda: self._notify_in_dev("Прикрепление файлов"))
        host_layout.addWidget(self._capsule)
        layout.addWidget(host)

    def _on_send(self, text: str) -> None:
        pixmap = self._pending_screenshot
        self.send_requested.emit(text)
        # Скриншот отображается графически в одном боксе с текстом сообщения.
        row = MessageRow(self._scroll)
        row.show_user(text, pixmap)
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, row)
        self._capsule.clear()
        self._clear_pending_screenshot()
        if not self._chat_has_messages:
            self._chat_has_messages = True
            self._welcome.hide()
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        # Отправляем запрос в роутер (заглушка зрения / маршрут на стриминг).
        self._dispatch(text, pixmap)

    def _dispatch(self, text: str, pixmap: Optional[QPixmap]) -> None:
        """Выполняет решение роутера: мгновенный ответ или потоковая генерация.

        Спрашивает main_router.route_request, который учитывает наличие скриншота
        (Предохранитель Пока-ёкэ) и режим интерфейса. Возможны два исхода:
          - "direct_reply": безопасный мгновенный ответ без обращения к Ollama;
          - "stream": запуск фонового потока OllamaWorker со стримингом текста.
        """
        has_screenshot = pixmap is not None and not pixmap.isNull()
        decision = route_request(text, self._mode, has_screenshot)

        # Мгновенный ответ (например, заглушка зрения) — без фонового потока.
        if decision.get("action") == "direct_reply":
            self._append_ai_message(
                decision.get("reply", ""),
                decision.get("thinking", ""),
            )
            return

        # Сквозная оркестрация DeepSeek-R1 -> Mistral-Small ВО ВСЕХ режимах.
        # Создаём двухфазный конвейер: размышления Дипсика стримятся в спойлер
        # «Размышления...», а итог рассуждений уходит Помощнику архитектора,
        # который формулирует финальный ответ на русском языке.
        if decision.get("action") == "chain":
            self._stream_raw = ""
            row = self._begin_ai_stream()
            worker = ChainedChatWorker(
                prompt=decision.get("user_prompt", text),
                orchestrator_model=decision.get(
                    "orchestrator_model", "deepseek-r1:14b"),
                orchestrator_prompt=decision.get("orchestrator_prompt"),
                assistant_model=decision.get(
                    "assistant_model", "mistral-small:22b"),
                assistant_prompt=decision.get("assistant_prompt"),
                mode=self._mode,
                parent=self,
            )
            # Рассуждения Ведущего Архитектора обновляют спойлер мыслей.
            worker.thinking_changed.connect(
                lambda th, r=row: self._on_chain_thinking(r, th)
            )
            worker.chunk_received.connect(
                lambda chunk, r=row: self._on_ai_chunk(r, chunk)
            )
            worker.generation_finished.connect(
                lambda ans, th, r=row: self._on_ai_finished(r, ans, th)
            )
            worker.start()
            return

        # Маршрут на потоковую генерацию: создаём строку ответа и фон. поток.
        row = self._begin_ai_stream()
        worker = OllamaWorker(
            prompt=decision.get("user_prompt", text),
            model=decision.get("model", "deepseek-r1:14b"),
            mode=self._mode,
            system_prompt=decision.get("system_prompt"),
            parent=self,
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
        """
        widget = self._scroll.widget()
        if widget is not None:
            widget.updateGeometry()
        if hasattr(self, "_chat_layout"):
            self._chat_layout.activate()
        bar = self._scroll.verticalScrollBar()
        QTimer.singleShot(0, lambda b=bar: b.setValue(b.maximum()))

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
        """Финализирует строку ответа по завершении генерации в фоне."""
        row.set_ai_stream(answer, thinking)
        row.finish_ai_stream()
        self._stream_raw = ""
        self._scroll_to_bottom()

    def _on_chain_thinking(self, row: MessageRow, thinking: str) -> None:
        """Обновляет спойлер рассуждений Дипсика в конвейере.

        В отличие от _on_ai_chunk, этот обработчик НЕ трогает текст ответа: пока
        Ведущий Архитектор рассуждает, видимый финальный ответ параллельно
        стримит Помощник архитектора (Мистраль) через сигнал chunk_received.
        """
        row.set_thinking_only(thinking)
        self._scroll_to_bottom()

    def _append_ai_message(self, text: str, thinking: str = "") -> None:
        """Добавляет готовое (мгновенное) сообщение ИИ в ленту чата."""
        row = MessageRow(self._scroll)
        row.show_ai(text, thinking)
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, row)
        self._scroll_to_bottom()

    def _launch_snipper(self) -> None:
        # Оверлей создаётся без родителя, чтобы охватить весь экран и не
        # ограничиваться геометрией окна чата (курсор и тонировка на весь экран).
        overlay = ScreenSnipperOverlay()
        overlay.captured.connect(self._on_screenshot_captured)
        overlay.exec()

    def _on_screenshot_captured(self, pixmap: QPixmap, bbox: tuple) -> None:
        # Сохраняем скриншот до момента отправки сообщения.
        self._pending_screenshot = pixmap
        self._preview.set_pixmap(pixmap)
        self._reposition_fade_overlay()
        # Прикреплённый скриншот разрешает отправку даже без текста.
        if hasattr(self, "_capsule"):
            self._capsule.set_has_screenshot(True)

    def _clear_screenshot(self) -> None:
        """Закрывает превью и сбрасывает ожидающий скриншот."""
        self._clear_pending_screenshot()

    def _clear_pending_screenshot(self) -> None:
        """Скрывает превью и очищает сохранённый скриншот."""
        self._pending_screenshot = None
        if hasattr(self, "_preview"):
            self._preview.hide()
        # Скриншот удалён — отправка снова требует наличия текста.
        if hasattr(self, "_capsule"):
            self._capsule.set_has_screenshot(False)

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
