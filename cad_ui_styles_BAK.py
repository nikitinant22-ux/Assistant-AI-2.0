import tkinter as tk
from tkinter import ttk

def setup_styles(bg_color, dark_box, text_color):
    """Настройка темной цветовой палитры для выпадающих списков Combobox"""
    style = ttk.Style()
    style.theme_use("clam")  
    style.configure("TCombobox", fieldbackground=bg_color, background=bg_color, foreground=text_color, arrowcolor=text_color, borderwidth=0)
    root = tk._default_root
    if root:
        root.option_add("*TCombobox*Listbox.background", dark_box)
        root.option_add("*TCombobox*Listbox.foreground", text_color)
        root.option_add("*TCombobox*Listbox.selectBackground", "#2196F3")

def draw_round_rect(canvas, w, h, r, fill_color, tag_name):
    """Универсальная векторная отрисовка скругленных углов для Canvas-элементов"""
    canvas.delete(tag_name)
    points = [r, 0,  w - r, 0,  w, 0,  w, r,  w, h - r,  w, h,  w - r, h,  r, h,  0, h,  0, h - r,  0, r,  0, 0]
    canvas.create_polygon(points, fill=fill_color, smooth=True, tags=tag_name)
