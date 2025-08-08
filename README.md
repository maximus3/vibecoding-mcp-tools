### vibecoding-mcp-tools — локальные MCP‑серверы с тулой ask_user

Набор локальных MCP‑серверов, предоставляющих единую тулу `ask_user` для запроса ответа у человека.
Две реализации UI:
- Web: FastAPI + Uvicorn + системный браузер (поддерживает текст и голос через Web Speech API).
- GUI: локальное окно на PyQt6 (только текст).

Обе версии работают по stdio и подходят для интеграции с Cursor MCP.

### Требования
- Python >= 3.12
- Установленный `uv` (управление зависимостями и запуск)
- macOS/Windows/Linux. Для GUI‑версии достаточно `PyQt6` из зависимостей проекта.

Проверить `uv`:

```bash
uv --version
```

### Установка зависимостей

```bash
uv sync
```

`uv` создаст/синхронизирует виртуальное окружение на основе `pyproject.toml` и `uv.lock`.

### Быстрый старт (stdio, запуск MCP‑сервера)
- Web‑версия (рекомендуется для голоса):
```bash
uv run --directory /Users/imaximus3/personal/vibecoding-mcp-tools mcp_server_web.py
```
- GUI‑версия (локальное окно):
```bash
uv run --directory /Users/imaximus3/personal/vibecoding-mcp-tools mcp_server.py
```

### Интеграция с Cursor MCP
Добавьте конфигурацию в `~/.cursor/mcp.json`. Можно включить обе реализации сразу:

```json
{
  "mcpServers": {
    "ask-user-mcp": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/Users/imaximus3/personal/vibecoding-mcp-tools",
        "mcp_server_web.py"
      ]
    },
    "ask-user-mcp-local": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/Users/imaximus3/personal/vibecoding-mcp-tools",
        "mcp_server.py"
      ]
    }
  }
}
```

Имена серверов:
- Web: `ask-user-mcp` (в коде: `name='ask-user-mcp'`)
- GUI: `ask-user-mcp-local` (в коде: `name='ask-user-mcp-local'`)

### Локальная проверка (без MCP)
Готовые команды:
```bash
make run-local-web   # Web‑версия в браузере
make run-local       # GUI‑версия (PyQt6 окно)
```

Эквивалент напрямую:
```bash
# Web
printf '%s' '{"question":"CLI: проверьте отправку ответа","placeholder":"Введите ответ","hints":["Тут подсказки","Это тоже подсказка"],"quick_answers":["Ок","Готово","Отмена","Cancel"]}' \
  | uv run --directory /Users/imaximus3/personal/vibecoding-mcp-tools mcp_server_web.py --local --log-level DEBUG | cat

# GUI
printf '%s' '{"question":"CLI: проверьте отправку ответа","placeholder":"Введите ответ","hints":["Тут подсказки","Это тоже подсказка"],"quick_answers":["Ок","Готово","Отмена","Cancel"]}' \
  | uv run --directory /Users/imaximus3/personal/vibecoding-mcp-tools mcp_server.py --local --log-level DEBUG | cat
```

### Тула `ask_user`
Параметры (вызов со стороны клиента MCP/LLM):
```json
{
  "tool": "ask_user",
  "params": {
    "question": "Какой у вас email для связи?",
    "placeholder": "Введите email",
    "quick_answers": ["name@example.com"],
    "hints": ["Можно указать рабочий или личный email"],
    "timeout_seconds": 180
  }
}
```

Ответ:
- Web‑версия:
```json
{
  "answer": "name@example.com",
  "source": "text",  // или "voice"
  "duration_ms": 53210
}
```
- GUI‑версия:
```json
{
  "answer": "name@example.com",
  "source": "text",
  "duration_ms": 53210
}
```

### Поведение и безопасность
- Web: временный HTTP‑сервер на `127.0.0.1:<random_port>` (FastAPI + Uvicorn).
- Одноразовый `session_token` (UUIDv4) добавляется в URL (`/?t=<token>`) и требуется в `POST /submit`.
- Открывается системный браузер. Если авто‑закрытие вкладки недоступно, показывается подтверждение, что окно можно закрыть.
- Голосовой ввод работает при наличии Web Speech API в браузере (иначе только текстовый ввод).
- Сервер слушает только `127.0.0.1`. CORS ограничен текущим локальным origin.
- По таймауту сервер корректно завершается и вызывающая сторона получит `TimeoutError`.

### Логи
Файлы логов пишутся в каталог `.logs/` рядом с проектом:
- Web: `.logs/mcp_server_web.log`
- GUI: `.logs/mcp_server.log`

### Разработка
Полезные команды:
```bash
make format   # форматирование и авто‑фиксы (ruff)
make lint     # только проверка (ruff)
```

### Зависимости
Проект использует `pyproject.toml` и `uv.lock`. Добавить пакет:
```bash
uv add <package>
```

### Лицензия
См. `LICENSE`.
