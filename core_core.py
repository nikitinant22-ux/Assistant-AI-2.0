# -*- coding: utf-8 -*-
"""core_core.py — Единый фундамент связи GUI с локальным llama-server (Канон 8, 11.2).

Данный модуль — единственный разрешённый канал обмена данными между графической
оболочкой и локальным сервером llama.cpp (llama-server), который поднимает единую
монолитную модель Qwen3 через OpenAI-совместимый шлюз. Модуль реализует:

    1. Класс фонового потока ``LlamaWorker(QThread)`` — изолирует все сетевые
       запросы к ``/v1/chat/completions`` от главного потока интерфейса, чтобы
       окно приложения не зависало ни на миллисекунду.
    2. Потоковый (Streaming) вывод текста через SSE-события: каждый новый кусок
       видимого ответа немедленно уходит в GUI через сигнал ``chunk_received``.
    3. Маршрутизацию потоковых токенов по Лингвистическому Барьеру (Канон 16.5):
       рассуждения модели внутри тегов ``<thinking>...</thinking>`` собираются
       отдельно и транслируются сигналом ``thinking_changed`` (спойлер мыслей,
       язык рассуждений — английский), а весь текст после закрывающего тега —
       строго русскоязычный ответ — плавно выводится в основное тело сообщения.

Старая многомодельная цепочка (DeepSeek-R1 -> Mistral-Small / Qwen через Ollama)
полностью ликвидирована: все запросы обслуживает ОДНА модель Qwen3 в одном
фоновом потоке LlamaWorker.

Все комментарии и строки документации написаны строго на русском языке.
"""

from __future__ import annotations

import json          # разбор полезной нагрузки SSE-событий llama-server
import logging       # консольный лог предупреждений опроса лимитов сервера
import os            # чтение переменной окружения LLAMA_SERVER_URL
import re            # регулярные выражения для выделения блока <thinking>
import threading     # фоновый прогрев кэша лимитов без блокировки GUI
from typing import Iterable, Optional

import requests      # HTTP-клиент: requests с stream=True для построчного чтения
# Виджетные типы Qt для мультимодального контента (Этап 4): QBuffer/QByteArray/
# QIODevice кодируют QPixmap скриншота в Base64-строку Vision-сообщения.
from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap


# Базовый адрес OpenAI-совместимого эндпоинта llama-server. Держим в одной
# константе, чтобы при переносе проекта на другой хост менять URL в одном месте.
# Значение по умолчанию — фактический запуск пользователя: llama-server с
# флагами --host 127.0.0.1 --port 8080. При необходимости адрес переопределяется
# переменной окружения LLAMA_SERVER_URL (без правки кода).
LLAMA_SERVER_URL: str = os.environ.get(
    "LLAMA_SERVER_URL", "http://127.0.0.1:8080/v1/chat/completions"
)

# Единственная модель всей системы — монолитная Qwen3, запущенная через llama-server.
MODEL_QWEN: str = "Qwen3.8-27B-IQ3-MIX"

# Понятное пользователю сообщение о сбое связи с llama-server (на русском языке).
LLAMA_ERROR_MESSAGE: str = (
    "Локальный сервер llama.cpp (llama-server) не отвечает. Убедитесь, что он "
    "запущен и модель Qwen3.8-27B-IQ3-MIX загружена на хосте 127.0.0.1:8080."
)

# Модульный логгер: предупреждения о недоступности сервера и сдвигах окна.
_CORE_LOGGER = logging.getLogger("AssistantAI.core_core")

# Безопасный дефолтный лимит контекста (токенов), если llama-server временно
# недоступен при опросе оборудования. Подменяется фактическим n_ctx сразу после
# первого успешного ответа /props или /v1/models (см. fetch_server_limits).
DEFAULT_N_CTX: int = 4096

# Порог использования контекстного окна: когда суммарный объём текста истории
# приближается к 75% от max_context_tokens, скользящее окно начинает вытеснять
# самые старые реплики, освобождая место под свежий контекст САПР.
CONTEXT_USAGE_THRESHOLD: float = 0.75

