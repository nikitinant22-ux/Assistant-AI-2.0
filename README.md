# Assistant AI 2.0

Ассистент для AutoCAD: многоагентная надстройка, которая принимает запрос
на естественном языке и выполняет построения в открытом чертеже через
COM-интерфейс AutoCAD.

## Устройство

Запрос поступает в диспетчер `main_router.py`, который направляет его
одному из агентов:

| Модуль | Роль |
|---|---|
| `agent_draftsman.py` | построение примитивов и геометрии |
| `agent_analyst.py` | анализ чертежа и ответы на вопросы по нему |
| `agent_operator.py` | операции над существующими объектами |

Вспомогательные слои: `cad_api.py` — вызовы модели, `cad_vdb.py` —
семантический поиск по справочнику COM-методов, `cad_vision.py` —
разбор изображений, `core_ontology.py` и `core_core.py` — состояние
и предметная модель, `cad_ui_core.py` и `cad_ui_styles.py` —
интерфейс на tkinter.

## Требования

- Windows с установленным AutoCAD (работа идёт через ActiveX/COM)
- Python 3.11
- [Ollama](https://ollama.com), запущенная локально на `http://localhost:11434`

Используемые модели — их нужно скачать заранее:

```
ollama pull qwen2.5-coder:14b-instruct-q8_0
ollama pull deepseek-r1:14b
ollama pull mistral-small:22b
ollama pull qwen2.5vl:7b
```

## Установка и запуск

```
pip install requests pyautocad pywin32 pillow psutil
python cad_ui_core.py
```

Перед запуском откройте в AutoCAD чертёж, с которым будете работать.

## Локальные файлы

`settings.json` и `chat_history.json` создаются при первом запуске,
хранят настройки и историю конкретного пользователя и в репозиторий
не попадают.
