# -*- coding: utf-8 -*-
"""core_core.py — Единый фундамент связи GUI с локальной Ollama (Канон 8, 11.2).

Данный модуль — единственный разрешённый канал обмена данными между графической
оболочкой и локальным сервером Ollama. Он реализует:

    1. Класс фонового потока ``OllamaWorker(QThread)`` — изолирует все сетевые
       запросы к Ollama от главного потока интерфейса. Прямые обращения к
       http://localhost:11434 из UI категорически запрещены, чтобы окно
       приложения не зависало ни на миллисекунду.
    2. Потоковый (Streaming) вывод текста: каждый новый кусок ответа модели
       немедленно уходит в GUI через сигнал ``chunk_received``.
    3. Аккумуляцию и парсинг тегов рассуждений DeepSeek-R1 ``<thinking>...</thinking>``:
       ход мыслей собирается отдельно и в конце передаётся вместе с очищенным
       финальным ответом через сигнал ``generation_finished``.

Поток принимает промпт пользователя, имя модели и текущий режим, а затем делает
запрос к эндпоинту ``/api/chat`` с параметром ``"stream": True`` (NDJSON-поток).

Все комментарии и строки документации написаны строго на русском языке.
"""

from __future__ import annotations

import json          # разбор NDJSON-строк, поступающих из потокового ответа Ollama
import re            # регулярные выражения для выделения блока <thinking>
from typing import Iterable, Optional

import requests      # HTTP-клиент: requests с stream=True для построчного чтения
from PyQt6.QtCore import QThread, pyqtSignal


# Базовый адрес локального API Ollama. Держим в одной константе, чтобы при
# переносе проекта на другой хост менять URL в одном месте.
OLLAMA_URL: str = "http://localhost:11434/api/chat"

# Модели, задействованные на этом этапе. deepseek-r1:14b — генератор диалога
# (умеет выдавать ход мыслей в тегах <thinking>), mistral-small:22b — инженерный
# справочник, считывающий cad_reference.md.
MODEL_DEEPSEEK: str = "deepseek-r1:14b"
MODEL_MISTRAL: str = "mistral-small:22b"

# Понятное пользователю сообщение о сбое связи с Ollama (на русском языке).
OLLAMA_ERROR_MESSAGE: str = (
    "Локальная модель Ollama не отвечает. Убедитесь, что она запущена."
)


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


def extract_clean_verdict(raw: str) -> str:
    """Жёсткая фильтрация (Пока-ёкэ): извлекает чистый вердикт Дипсика.

    Из сырого накопленного текста Ведущего Архитектора возвращается ТОЛЬКО
    финальное суждение — всё, что находится ПОСЛЕ последнего закрывающего
    тега ``</thinking>``. Внутренние размышления ``<thinking>...</thinking>``
    принудительно отсекаются и никогда не попадают на вход Помощника.

    Аргументы:
        raw: весь накопленный сырой вывод модели deepseek-r1.

    Возвращает:
        str — чистый вердикт ГИПа без тегов рассуждений (может быть пустым,
              если модель выдала лишь мысли и не сформулировала суждения).
    """
    raw = (raw or "").strip()
    if not raw:
        return ""

    # 1. Есть закрытый блок рассуждений — берём строго то, что после него.
    last_close = raw.rfind(_THINK_CLOSE)
    if last_close != -1:
        return raw[last_close + len(_THINK_CLOSE):].strip()

    # 2. Закрытого тега нет, но есть открытый — модель ещё рассуждает.
    #    Отсекаем всё, что внутри мыслей, оставляя лишь возможный текст
    #    ДО открывающего тега (обычно пуст). Мистраль мысли не получит.
    last_open = raw.rfind(_THINK_OPEN)
    if last_open != -1:
        return raw[:last_open].strip()

    # 3. Тегов рассуждений нет вовсе — весь текст является вердиктом.
    return raw


# ---------------------------------------------------------------------------
# Бронированный сборщик NDJSON-строк (Канон Daman / Канон 14.3)
# ---------------------------------------------------------------------------

# Байт переноса строки — разделитель NDJSON-объектов в потоке Ollama.
_LINE_SEP = b"\n"


