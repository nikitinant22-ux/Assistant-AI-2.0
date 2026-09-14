# -*- coding: utf-8 -*-
"""Модуль дизайн-кода интерфейса «Бесшовного пространства» (Канон 16.5)."""

from __future__ import annotations
from typing import Dict, List
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

WELCOME_SCREENS: Dict[str, Dict[str, List[str]]] = {
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
        /* Отступы подстраиваются под увеличенные кнопки капсулы.
           Вертикальные отступы симметричны, чтобы нижняя строка текста
           не прижималась к границе бокса. */
        padding-left: 48px;
        padding-right: 96px;
        padding-top: 10px;
        padding-bottom: 10px;
    }}

    QPushButton#plusBtn, QPushButton#cameraBtn, QPushButton#sendBtn {{
        background-color: transparent;
        border: none;
        color: {p.TEXT};
        border-radius: 8px;
    }}
    /* Крупный знак «+» на кнопке прикрепления. */
    QPushButton#plusBtn {{
        font-size: 20px;
        font-weight: 600;
        padding-top: 0px;
        padding-bottom: 3px;
    }}
    QPushButton#plusBtn:hover, QPushButton#cameraBtn:hover, QPushButton#sendBtn:hover {{
        background-color: {p.BUBBLE};
        color: {p.TEXT};
    }}
    /* Отключённая кнопка отправки (пустой бокс): прозрачная, без подсветки. */
    QPushButton#sendBtn:disabled {{
        background-color: transparent;
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