# Грубая оценка числа токенов: 1 символ ≈ 0.25 токена (4 символа на токен).
TOKENS_PER_CHAR: float = 0.25

# Кэш результата опроса оборудования: повторные вызовы не долбят сервер.
_CACHED_N_CTX: Optional[int] = None


def _server_base_url() -> str:
    """Возвращает корень llama-server без пути OpenAI-шлюза.

    Из полного адреса ``http://host:port/v1/chat/completions`` отбрасывается
    суффикс ``/v1/chat/completions``, чтобы получить базовый хост для служебных
    эндпоинтов ``/props`` и ``/v1/models``. Порт берётся из той же константы
    LLAMA_SERVER_URL — жёсткая привязка к конкретному порту здесь запрещена.
    """
    suffix = "/v1/chat/completions"
    if LLAMA_SERVER_URL.endswith(suffix):
        return LLAMA_SERVER_URL[: -len(suffix)].rstrip("/")
    return LLAMA_SERVER_URL.rstrip("/")


def _extract_n_ctx(data) -> Optional[int]:
    """Рекурсивно ищет первое целочисленное значение ключа ``n_ctx`` в JSON.

    Разные сборки llama.cpp кладут размер контекста в различные места:
    ``default_slot_config.n_ctx``, ``default_generation_settings.n_ctx`` либо
    в метаданные моделей из ``/v1/models``. Рекурсивный обход покрывает все
    варианты, не привязываясь к конкретной схеме ответа сервера.

    Аргументы:
        data: распарсенный JSON-ответ (dict, list или примитив).

    Возвращает:
        int | None — найденное значение n_ctx, либо None.
    """
    if isinstance(data, dict):
        value = data.get("n_ctx")
        if isinstance(value, int):
            return value
        for item in data.values():
            found = _extract_n_ctx(item)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _extract_n_ctx(item)
            if found:
                return found
    return None


def fetch_server_limits(force: bool = False) -> int:
    """Опрашивает llama-server и возвращает размер контекстного окна n_ctx.

    Отправляет лёгкий GET на служебный эндпоинт ``/props`` (fallback — OpenAI-
    совместимый ``/v1/models``) и извлекает из JSON значение ``n_ctx``
    (рекурсивный поиск покрывает ``default_generation_settings.n_ctx`` и
    другие схемы ответа llama.cpp). Результат кэшируется на время сессии:
    обычные вызовы мгновенны и сети не трогают.

    Форсированный режим (force=True) используется фоновым heartbeat-поллером
    интерфейса: кэш перезаписывается ЖИВЫМ значением при каждом тике. Это
    позволяет прогрессбару контекста перестроиться с безопасного дефолта
    4096 на реальные 16384, как только связь с llama-server восстановится
    (однократный прогрев при старте мог застать сервер выключенным).

    Если сервер недоступен: при отсутствии кэша возвращается безопасный
    дефолт DEFAULT_N_CTX (4096) — интерфейс продолжает работать; при уже
    известном лимите он СОХРАНЯЕТСЯ (не откатываемся вниз из-за временного
    таймаута, Пока-ёкэ рассинхронизации индикатора).

    Аргументы:
        force: True — игнорировать кэш и принудительно пере-опросить сервер.

    Возвращает:
        int — фактический размер контекстного окна llama-server в токенах.
    """
    global _CACHED_N_CTX
    # Без форсирования пользуемся сессионным кэшем — сеть не дёргаем.
    if not force and _CACHED_N_CTX is not None:
        return _CACHED_N_CTX

    n_ctx: Optional[int] = None
    base = _server_base_url()
    try:
        # Основной источник: служебные свойства llama-server (/props).
        resp = requests.get(base + "/props", timeout=(3, 8))
        resp.raise_for_status()
        n_ctx = _extract_n_ctx(resp.json())
        if n_ctx is None:
            # Fallback: OpenAI-совместимый список моделей (метаданные n_ctx).
            resp_models = requests.get(base + "/v1/models", timeout=(3, 8))
            resp_models.raise_for_status()
            n_ctx = _extract_n_ctx(resp_models.json())
    except requests.RequestException as exc:
        # Предупреждаем только о ПЕРВОМ падении: сервер может лежать часами,
        # и каждый тик фонового heartbeat не должен спамить консоль логами.
        if _CACHED_N_CTX is None:
            _CORE_LOGGER.warning(
                "Не удалось опросить лимиты llama-server (%s): %s. "
                "Используется безопасный дефолт n_ctx = %d.",
                base, exc, DEFAULT_N_CTX,
            )
    except ValueError as exc:
        if _CACHED_N_CTX is None:
            _CORE_LOGGER.warning(
                "Некорректный JSON при опросе лимитов llama-server: %s. "
                "Используется безопасный дефолт n_ctx = %d.",
                exc, DEFAULT_N_CTX,
            )

    if n_ctx and n_ctx > 0:
        # Живой ответ сервера: обновляем кэш актуальным значением (например,
        # 16384 вместо стартового дефолта 4096) — бар контекста перестроится.
        _CACHED_N_CTX = n_ctx
    elif _CACHED_N_CTX is None:
        # Сервер недоступен и кэша ещё нет — мягкий безопасный дефолт 4096.
        _CACHED_N_CTX = DEFAULT_N_CTX
        _CORE_LOGGER.info(
            "llama-server не сообщил n_ctx — принят безопасный дефолт %d.",
            _CACHED_N_CTX,
        )
    # При живом кэше и неудачном форсированном опросе значение НЕ меняется.
    return _CACHED_N_CTX


