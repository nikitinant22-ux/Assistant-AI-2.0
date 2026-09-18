# -*- coding: utf-8 -*-
"""Модуль дизайн-кода интерфейса «Бесшовного пространства» (Канон 16.5)."""

from __future__ import annotations
from typing import Dict, List, TypedDict
from PyQt6.QtGui import QColor, QLinearGradient, QPainter
from PyQt6.QtWidgets import QWidget

class Palette:
    """Цветовая палитра безрамочного полотна Windows 10."""
    GRAPHITE: str = "#1e1e1e"
    DRAWER: str = "#141414"
    INK: str = "#0f0f0f"
    BUBBLE: str = "#2d2d2d"
    BUBBLE_HOVER: str = "#3d4046"
    INPUT: str = "#262626"
    INPUT_FOCUS: str = "#333333"
    ACCENT: str = "#2196F3"
    ACCENT_HOVER: str = "#42a5f5"
    TEXT: str = "#e8e8e8"
    TEXT_DIM: str = "#9a9a9a"
    TEXT_HINT: str = "#6b6b6b"
    DIVIDER: str = "#2a2a2a"

MODE_CHAT: str = "chat"
MODE_ASSISTANT: str = "assistant"
MODE_SANDBOX: str = "sandbox"

MODE_LABELS: Dict[str, str] = {
    MODE_CHAT: "Чат",
    MODE_ASSISTANT: "Помощник",
    MODE_SANDBOX: "Песочница",
}

class WelcomeScreen(TypedDict):
    """Структура данных приветственного экрана для каждого режима (Канон 16.5)."""
    title: str
    subtitle: str
    examples: List[str]


WELCOME_SCREENS: Dict[str, WelcomeScreen] = {
    MODE_CHAT: {
        "title": "Привет! Я твой ИИ-Ассистент AutoCAD.",
        "subtitle": "Выбери нужного агента вверху и задай задачу. Например:",
        "examples": [
            "• «Начерти полилинию по координатам трассы» (Чертёжник)",
            "• «Проверь пересечения и найди коллизии объектов» (Аналитик)",
        ],
    },
    MODE_ASSISTANT: {
        "title": "Режим ИИ-Помощника активен.",
        "subtitle": "Я готов управлять чертежом через атомарные кубики. Например:",
        "examples": [
            "• «Заморозь все слои, кроме слоя 0»",
            "• «Поменяй цвет выделенных объектов на красный»",
        ],
    },
    MODE_SANDBOX: {
        "title": "Добро пожаловать в Песочницу кода.",
        "subtitle": "Здесь ты можете запросить генерацию скриптов или LISP-функций без их автовыполнения.",
        "examples": [],
    },
}

