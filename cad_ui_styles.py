# -*- coding: utf-8 -*-
"""cad_ui_styles.py — Единый репозиторий стилей тёмной темы интерфейса.

Данный модуль отвечает за:
    1. Настройку глобальной тёмной палитры ttk-виджетов через ttk.Style и
       системные опции option_add (включая оформление стандартного Combobox).
    2. Универсальную векторную отрисовку скруглённых прямоугольников для
       Canvas-элементов интерфейса.

Примечание: выпадающий список агентов реализован на стандартном
tk.OptionMenu в cad_ui_core.py и не зависит от ttk.Combobox, чтобы не ломать
вёрстку в тёмной теме.

Все комментарии и строки документации написаны на русском языке.
"""

import tkinter as tk
from tkinter import ttk


# Палитра тёмной темы интерфейса (используется кастомными виджетами).
DARK_BG = "#1E1E24"        # основной фон окна (глубокий графитовый)
COMBO_SELECT_BG = "#2196F3"  # подсветка выбранного элемента раскрытого списка
COMBO_BORDER = "#4E5260"   # тонкая, но заметная рамка бокса


def setup_styles(bg_color, dark_box, text_color):
    """Настраивает тёмную цветовую палитру ttk-виджетов приложения.

    Переключает тему ttk на «clam» (гибкая и легко стилизуемая), задаёт
    оформление стандартного TCombobox и системные цвета раскрывающегося
    списка (Listbox) через option_add.

    Аргументы:
        bg_color: основной цвет фона окна.
        dark_box: цвет фона полей ввода/списков.
        text_color: базовый цвет текста интерфейса.
    """
    style = ttk.Style()
    style.theme_use("clam")

    # Стандартный Combobox: тёмное поле, светлый текст, тонкая рамка.
    style.configure(
        "TCombobox",
        fieldbackground=dark_box,
        background=dark_box,
        foreground=text_color,
        arrowcolor=text_color,
        bordercolor=COMBO_BORDER,
        lightcolor=COMBO_BORDER,
        darkcolor=COMBO_BORDER,
        borderwidth=1,
        relief="flat",
        padding=6,
    )
    # Убираем нежелательные артефакты фокуса/активного состояния.
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", dark_box), ("focus", dark_box)],
        selectbackground=[("readonly", dark_box)],
        selectforeground=[("readonly", text_color)],
        foreground=[("readonly", text_color)],
        arrowcolor=[("active", "#2196F3"), ("pressed", "#2196F3")],
    )

    # Цвета раскрывающегося списка (Listbox) для всех Combobox приложения.
    root = tk._default_root
    if root:
        root.option_add("*TCombobox*Listbox.background", dark_box)
        root.option_add("*TCombobox*Listbox.foreground", text_color)
        root.option_add("*TCombobox*Listbox.selectBackground", COMBO_SELECT_BG)
        root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
        root.option_add("*TCombobox*Listbox.borderWidth", 0)
        root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 9))


def draw_round_rect(canvas, w, h, r, fill_color, tag_name):
    """Универсальная векторная отрисовка скруглённого прямоугольника.

    Рисует на Canvas сглаженный многоугольник с заданным радиусом скругления
    углов и указанным цветом заливки.

    Аргументы:
        canvas: объект tk.Canvas.
        w: ширина области.
        h: высота области.
        r: радиус скругления углов.
        fill_color: цвет заливки.
        tag_name: имя тега для последующего поиска/удаления.
    """
    canvas.delete(tag_name)
    points = [r, 0, w - r, 0, w, 0, w, r, w, h - r, w, h,
              w - r, h, r, h, 0, h, 0, h - r, 0, r, 0, 0]
    canvas.create_polygon(points, fill=fill_color, smooth=True, tags=tag_name)


def draw_round_rect_outline(canvas, w, h, r, outline_color, tag_name):
    """Рисует тонкий скруглённый контур прямоугольника (рамку).

    Используется поверх скруглённой заливки для аккуратного минималистичного
    обрамления бокса выпадающего списка.

    Аргументы:
        canvas: объект tk.Canvas.
        w: ширина области.
        h: высота области.
        r: радиус скругления углов.
        outline_color: цвет контура.
        tag_name: имя тега для последующего поиска/удаления.
    """
    canvas.delete(tag_name)
    points = [r, 0, w - r, 0, w, 0, w, r, w, h - r, w, h,
              w - r, h, r, h, 0, h, 0, h - r, 0, r, 0, 0]
    canvas.create_polygon(
        points,
        fill="",
        outline=outline_color,
        width=1,
        smooth=True,
        tags=tag_name,
    )