def get_cached_n_ctx() -> Optional[int]:
    """Возвращает закэшированный размер контекста БЕЗ сетевого вызова.

    Используется статус-баром интерфейса (бар токенов капсулы ввода): главный
    поток GUI не должен ходить в сеть синхронно, поэтому сначала читается кэш;
    daemon-поток prewarm_server_limits наполняет его асинхронно.

    Возвращает:
        Optional[int] — фактический n_ctx llama-server, либо None, если
        опрос оборудования ещё не завершился.
    """
    return _CACHED_N_CTX


def prewarm_server_limits() -> None:
    """Прогревает кэш лимитов сервера в фоновом потоке (без блокировки GUI).

    Вызывается при старте приложения: первый GET на /props уходит из daemon-
    потока, поэтому интерфейс не зависает даже при медленном ответе сервера.
    Повторные вызовы безопасны — при заполненном кэше поток не создаётся.
    """
    if _CACHED_N_CTX is not None:
        return

    def _warm() -> None:
        try:
            fetch_server_limits()
        except Exception:
            # Прогрев не должен ронять приложение ни при каких условиях.
            _CORE_LOGGER.warning("Фоновый прогрев лимитов llama-server прерван.")

    threading.Thread(
        target=_warm, name="server-limits-prewarm", daemon=True
    ).start()


def estimate_tokens(text: str) -> int:
    """Грубо оценивает число токенов текста (1 символ ≈ 0.25 токена).

    Аргументы:
        text: произвольный текст сообщения (уже очищен от <thinking>).

    Возвращает:
        int — оценка числа токенов (минимум 1, чтобы пустая строка давала 0).
    """
    return max(1, int(len(text or "") * TOKENS_PER_CHAR))


# ---------------------------------------------------------------------------
# Конвертация скриншота в Base64 (мультимодальный Vision-контент, Этап 4)
# ---------------------------------------------------------------------------

def convert_pixmap_to_base64_string(pixmap: QPixmap) -> str:
    """Кодирует QPixmap (Зона 1 капсулы) в чистую строку Base64 в формате PNG.

    Стандартный механизм Qt: пиксмап сохраняется в QByteArray через QBuffer
    (формат PNG), после чего байты кодируются Base64 и декодируются в UTF-8.
    Итоговая строка подставляется в data URL вида
    ``data:image/png;base64,...`` для поля image_url Vision-сообщения.

    Аргументы:
        pixmap: захваченный «Ножницами» скриншот САПР (может быть null).

    Возвращает:
        str — чистая Base64-строка PNG, либо пустая строка при сбое
        сохранения (принцип Пока-ёкэ: ошибка кодирования не роняет поток).
    """
    if pixmap is None or pixmap.isNull():
        return ""
    byte_array = QByteArray()
    buffer = QBuffer(byte_array)
    try:
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not pixmap.save(buffer, "PNG"):
            return ""
        # toBase64() возвращает QByteArray; .data() даёт bytes; decode -> UTF-8.
        return byte_array.toBase64().data().decode("utf-8")
    finally:
        # QBuffer держит ссылку на byte_array — буфер обязательно закрываем.
        buffer.close()