def iter_response_lines(response) -> Iterable[str]:
    """Построчно отдаёт декодированные NDJSON-строки из потокового ответа.

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
        Итератор декодированных строк NDJSON (без пустых разделителей).
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


def parse_line(line: str) -> Optional[dict]:
    """Разбирает одну NDJSON-строку в словарь.

    Если строка оказалась повреждённой или служебной (не JSON), возвращает None,
    чтобы вызывающий код мог безопасно пропустить её, не прерывая поток.

    Аргументы:
        line: одна строка NDJSON.

    Возвращает:
        dict | None — распознанный объект, либо None при невозможности разбора.
    """
    try:
        return json.loads(line)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Фоновый поток стриминга Ollama
# ---------------------------------------------------------------------------

class OllamaWorker(QThread):
    """Фоновый поток, выполняющий потоковый запрос к локальной Ollama.

    Никакие сетевые операции не выполняются в главном потоке GUI. Работник
    принимает промпт, имя модели, режим и (опционально) системный промпт,
    а затем в методе ``run()`` стримит ответ модели в интерфейс.

    Сигналы:
        chunk_received(str):     каждый новый фрагмент сырого текста модели
                                 в реальном времени (передаётся как есть).
        generation_finished(str, str): кортеж (финальный_ответ, блок_мыслей),
                                 отправляется по завершении генерации.
    """

    chunk_received = pyqtSignal(str)
    generation_finished = pyqtSignal(str, str)

    def __init__(
        self,
        prompt: str,
        model: str = MODEL_DEEPSEEK,
        mode: str = "chat",
        system_prompt: Optional[str] = None,
        num_ctx: int = 16384,
        parent=None,
    ) -> None:
        """Инициализирует фоновый работник стриминга.

        Аргументы:
            prompt: текст запроса пользователя.
            model: имя модели Ollama (по умолчанию deepseek-r1:14b).
            mode: текущий режим интерфейса ('chat' | 'assistant' | 'sandbox').
            system_prompt: системный промпт модели (или None для дефолтного).
            num_ctx: размер контекстного окна в токенах. Без явной установки Ollama
                часто берёт по умолчанию 2048, чего не хватает при длинном
                системном промпте — модель обрывает ответ через пару строк.
            parent: родительский QObject для корректного времени жизни.
        """
        super().__init__(parent)
        self._prompt = (prompt or "").strip()
        self._model = model
        self._mode = mode
        self._system_prompt = system_prompt
        self._num_ctx = num_ctx
        self._raw: str = ""

    # ------------------------------------------------------- сборка сообщений
    def _build_messages(self) -> list:
        """Собирает список сообщений chat-формата для запроса к Ollama.

        Возвращает:
            list — список словарей вида {"role": ..., "content": ...}.
        """
        messages = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.append({"role": "user", "content": self._prompt})
        return messages

    # ------------------------------------------------------------- основной ход
    def run(self) -> None:
        """Выполняет потоковый запрос к Ollama в фоновом потоке.

        Читает NDJSON-поток ответа построчно, каждый фрагмент контента
        транслирует в GUI через сигнал ``chunk_received`` и накапливает в
        ``self._raw``. По завершении (или при сбое связи) разбирает накопленный
        текст на ответ и мысли и отправляет их сигналом ``generation_finished``.
        """
        payload = {
            "model": self._model,
            "messages": self._build_messages(),
            "stream": True,
            # Явно расширяем контекстное окно, иначе при большом системном
            # промпте модель упирается в дефолтный num_ctx и обрывает ответ.
            "options": {
                "num_ctx": self._num_ctx,
                "temperature": 0.7,
            },
        }
        try:
            # timeout=(connect, read): соединение ждём до 30 с, чтение потока — 90 с
            # (увеличенное время ожидания ответа модели, Канон Daman).
            with requests.post(
                OLLAMA_URL, json=payload, stream=True, timeout=(30, 90)
            ) as resp:
                resp.raise_for_status()
                # Бронированный сборщик: склеивает разорванные NDJSON-строки,
                # а повреждённые строки пропускает, не прерывая поток.
                for line in iter_response_lines(resp):
                    data = parse_line(line)
                    if not data:
                        continue  # битая/служебная строка — пропускаем
                    delta = data.get("message", {}).get("content", "")
                    if delta:
                        self._raw += delta
                        self.chunk_received.emit(delta)
                    if data.get("done", False):
                        break  # модель закончила генерацию
        except requests.RequestException as exc:
            # Сеть недоступна или Ollama выключена — сообщаем пользователю.
            error = f"{OLLAMA_ERROR_MESSAGE}\n({exc})"
            self._raw += error
            self.chunk_received.emit(error)
        except Exception as exc:
            # Любая прочая ошибка тоже должна дойти до интерфейса.
            error = f"Ошибка генерации: {exc}"
            self._raw += error
            self.chunk_received.emit(error)

        # Разбираем накопленный текст: финальный ответ + отдельный блок мыслей.
        answer, thinking, _in = split_thinking(self._raw)
        self.generation_finished.emit(answer, thinking)


# ---------------------------------------------------------------------------
# Двухфазный многомодельный конвейер режима «Чат» (Канон 16.5)
# ---------------------------------------------------------------------------


class ChainedChatWorker(QThread):
    """Сквозная оркестрация режима «Чат»: DeepSeek-R1 -> Mistral-Small.

    Фаза 1 — Ведущий Архитектор-Градостроитель (deepseek-r1:14b): принимает
    запрос инженера и системный промпт оркестрации, рассуждает на русском в
    тегах <thinking> и выдаёт сжатое, очищенное суждение.

    Фаза 2 — Помощник архитектора (mistral-small:22b): получает очищенное
    суждение Дипсика вместе со своим системным промптом и формулирует финальный
    красивый ответ строго на русском языке.

    Такой конвейер гарантирует 100% фикс русского языка и единую профессиональную
    ДНК софта: размышления прячутся в спойлер, а видимый текст генерирует Мистраль.

    Сигналы:
        chunk_received(str):    фрагмент ФИНАЛЬНОГО ответа Мистрали (реальный
                                онлайн-стрим видимого текста).
        thinking_changed(str):  актуальный блок рассуждений Дипсика — обновляет
                                раскрывающийся спойлер мыслей в реальном времени.
        generation_finished(str, str): кортеж (финальный_ответ, блок_мыслей) —
                                вызывается по завершении обеих фаз.
    """

    chunk_received = pyqtSignal(str)
    thinking_changed = pyqtSignal(str)
    generation_finished = pyqtSignal(str, str)

    def __init__(
        self,
        prompt: str,
        orchestrator_model: str = MODEL_DEEPSEEK,
        orchestrator_prompt: Optional[str] = None,
        assistant_model: str = MODEL_MISTRAL,
        assistant_prompt: Optional[str] = None,
        mode: str = "chat",
        num_ctx: int = 16384,
        parent=None,
    ) -> None:
        """Инициализирует конвейер «Чат» из двух последовательных вызовов Ollama.

        Аргументы:
            prompt: текст запроса пользователя (для Ведущего Архитектора).
            orchestrator_model: модель Диспетчера (по умолчанию deepseek-r1:14b).
            orchestrator_prompt: системный промпт оркестрации Ведущего Архитектора.
            assistant_model: модель Помощника (по умолчанию mistral-small:22b).
            assistant_prompt: системный промпт Помощника архитектора.
            mode: текущий режим интерфейса ('chat').
            num_ctx: размер контекстного окна в токенах для обеих моделей.
            parent: родительский QObject для корректного времени жизни.
        """
        super().__init__(parent)
        self._prompt = (prompt or "").strip()
        self._orchestrator_model = orchestrator_model
        self._orchestrator_prompt = orchestrator_prompt
        self._assistant_model = assistant_model
        self._assistant_prompt = assistant_prompt
        self._mode = mode
        self._num_ctx = num_ctx
        self._thinking: str = ""   # накопленный блок рассуждений Дипсика

    # ------------------------------------------------------- сборка сообщений
    @staticmethod
    def _build_messages(system_prompt: Optional[str], user_content: str) -> list:
        """Собирает сообщения chat-формата для запроса к Ollama.

        Аргументы:
            system_prompt: системный промпт модели (или None).
            user_content: текст роли пользователя.

        Возвращает:
            list — список словарей вида {"role": ..., "content": ...}.
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_content})
        return messages

    # ----------------------------------------------------------- фаза 1: Дипсик
    def _run_orchestrator(self) -> str:
        """Выполняет запрос к DeepSeek и возвращает ОЧИЩЕННОЕ суждение.

        Поток рассуждений <thinking> транслируется сигналом ``thinking_changed``
        (для спойлера), а видимый текст вне тегов становится суждением, которое
        уйдёт Помощнику. При сбое сети возвращает текст ошибки на русском.

        Возвращает:
            str — очищенное суждение Ведущего Архитектора либо сообщение об ошибке.
        """
        payload = {
            "model": self._orchestrator_model,
            "messages": self._build_messages(self._orchestrator_prompt, self._prompt),
            "stream": True,
            "options": {"num_ctx": self._num_ctx, "temperature": 0.7},
        }
        deepseek_raw = ""
        try:
            # timeout=(connect, read): ждём соединение до 30 с, чтение потока — 90 с.
            with requests.post(
                OLLAMA_URL, json=payload, stream=True, timeout=(30, 90)
            ) as resp:
                resp.raise_for_status()
                # Бронированный сборщик NDJSON-строк (склейка разорванных чанков).
                for line in iter_response_lines(resp):
                    data = parse_line(line)
                    if not data:
                        continue  # битая/служебная строка — пропускаем
                    msg = data.get("message", {}) or {}
                    delta = msg.get("content") or ""
                    # Сборки deepseek-r1 отдают рассуждения в РАЗНЫХ полях:
                    # "thinking" (текущая сборка Ollama), "reasoning_content",
                    # либо тегами <thinking> внутри "content". Учитываем все.
                    field_thinking = (
                        msg.get("thinking")
                        or msg.get("reasoning_content")
                        or ""
                    )
                    if field_thinking:
                        # Поле может приходить КУМУЛЯТИВНО (весь текст каждый раз)
                        # либо ИНКРЕМЕНТАЛЬНО (каждый чанк — новый фрагмент).
                        # Обрабатываем оба варианта конструкцией:
                        #   - если новый текст начинается с уже собранного — он
                        #     кумулятивный, просто наращиваем до нового значения;
                        #   - иначе — это новый фрагмент, дописываем его в хвост.
                        if self._thinking and field_thinking.startswith(
                                self._thinking):
                            self._thinking = field_thinking
                        else:
                            self._thinking += field_thinking
                        self.thinking_changed.emit(self._thinking)
                    if delta:
                        deepseek_raw += delta
                        # Если рассуждения пришли тегами внутри content —
                        # извлекаем их и показываем в спойлере.
                        _ans, inline_thinking, _in = split_thinking(deepseek_raw)
                        if inline_thinking and not field_thinking:
                            if inline_thinking != self._thinking:
                                self._thinking = inline_thinking
                                self.thinking_changed.emit(self._thinking)
                    if data.get("done", False):
                        break
        except requests.RequestException as exc:
            error = f"{OLLAMA_ERROR_MESSAGE}\n({exc})"
            deepseek_raw = error
            self.thinking_changed.emit(error)
        except Exception as exc:
            error = f"Ошибка генерации: {exc}"
            deepseek_raw = error
            self.thinking_changed.emit(error)

        # Жёсткая фильтрация (Пока-ёкэ): чистый вердикт — ТОЛЬКО текст ПОСЛЕ
        # закрытого тега </thinking>. К этому моменту мысли Дипсика уже ушли
        # в спойлер чата в реальном времени (см. цикл выше), поэтому здесь их
        # можно безопасно отсечь. Внутренние размышления на вход Помощника
        # НИКОГДА не попадают — никакого fallback на сырые теги нет.
        return extract_clean_verdict(deepseek_raw)

    # ------------------------------------------------------------- основной ход
    def run(self) -> None:
        """Выполняет двухфазный конвейер «Чат» в фоновом потоке.

        Сначала запрашивает DeepSeek (мысли уходят в спойлер), затем передаёт
        очищенное суждение Мистрали и стримит финальный ответ в интерфейс.
        """
        # Фаза 1: Ведущий Архитектор рассуждает и формирует суждение.
        judgment = self._run_orchestrator()

        # Фаза 2: Помощник архитектора формулирует красивый финальный ответ.
        # Бронированный Конвейерный Шаблон (Prompt Wrapper): чистый вердикт
        # Дипсика связывается с исходным запросом инженера в строгой системной
        # структуре, чтобы Мистраль не цитировал внутренние монологи, а выдал
        # развёрнутый ответ градостроителя строго по директиве Ведущего ГИПа.
        from main_router import build_mistral_input
        mistral_user_content = build_mistral_input(judgment, self._prompt)
        payload = {
            "model": self._assistant_model,
            "messages": self._build_messages(
                self._assistant_prompt, mistral_user_content
            ),
            "stream": True,
            "options": {"num_ctx": self._num_ctx, "temperature": 0.7},
        }
        mistral_raw = ""
        try:
            # timeout=(connect, read): соединение до 30 с, чтение потока — 90 с.
            with requests.post(
                OLLAMA_URL, json=payload, stream=True, timeout=(30, 90)
            ) as resp:
                resp.raise_for_status()
                # Бронированный сборщик NDJSON-строк — финальное предложение
                # дописывается до точки, а не обрывается на разорванном чанке.
                for line in iter_response_lines(resp):
                    data = parse_line(line)
                    if not data:
                        continue  # битая/служебная строка — пропускаем
                    delta = data.get("message", {}).get("content", "")
                    if delta:
                        mistral_raw += delta
                        self.chunk_received.emit(delta)
                    if data.get("done", False):
                        break
        except requests.RequestException as exc:
            error = f"{OLLAMA_ERROR_MESSAGE}\n({exc})"
            mistral_raw = error
            self.chunk_received.emit(error)
        except Exception as exc:
            error = f"Ошибка генерации: {exc}"
            mistral_raw = error
            self.chunk_received.emit(error)

        # Финальный ответ Мистрали + блок мыслей Дипсика (для спойлера).
        answer, _th, _in = split_thinking(mistral_raw)
        self.generation_finished.emit(answer.strip(), self._thinking)


# ---------------------------------------------------------------------------
# Модульный уровень (удобно для ручных проверок без запуска GUI)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # Быстрый самопроверочный тест парсинга тегов рассуждений.
    sample = (
        "<thinking>Проверяю намерение пользователя.</thinking>\n"
        "Готов выполнить команду начертить линию."
    )
    _ans, _th, _in = split_thinking(sample)
    print("Ответ:", _ans)
    print("Мысли:", _th)
    print("Внутри мыслей:", _in)
    sys.exit(0)