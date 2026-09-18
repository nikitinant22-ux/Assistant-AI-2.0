# -*- coding: utf-8 -*-
"""history_manager.py — локальный менеджер сессий чатов в формате JSON.

Каждая сессия диалога хранится в отдельном JSON-файле внутри папки ``history/``
в корне проекта и имеет строгую структуру:

    {
        "id": "<uuid>",
        "title": "Название чата",
        "messages": [
            {"role": "user",      "content": "..."},
            {"role": "assistant", "content": "..."},
            ...
        ]
    }

Модуль изолирует всю файловую механику от графического интерфейса: главное
окно (cad_ui_core) работает только с объектом HistoryManager, не зная деталей
имён файлов, атомарных записей и обработки битых данных.

Правила проекта:
    - все docstrings и inline-комментарии написаны строго на русском языке;
    - идентификация для машины (имена методов, ключи JSON) — на английском;
    - любые повреждённые файлы не роняют интерфейс (принцип Пока-ёкэ),
      а пропускаются с предупреждением в скрытый системный лог.
"""

from __future__ import annotations

import json          # сериализация сессии в JSON и обратно
import logging       # скрытый лог предупреждений о битых файлах сессий
import os            # пути, создание папки history/, проверка существования
import re            # разбиение первого запроса на слова для автоименования
import uuid          # генерация уникальных id сессий
from typing import Dict, List, Optional

# Модульный логгер: предупреждения о повреждённых или недоступных файлах.
_HISTORY_LOGGER = logging.getLogger("AssistantAI.history_manager")