# ---------------------------------------------------------------------------
# Парсинг тегов рассуждений <thinking>...</thinking>
# ---------------------------------------------------------------------------

# Регулярное выражение закрытого блока рассуждений (без учёта регистра, с
# допуском пробелов вокруг имени тега и DOTALL для многострочного содержимого).
_THINK_BLOCK_RE = re.compile(r"<\s*thinking\s*>(.*?)<\s*/\s*thinking\s*>",
                             re.IGNORECASE | re.DOTALL)
# Имена открывающего и закрывающего тегов как литеральные подстроки.
_THINK_OPEN = "<thinking>"
_THINK_CLOSE = "</thinking>"


def split_thinking(raw: str) -> tuple:
    """Разделяет сырой поток LLM на три компонента.

    Работает на «лету» во время стриминга: вызывается на каждом вновь
    накопленном фрагменте текста и возвращает актуальное состояние разбора.

    Аргументы:
        raw: весь накопленный на данный момент сырой текст ответа модели.

    Возвращает:
        tuple (answer, thinking, in_thinking):
            answer     — видимый текст ВНЕ тегов рассуждений;
            thinking   — содержимое блоков <thinking>...</thinking> (без тегов);
            in_thinking— True, если последний открытый тег ещё не закрыт
                         (модель прямо сейчас рассуждает, ответа ещё нет).
    """
    raw = raw or ""

    # 1. Есть закрытый блок рассуждений — всё, что после него, это ответ.
    last_close = raw.rfind(_THINK_CLOSE)
    if last_close != -1:
        tail = raw[last_close + len(_THINK_CLOSE):]
        thinking = "\n".join(
            m.group(1).strip() for m in _THINK_BLOCK_RE.finditer(raw)
        )
        return tail.strip(), thinking.strip(), False

    # 2. Закрытых блоков нет, но есть открытый тег — модель рассуждает сейчас.
    last_open = raw.rfind(_THINK_OPEN)
    if last_open != -1:
        thinking = "\n".join(
            m.group(1).strip() for m in _THINK_BLOCK_RE.finditer(raw)
        )
        # Незакрытый «хвост» рассуждений добавляем к общему блоку мыслей.
        tail_think = raw[last_open + len(_THINK_OPEN):].strip()
        if tail_think:
            thinking = ((thinking + "\n" + tail_think).strip()
                        if thinking else tail_think)
        return "", thinking, True

    # 3. Тегов рассуждений нет вовсе — весь текст является ответом.
    return raw.strip(), "", False


# ---------------------------------------------------------------------------
# Бронированный сборщик NDJSON/SSE-строк (Канон Daman / Канон 14.3)
# ---------------------------------------------------------------------------

# Байт переноса строки — разделитель объектов в потоковом ответе сервера.
_LINE_SEP = b"\n"

# Префикс полезной нагрузки SSE-события OpenAI-совместимого шлюза.
_SSE_PREFIX = "data:"


def iter_response_lines(response) -> Iterable[str]:
    """Построчно отдаёт декодированные строки из потокового ответа.

    Бронированный сборщик, исключающий обрыв ответа на полуслове конструкцией,
    а не инструкцией быть внимательным (Канон 14.3). Накопленные сырые байты
    разбиваются ТОЛЬКО по символу переноса строки. Если чанк пришёл разорванным
    пополам — например, TCP-сегмент разрезал JSON-объект или многобайтовый символ
    UTF-8 — строка склеивается целиком в буфере и декодируется лишь после того,
    как станет полной. Так служебные \n и \r на концах строк больше не теряются,
    а незавершённый хвост без финального переноса тоже попадает в вывод.

    Аргументы:
        response: объект потокового ответа requests (с включённым stream=True).

    Возвращает:
        Итератор декодированных строк (без пустых разделителей).
    """
    buffer = b""
    for chunk in response.iter_content(chunk_size=8192):
        if not chunk:
            continue  # пустой кусок ничего не даёт — пропускаем
        buffer += chunk
        while _LINE_SEP in buffer:
            line, buffer = buffer.split(_LINE_SEP, 1)
            line = line.strip()
            if line:
                yield line.decode("utf-8", errors="replace")
    tail = buffer.strip()
    if tail:
        yield tail.decode("utf-8", errors="replace")