def build_global_stylesheet() -> str:
    p = Palette
    return f"""
    /* Главный фон окна чата — сплошной матовый графит */
    QWidget#mainCanvas, QMainWindow {{
        background-color: {p.GRAPHITE};
        color: {p.TEXT};
        font-family: "Segoe UI", "Segoe UI Variable", sans-serif;
    }}

    QWidget#controlRail {{
        background-color: {p.DRAWER};
    }}

    QWidget#drawerBody {{
        background-color: {p.DRAWER};
    }}

    /* Кнопки-иконки (создание чата, история) в Инженерном хабе. */
    QPushButton#hubAddBtn, QPushButton#hubHistBtn {{
        background-color: transparent;
        border: none;
        border-radius: 6px;
    }}
    QPushButton#hubAddBtn:hover, QPushButton#hubHistBtn:hover {{
        background-color: {p.BUBBLE};
    }}

    QScrollArea#historyScroll, QScrollArea#historyScroll > QWidget > QWidget {{
        background-color: {p.DRAWER};
        border: none;
    }}

    /* Элементы списка чатов в Инженерном хабе — светлый текст. */
    QFrame#chatItem {{
        background-color: transparent;
        border: none;
        border-radius: 8px;
    }}
    QFrame#chatItem:hover {{
        background-color: {p.BUBBLE};
    }}
    QFrame#chatItem QLabel {{
        color: {p.TEXT};
        background: transparent;
        font-size: 14px;
    }}

    QScrollArea#chatScroll, QScrollArea#chatScroll > QWidget,
    QWidget#chatViewport, QWidget#viewport {{
        background-color: {p.GRAPHITE} !important;
        background: transparent !important;
        border: none !important;
    }}

    QFrame#inputCapsule {{
        background-color: {p.INPUT};
        border-radius: 20px;
        border: 1px solid transparent;
    }}
    QFrame#inputCapsule[focused="true"] {{
        background-color: {p.INPUT_FOCUS};
        border: 1px solid {p.ACCENT};
    }}

    QPlainTextEdit#messageInput {{
        background-color: transparent;
        border: none;
        color: {p.TEXT};
        font-size: 14px;
        /* Кнопок внутри поля больше нет (трёхзонная капсула) — отступы
           симметричные, чтобы текст не прижимался к краям бокса. */
        padding-left: 12px;
        padding-right: 12px;
        padding-top: 10px;
        padding-bottom: 10px;
    }}

    QPushButton#plusBtn, QPushButton#cameraBtn, QPushButton#sendBtn {{
        background-color: transparent;
        border: none;
        color: {p.TEXT};
        border-radius: 8px;
    }}
    QPushButton#plusBtn:hover, QPushButton#cameraBtn:hover, QPushButton#sendBtn:hover {{
        background-color: {p.BUBBLE};
        color: {p.TEXT};
    }}
    /* Отключённая кнопка отправки (пустой бокс): прозрачная, без подсветки. */
    QPushButton#sendBtn:disabled {{
        background-color: transparent;
    }}

    /* Светодиод-индикатор подключения к AutoCAD (Зона 3, левое крыло). */
    QLabel#statusLed {{
        background-color: transparent;
        font-size: 13px;
    }}

    /* Имя текущего DWG-чертежа с многострочным тултипом (Зона 3). */
    QLabel#dwgNameLabel {{
        background-color: transparent;
        color: {p.TEXT_DIM};
        font-size: 13px;
    }}

    /* Тонкий парящий бар заполненности контекста (Fluent Design, Зона 3). */
    QProgressBar#contextBar {{
        background-color: rgba(255, 255, 255, 0.07);
        border: none;
        border-radius: 3px;
        min-height: 6px;
        max-height: 6px;
    }}
    QProgressBar#contextBar::chunk {{
        background-color: {p.ACCENT};
        border-radius: 3px;
    }}

    /* Карточка-подсказка кольца контекста: текст токенов + полоска. */
    QFrame#contextRingTip {{
        background-color: #1a1a1a;
        border: 1px solid {p.DIVIDER};
        border-radius: 8px;
    }}
    QLabel#contextTipLabel {{
        background-color: transparent;
        color: {p.TEXT};
        font-size: 12px;
    }}

    /* Кастомный многострочный тултип капсулы (путь, имя DWG, токены). */
    QToolTip {{
        background-color: {p.INK};
        color: {p.TEXT};
        border: 1px solid {p.DIVIDER};
        border-radius: 6px;
        padding: 6px 8px;
        font-size: 12px;
    }}

    /* 
      Капсульный переключатель трёх режимов (SegmentedWidget).
      Жесткое экранирование фигурных скобок для корректной f-строки.
    */
    SegmentedWidget {{
        background-color: {p.INPUT};
        border-radius: 14px;
        padding: 2px;
    }}

    /* Принудительно красим внутренний текст элементов SegmentedWidget */
    SegmentedWidget *, SegmentedWidget QLabel, SegmentedWidget QPushButton, PivotItem {{
        color: {p.TEXT} !important;
    }}

    PivotItem:hover, SegmentedItem:hover {{
        background-color: {p.BUBBLE} !important;
        color: white !important;
    }}

    PivotItem[isSelected=true], SegmentedItem[isSelected=true] {{
        color: white !important;
    }}

    /* Пузырь сообщения пользователя: контейнер, внутри скриншот + текст. */
    QWidget#userBubble {{
        background-color: {p.BUBBLE};
        border-radius: 16px;
    }}
    QLabel#userBubbleText {{
        background-color: transparent;
        color: {p.TEXT};
        font-size: 14px;
    }}

    /* Карточка сообщения со скриншотом (Fluent 2): фикс. размер 120×120,
       скругление 12px (токен --borderRadiusLarge). */
    QWidget#userCard {{
        background-color: {p.BUBBLE};
        border-radius: 12px;
    }}
    /* Подпись под миниатюрой: перенос по словам и троеточие в 2 строки
       рисуются кодом в CaptionClamp. */
    QLabel#userCardCaption {{
        background-color: transparent;
        color: {p.TEXT};
        font-size: 14px;
    }}

    QLabel#aiAnswer {{
        background-color: transparent;
        color: {p.TEXT};
        font-size: 14px;
        padding: 2px 0px;
    }}

    /* Спойлер «Размышления...» DeepSeek-R1: Fluent-складочка с индиго-акцентом. */
    QLabel#thinkingLabel {{
        background-color: transparent;
        border: none;
        color: {p.TEXT_DIM};
        font-size: 13px;
        padding: 4px 0 4px 6px;
    }}
    /* Стрелка вниз сразу после фразы «Размышления...». */
    QPushButton#thinkingArrow {{
        background-color: transparent;
        border: none;
        color: {p.TEXT_DIM};
        font-size: 12px;
        padding: 2px 6px;
        border-radius: 6px;
    }}
    QPushButton#thinkingArrow:hover {{
        color: {p.TEXT};
        background-color: rgba(255,255,255,0.08);
    }}
    /* Тело блока рассуждений: приглушённая карточка с акцентной полосой слева. */
    QLabel#thinkingBody {{
        background-color: #1a1a1a;
        color: {p.TEXT_DIM};
        font-size: 13px;
        line-height: 1.4;
        padding: 10px 12px;
        border-left: 3px solid {p.ACCENT};
        border-radius: 8px;
    }}

    QWidget#welcomeHost {{ background-color: transparent; }}
    QLabel#welcomeTitle {{ color: {p.TEXT}; font-size: 20px; font-weight: 600; }}
    QLabel#welcomeSubtitle {{ color: {p.TEXT_DIM}; font-size: 14px; }}
    QLabel#welcomeExample {{ color: {p.TEXT_HINT}; font-size: 13px; }}

    QScrollBar:vertical {{ background: transparent; width: 6px; }}
    QScrollBar::handle:vertical {{ background: {p.DIVIDER}; border-radius: 3px; }}
    QScrollBar::handle:vertical:hover {{ background: {p.BUBBLE_HOVER}; }}

    /* Пунктирная рамка фокуса (focus rect) после клика по любой кнопке —
       в Fluent-интерфейсе она не нужна: outline: none глушит её глобально,
       при этом клавиатурный фокус (Tab/Enter) сохраняется. */
    QPushButton:focus {{
        outline: none;
    }}

    /* Монолитные кликабельные строки верхней панели хаба: иконка + подпись
       упакованы в единый виджет. Иконки внутри — оригинальные (hubAddBtn/
       hubHistBtn), поэтому их стиль не меняется; подсветка теперь на строке. */
    QFrame#hubActionRow {{
        background-color: transparent;
        border: none;
        border-radius: 8px;
    }}
    QFrame#hubActionRow:hover {{
        background-color: {p.BUBBLE};
    }}
    QFrame#hubActionRow QLabel {{
        color: {p.TEXT};
        background: transparent;
        font-size: 13px;
    }}

    /* Минималистичная кнопка ✕ удаления чата в строке списка: появляется
       только при наведении курсора на строку ChatListItemWidget. */
    QToolButton#chatItemDelete {{
        background-color: transparent;
        border: none;
        color: {p.TEXT_DIM};
        border-radius: 6px;
        font-size: 14px;
        padding: 2px;
    }}
    QToolButton#chatItemDelete:hover {{
        background-color: rgba(255, 255, 255, 0.10);
        color: {p.TEXT};
    }}

    /* Подсветка активной (открытой в окне переписки) сессии в списке хаба. */
    QFrame#chatItem[active="true"] {{
        background-color: rgba(33, 150, 243, 0.16);
    }}
    """