class HistoryManager:
    """Чтение/запись локальной истории диалогов в папку ``history/`` проекта.

    Методы класса потоконезависимы и вызываются из главного потока GUI
    (дисковые операции выполняются мгновенно и не блокируют интерфейс).
    """

    # Имя папки сессий относительно корня проекта.
    HISTORY_DIR_NAME: str = "history"
    # Автоименование: максимум слов и символов в названии нового чата.
    TITLE_MAX_WORDS: int = 4
    TITLE_MAX_CHARS: int = 25

    def __init__(self, history_dir: Optional[str] = None) -> None:
        """Инициализирует менеджер и гарантирует существование папки history/.

        Аргументы:
            history_dir: путь к папке сессий; по умолчанию — подпапка ``history``
                в корне проекта (рядом с этим файлом). Необязательный параметр
                позволяет тестам указывать временный каталог.
        """
        self.history_dir: str = history_dir or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), self.HISTORY_DIR_NAME
        )
        # Папка создаётся сразу при старте приложения, чтобы первый запрос
        # пользователя гарантированно нашёл место для своего JSON-файла.
        os.makedirs(self.history_dir, exist_ok=True)

    # ------------------------------------------------------------------ пути
    def _path_for(self, session_id: str) -> str:
        """Возвращает абсолютный путь к JSON-файлу сессии по её id."""
        return os.path.join(self.history_dir, f"{session_id}.json")

    # ---------------------------------------------------------------- чтение
    def list_sessions(self) -> List[dict]:
        """Считывает все сессии из папки history, свежие — сверху списка.

        Каждая сессия дополняется служебным ключом ``updated_at`` (время
        последнего изменения файла в секундах) для сортировки. Повреждённые
        JSON-файлы пропускаются с предупреждением в лог — интерфейс при этом
        не падает (принцип Пока-ёкэ).

        Возвращает:
            list — список словарей вида {"id", "title", "messages", "updated_at"}.
        """
        sessions: List[dict] = []
        if not os.path.isdir(self.history_dir):
            return sessions
        for name in sorted(os.listdir(self.history_dir)):
            if not name.endswith(".json"):
                continue  # посторонние файлы в папке игнорируем
            path = os.path.join(self.history_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict) and data.get("id"):
                    # Добавляем время последнего изменения для сортировки.
                    data["updated_at"] = os.path.getmtime(path)
                    sessions.append(data)
                else:
                    _HISTORY_LOGGER.warning(
                        "Файл сессии без id пропущен: %s", name
                    )
            except (OSError, ValueError):
                _HISTORY_LOGGER.warning(
                    "Повреждённый файл сессии пропущен: %s", name
                )
        # Самые свежие диалоги оказываются вверху списка хаба.
        sessions.sort(key=lambda s: s.get("updated_at", 0.0), reverse=True)
        return sessions

    def load_session(self, session_id: str) -> Optional[dict]:
        """Загружает полную сессию (id, title, messages) по её id.

        Возвращает:
            dict — данные сессии, либо None, если файл отсутствует или битый.
        """
        path = self._path_for(session_id)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            _HISTORY_LOGGER.warning("Не удалось прочитать сессию: %s", session_id)
            return None

    # ---------------------------------------------------------------- запись
    def create_session(
        self, title: str, messages: Optional[List[dict]] = None
    ) -> str:
        """Создаёт физический JSON-файл новой сессии на диске.

        Аргументы:
            title: название чата (строка), пустая строка заменяется «Новый чат».
            messages: начальный массив реплик {"role", "content"} (необязателен).

        Возвращает:
            str — сгенерированный uuid новой сессии (id файла).
        """
        session_id = str(uuid.uuid4())
        data: Dict[str, object] = {
            "id": session_id,
            "title": title or "Новый чат",
            "messages": list(messages or []),
        }
        self._atomic_write(session_id, data)
        return session_id

    def save_session(
        self, session_id: str, title: str, messages: List[dict]
    ) -> None:
        """Перезаписывает JSON-файл сессии актуальным состоянием диалога.

        Вызывается после каждой добавленной реплики, поэтому файл всегда
        хранит последнюю согласованную версию беседы.
        """
        data: Dict[str, object] = {
            "id": session_id,
            "title": title or "Новый чат",
            "messages": list(messages or []),
        }
        self._atomic_write(session_id, data)

    def _atomic_write(self, session_id: str, data: dict) -> None:
        """Атомарная запись JSON: временный файл + os.replace (без битого JSON).

        Если процесс оборвётся посреди записи, на диске останется лишь
        временный ``*.tmp``-файл, а исходная сессия сохранит прежнее состояние.
        """
        path = self._path_for(session_id)
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except OSError:
            # Скрытый лог вместо исключения: сбой записи не должен ронять GUI.
            _HISTORY_LOGGER.warning("Ошибка записи сессии: %s", session_id)
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    # -------------------------------------------------------------- удаление
    def delete_session(self, session_id: str) -> bool:
        """Удаляет физический JSON-файл сессии с диска ПК.

        Возвращает:
            bool — True, если файл был удалён (или уже отсутствовал).
        """
        path = self._path_for(session_id)
        try:
            if os.path.exists(path):
                os.remove(path)
                return True
        except OSError:
            _HISTORY_LOGGER.warning("Не удалось удалить сессию: %s", session_id)
        return False

    # ------------------------------------------------------------- именование
    @staticmethod
    def make_title_from_prompt(prompt: str) -> str:
        """Автоименование сессии по первому запросу пользователя.

        Логика (ТЗ Этап 3): берутся первые 3-4 слова запроса, результирующая
        строка обрезается до 25 символов (с многоточием при обрезке). Пустой
        или полностью пробельный запрос получает безопасное имя «Новый чат».

        Аргументы:
            prompt: текст первого сообщения пользователя.

        Возвращает:
            str — готовое название чата.
        """
        words = [w for w in re.split(r"\s+", prompt.strip()) if w]
        title = " ".join(words[: HistoryManager.TITLE_MAX_WORDS])
        if len(title) > HistoryManager.TITLE_MAX_CHARS:
            # Обрезаем по границе символов и добавляем многоточие, чтобы
            # длинное название не ломало верстку строки списка чатов.
            title = title[: HistoryManager.TITLE_MAX_CHARS].rstrip() + "…"
        return title or "Новый чат"