def iter_sse_events(line_iter: Iterable[str]) -> Iterable[str]:
    """Извлекает полезную нагрузку из строк SSE-потока llama-server.

    llama-server шлёт события вида ``data: {json}`` и финальный маркер
    ``data: [DONE]``. Строки без префикса ``data:`` (служебные комментарии
    вида ``: keep-alive``) пропускаются, чтобы они не ломали парсинг.

    Аргументы:
        line_iter: итератор декодированных строк (из iter_response_lines).

    Возвращает:
        Итератор полезных нагрузок SSE-событий (без префикса "data:").
    """
    for line in line_iter:
        line = line.strip()
        if not line.startswith(_SSE_PREFIX):
            continue  # служебная строка — пропускаем
        payload = line[len(_SSE_PREFIX):].strip()
        if payload:
            yield payload


def parse_line(payload: str) -> Optional[dict]:
    """Разбирает полезную нагрузку SSE-события в словарь.

    Если строка оказалась повреждённой или служебной (не JSON), возвращает None,
    чтобы вызывающий код мог безопасно пропустить её, не прерывая поток.

    Аргументы:
        payload: одна полезная нагрузка SSE-события.

    Возвращает:
        dict | None — распознанный объект, либо None при невозможности разбора.
    """
    try:
        return json.loads(payload)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Фоновый поток одномодельного стриминга (LlamaWorker)
# ---------------------------------------------------------------------------