def paint_fade_out(widget: QWidget, painter: QPainter, height: int) -> None:
    rect = widget.rect()
    base = QColor(Palette.GRAPHITE)
    # Задаём альфу ЯВНО через setAlpha: конструктор QColor("#RRGGBBAA")
    # трактует 8-й символ как первичную альфу (#AARRGGBB), из-за чего
    # строка вида "#1e1e1e00" превращалась в полупрозрачный тон, а не в 0.
    top = QColor(base); top.setAlpha(0)     # верх — полностью прозрачный
    mid = QColor(base); mid.setAlpha(0)     # зона задержки тоже прозрачна
    bottom = QColor(base); bottom.setAlpha(255)  # у ввода — плотный оттенок
    gradient = QLinearGradient(0, 0, 0, height)
    gradient.setColorAt(0.00, top)
    gradient.setColorAt(0.40, mid)
    gradient.setColorAt(1.00, bottom)
    painter.fillRect(rect, gradient)

def paint_hub_fade_line(widget: QWidget, painter: QPainter) -> None:
    w = max(1, widget.width())
    y = widget.height() // 2
    gradient = QLinearGradient(0, 0, w, 0)
    gradient.setColorAt(0.0, QColor(Palette.DIVIDER + "00"))
    gradient.setColorAt(0.5, QColor(Palette.DIVIDER))
    gradient.setColorAt(1.0, QColor(Palette.DIVIDER + "00"))
    painter.fillRect(0, y - 1, w, 2, gradient)