class LlamaWorker(QThread):
    """Фоновый поток потокового запроса к llama-server (единая модель Qwen3).

    Никакие сетевые операции не выполняются в главном потоке GUI. Работник
    принимает промпт пользователя, системный контекст режима и текущий режим,
    а затем в методе ``run()`` стримит ответ модели в интерфейс.

    Лингвистический Барьер (Канон 16.5) обрабатывается конструкцией:
        - токены внутри незакрытого тега ``<thinking>`` уходят сигналом
          ``thinking_changed`` в спойлер ThinkingSpoiler (английский язык);
        - весь текст ПОСЛЕ закрывающего тега ``</thinking>`` — строго
          русскоязычный ответ — инкрементально уходит в ``chunk_received``
          и плавно выводится в основное тело сообщения MessageRow.

    Сигналы:
        chunk_received(str):     новый фрагмент видимого ответа модели.
        thinking_changed(str):   актуальный блок рассуждений модели целиком
                                 (обновляет раскрывающийся спойлер мыслей).
        generation_finished(str, str): кортеж (финальный_ответ, блок_мыслей) —
                                 отправляется по завершении генерации.
    """

    chunk_received = pyqtSignal(str)
    thinking_changed = pyqtSignal(str)
    generation_finished = pyqtSignal(str, str)

    def __init__(
        self,
        prompt: str,
        model: str = MODEL_QWEN,
        mode: str = "chat",
        system_prompt: Optional[str] = None,
        chat_history: Optional[list] = None,
        # Скриншот САПР из Зоны 1 капсулы: при наличии превращается в Base64
        # и упаковывается в мультимодальный Vision-контент (Этап 4).
        screenshot_pixmap: Optional[QPixmap] = None,
        max_context_tokens: Optional[int] = None,
        num_ctx: int = 32768,
        temperature: float = 0.1,
        max_tokens: int = 16384,
        parent=None,
    ) -> None:
        """Инициализирует одномодельный фоновый работник стриминга.

        Аргументы:
            prompt: текст запроса пользователя.
            model: имя модели Qwen3, загруженной в llama-server.
            mode: текущий режим интерфейса ('chat' | 'assistant' | 'sandbox').
            system_prompt: системный контекст режима (с уже зашитым
                Лингвистическим Барьером), или None для дефолтного.
            chat_history: стерильная история прошлых реплик диалога (массив
                словарей {"role": "user"|"assistant", "content": ...}). Контент
                ответов ассистента уже очищен от тегов <thinking> на стороне GUI.
                Копируется в worker, чтобы сборка messages не мутировала список
                главного потока.
            screenshot_pixmap: захваченный скриншот САПР (QPixmap из Зоны 1
                капсулы), либо None. При наличии финальное сообщение пользователя
                собирается как мультимодальный массив Vision API (текст + Base64
                image_url); иначе контент остаётся обычной текстовой строкой.
            max_context_tokens: фактический размер контекстного окна llama-server
                в токенах (результат fetch_server_limits). При None лимит
                запрашивается у сервера автоматически; при недоступности сервера
                применяется безопасный дефолт DEFAULT_N_CTX (4096).
            num_ctx: желаемый размер контекстного окна запроса в токенах
                (не превысит фактический лимит сервера max_context_tokens).
            temperature: температура сэмплирования (низкая — детерминизм).
            max_tokens: страховочный потолок длины вывода в токенах.
            parent: родительский QObject для корректного времени жизни.
        """
        super().__init__(parent)
        self._prompt = (prompt or "").strip()
        self._model = model
        self._mode = mode
        self._system_prompt = system_prompt
        # Защитная копия истории: поток worker живёт дольше вызова _dispatch,
        # поэтому мутировать исходный список GUI из фонового потока нельзя.
        self._chat_history: list = list(chat_history or [])
        # Прикреплённый скриншот (QPixmap) для мультимодального контента.
        # Пиксмап иммутабелен (implicit sharing) — безопасно читается из потока.
        self._screenshot: Optional[QPixmap] = screenshot_pixmap
        # Динамический лимит контекста сессии: явно переданное значение имеет
        # приоритет, иначе n_ctx опрашивается у llama-server (с кэшем и дефолтом).
        self.max_context_tokens: int = (
            max_context_tokens or fetch_server_limits()
        )
        self._num_ctx = num_ctx
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._raw: str = ""                 # накопленный контент (поле content)
        self._reasoning_raw: str = ""       # накопленные рассуждения (reasoning_content)
        self._emitted_answer: str = ""      # уже отправленная в GUI часть ответа
        self._last_thinking: str = ""       # последний отправленный блок мыслей

    # ------------------------------------------------------- сборка сообщений
    def _build_messages(self) -> list:
        """Динамически собирает messages под фактический контекст llama-server.

        Порядок эшелонов (двухконтурная память без тегов <thinking>):
            1. Первым элементом пакуется жёсткий системный промпт активного
               режима (Чат / Помощник / Песочница) — индекс 0, защищён от сдвига.
            2. Вторым эшелоном пристыковывается стерильная история прошлых
               реплик — ТОЛЬКО пока оценка их токенов не превышает 75% от
               self.max_context_tokens. Это динамическое скользящее окно
               (Sliding Window): самые старые реплики вытесняются сверху,
               освобождая место под свежий контекст САПР. Жёсткого лимита
               «N сообщений» здесь нет — окно управляется токенным бюджетом.
            3. Самым последним элементом встаёт свежий текущий запрос
               пользователя. При прикреплённом скриншоте его content собирается
               как мультимодальный массив Vision API (текст + Base64 image_url);
               без скриншота — обычная текстовая строка (Этап 4).

        Возвращает:
            list — список словарей вида {"role": ..., "content": ...}.
        """
        active_messages: list = []

        # 1. Жёсткий системный контекст активного режима (индекс 0).
        if self._system_prompt:
            active_messages.append(
                {"role": "system", "content": self._system_prompt}
            )

        # Токенный бюджет окна: 75% от фактического n_ctx сервера. Место под
        # системный промпт и свежий запрос резервируется заранее — они всегда
        # в приоритете, а история набирается «с конца», т.к. свежие реплики
        # (с координатами САПР) важнее древних.
        budget = max(1, int(self.max_context_tokens * CONTEXT_USAGE_THRESHOLD))
        used_tokens = (
            sum(estimate_tokens(m["content"]) for m in active_messages)
            + estimate_tokens(self._prompt)
        )

        # 2. Динамическое скользящее окно истории по токенам (pop сверху).
        kept_history: list = []
        for msg in reversed(self._chat_history):
            msg_tokens = estimate_tokens(msg["content"])
            if used_tokens + msg_tokens > budget:
                break  # бюджет исчерпан — более старые реплики срезаем
            kept_history.append({
                "role": msg["role"],
                "content": msg["content"],
            })
            used_tokens += msg_tokens
        kept_history.reverse()  # возвращаем хронологический порядок

        active_messages.extend(kept_history)

        # 3. Свежий запрос пользователя — всегда последним. Если к запросу
        #    прикреплён скриншот (Зона 1 капсулы), поле content трансформируется
        #    в массив объектов по стандарту Vision API (Пока-ёкэ): текстовая
        #    часть + Base64 data URL картинки. Без скриншота — текстовая строка.
        if self._screenshot is not None and not self._screenshot.isNull():
            base64_data = convert_pixmap_to_base64_string(self._screenshot)
            # Пустой текст (скриншот без подписи) заменяем нейтральным запросом,
            # чтобы OpenAI-совместимый шлюз не отверг пустое поле "text".
            text_part = self._prompt or (
                "Проанализируйте прикреплённый скриншот чертежа AutoCAD."
            )
            user_content = [
                {"type": "text", "text": text_part},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{base64_data}",
                    },
                },
            ]
        else:
            # Без скриншота контент остаётся обычной текстовой строкой.
            user_content = self._prompt
        active_messages.append({"role": "user", "content": user_content})
        return active_messages

    # ------------------------------------------------- маршрутизация фрагмента
    def _route_delta(self, content: str) -> None:
        """Распределяет очередной фрагмент потока между спойлером и телом ответа.

        Фрагменты внутри незакрытого ``<thinking>`` накапливаются в спойлер
        (сигнал ``thinking_changed`` — обновление целиком), а текст после
        ``</thinking>`` отправляется в тело сообщения инкрементально, только
        НОВОЙ частью — это исключает дублирование уже нарисованного текста.

        Аргументы:
            content: новый фрагмент контента модели из SSE-события.
        """
        self._raw += content
        answer, thinking, _in = split_thinking(self._raw)

        # Спойлер обновляется целиком; сравнение с прошлым значением исключает
        # холостые перерисовки, когда модель ещё не выдала новых мыслей.
        if thinking != self._last_thinking:
            self._last_thinking = thinking
            self.thinking_changed.emit(thinking)

        # Видимый ответ отдаём только приращением (префиксный инкремент):
        # GUI перерисовывает накопленный текст, поэтому лишние дубли опасны.
        if len(answer) > len(self._emitted_answer):
            new_part = answer[len(self._emitted_answer):]
            self._emitted_answer = answer
            if new_part:
                self.chunk_received.emit(new_part)

    # ------------------------------------------------------------- основной ход
    def run(self) -> None:
        """Выполняет потоковый запрос к llama-server в фоновом потоке.

        Отправляет POST на OpenAI-совместимый эндпоинт ``/v1/chat/completions``
        с ``"stream": True`` и читает SSE-поток. Полезная нагрузка каждого
        события содержит поле ``choices[0].delta.content`` — именно его фрагменты
        распределяются методом ``_route_delta`` между спойлером и телом ответа.
        По завершении (или при сбое связи) собирает финальный ответ и мысли и
        отправляет их сигналом ``generation_finished``.
        """
        payload = {
            "model": self._model,
            "messages": self._build_messages(),
            "stream": True,
            "temperature": self._temperature,
            # Контекстное окно запроса подстраивается под фактический лимит
            # сервера: просим не больше, чем сервер реально выделил (n_ctx),
            # иначе llama-server отклонит запрос или оборвёт генерацию.
            "n_ctx": min(self._num_ctx, self.max_context_tokens),
            # Страховочный потолок длины ответа (макросы в Песочнице длинные).
            "max_tokens": self._max_tokens,
        }
        try:
            # timeout=(connect, read): соединение ждём до 30 с, чтение потока —
            # до 600 с, т.к. Qwen3-27B рассуждает заметно дольше лёгких моделей.
            with requests.post(
                LLAMA_SERVER_URL,
                json=payload,
                stream=True,
                timeout=(30, 600),
            ) as resp:
                resp.raise_for_status()
                # Бронированный сборщик SSE-событий: склеивает разорванные
                # TCP-чанки, а повреждённые строки пропускает, не прерывая поток.
                for event_payload in iter_sse_events(iter_response_lines(resp)):
                    if event_payload == "[DONE]":
                        break  # сервер официально завершил генерацию
                    data = parse_line(event_payload)
                    if not data:
                        continue  # битое событие — пропускаем
                    if data.get("error"):
                        # Ошибка уровня llama-server (например, не найдена модель).
                        error_text = f"Ошибка llama-server: {data['error']}"
                        self._route_delta(error_text)
                        break
                    choices = data.get("choices") or []
                    if not choices:
                        continue  # служебное событие без контента
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    # Сборка llama-server + GGUF Qwen3 отдают рассуждения ОТДЕЛЬНЫМ
                    # полем delta.reasoning_content (БЕЗ тегов <thinking> в content).
                    # Аккумулируем их в собственный буфер и транслируем СТРОГО в
                    # спойлер мыслей — в видимое тело сообщения они не попадают.
                    reasoning = delta.get("reasoning_content")
                    if reasoning:
                        self._reasoning_raw += reasoning
                        if self._reasoning_raw != self._last_thinking:
                            self._last_thinking = self._reasoning_raw
                            self.thinking_changed.emit(self._reasoning_raw)
                    content = delta.get("content")
                    if content:
                        # Видимый ответ (и теги <thinking>, если шаблон другой
                        # сборки кладёт рассуждения прямо в content) — разбираем
                        # методом _route_delta как обычно.
                        self._route_delta(content)
                    if choice.get("finish_reason") in ("stop", "length"):
                        break  # модель завершила вывод по своей воле
        except requests.RequestException as exc:
            # Сеть недоступна или llama-server выключен — сообщаем пользователю.
            error = f"{LLAMA_ERROR_MESSAGE}\n({exc})"
            self._route_delta(error)
        except Exception as exc:
            # Любая прочая ошибка тоже должна дойти до интерфейса.
            error = f"Ошибка генерации: {exc}"
            self._route_delta(error)

        # Финальный ответ — текст вне тегов рассуждений, мысли — накопленный
        # спойлер (английский). Оба значения уходят в GUI одним сигналом.
        answer, _th, _in = split_thinking(self._raw)
        self.generation_finished.emit(answer, self._last_thinking)


# ---------------------------------------------------------------------------
# Модульный уровень (удобно для ручных проверок без запуска GUI)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # Включаем UTF-8 в консоли Windows, чтобы кириллица не падала в кодировке.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # Самопроверка парсинга тегов рассуждений (основа маршрутизации спойлера).
    sample = (
        "<thinking>User wants to draw a circle.</thinking>\n"
        "Сейчас построю окружность радиусом 10 мм."
    )
    _ans, _th, _in = split_thinking(sample)
    assert _ans == "Сейчас построю окружность радиусом 10 мм."
    assert "draw a circle" in _th
    print("Ответ:", _ans)
    print("Мысли:", _th)
    print("Внутри мыслей:", _in)

    # Самопроверка бронированного SSE-сборщика (разорванные чанки склеиваются).
    events = list(iter_sse_events(
        iter(["data: {\"choices\":[{\"delta\":{\"content\":\"Час",
              "ть 1\"}}]}", "data: [DONE]", ": keep-alive", ""])
    ))
    assert events[0] == '{"choices":[{"delta":{"content":"Час'
    assert events[1] == "[DONE]"
    assert len(events) == 2  # служебная строка ": keep-alive" отброшена
    print("Бронированный SSE-сборщик: OK")
    sys.exit(0)