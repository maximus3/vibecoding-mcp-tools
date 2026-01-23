### vibecoding-mcp-tools — локальные MCP‑серверы

Набор локальных MCP‑серверов:

1. **ask_user** — интерактивный запрос ответа у пользователя
   - Web: FastAPI + Uvicorn + системный браузер (текст и голос через Web Speech API)
   - GUI: локальное окно на PyQt6 (только текст)

2. **mcp-proxy** — агрегатор тулов из нескольких MCP серверов
   - Объединяет тулы из разных MCP бинарников
   - GUI для выбора тулов (PyQt6)
   - Автоматическая сборка бинарников
   - Поддержка любых систем сборки (ya make, cargo, go build и т.д.)

Все версии работают по stdio и подходят для интеграции с Cursor MCP.

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
Добавьте конфигурацию в `~/.cursor/mcp.json`:

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
    },
    "mcp-proxy": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/Users/imaximus3/personal/vibecoding-mcp-tools",
        "mcp_proxy_server.py"
      ]
    }
  }
}
```

Имена серверов:
- Web: `ask-user-mcp` (в коде: `name='ask-user-mcp'`)
- GUI: `ask-user-mcp-local` (в коде: `name='ask-user-mcp-local'`)
- Proxy: `mcp-proxy` (в коде: `name='mcp-proxy'`)

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

---

## MCP Proxy Server

### Назначение
MCP Proxy Server агрегирует тулы из нескольких внешних MCP серверов (бинарников) и позволяет:
- Объединять тулы из разных источников в единый MCP сервер
- Выбирать какие тулы экспортировать через GUI
- Автоматически собирать бинарники при необходимости
- Поддерживать любые системы сборки (ya make, cargo, go build, npm run build и т.д.)

### Конфигурация

Создайте файл `proxy_config.json` в корне проекта:

```json
{
  "servers": [
    {
      "name": "my-server",
      "binary": "/path/to/mcp-server-binary",
      "build_command": "cargo build --release",
      "build_cwd": "/path/to/project",
      "args": ["--option", "value"]
    },
    {
      "name": "another-server",
      "binary": "/path/to/another-binary",
      "build_command": null,
      "build_cwd": null,
      "args": []
    }
  ],
  "enabled_tools": []
}
```

**Параметры сервера:**
- `name` — имя сервера (для отображения)
- `binary` — путь к бинарнику MCP сервера
- `build_command` — команда сборки (опционально, например: `ya make -r`, `cargo build --release`)
- `build_cwd` — рабочая директория для сборки (опционально)
- `args` — аргументы для запуска бинарника
- `timeout` — таймаут на получение списка тулов в секундах (по умолчанию: 30)
- `call_timeout` — таймаут на вызов тулы в секундах (по умолчанию: 300)
- `enabled_tools` — список включённых тулов (пустой = все включены)

### Использование

**Список доступных тулов:**
```bash
make proxy-list
# или
uv run mcp_proxy_server.py --list-tools
```

**GUI конфигуратор (выбор тулов):**
```bash
make proxy-configure
# или
uv run mcp_proxy_server.py --configure
```

**Пересборка всех бинарников:**
```bash
make proxy-rebuild
# или
uv run mcp_proxy_server.py --rebuild
```

**Тест MCP протокола:**
```bash
make proxy-test
# или
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | uv run mcp_proxy_server.py
```

**Запуск proxy сервера:**
```bash
uv run mcp_proxy_server.py
```

### Примеры конфигурации

**Rust проект:**
```json
{
  "name": "rust-mcp",
  "binary": "/path/to/project/target/release/mcp-server",
  "build_command": "cargo build --release",
  "build_cwd": "/path/to/project",
  "args": [],
  "timeout": 30,
  "call_timeout": 300
}
```

**Go проект:**
```json
{
  "name": "go-mcp",
  "binary": "/path/to/project/bin/mcp-server",
  "build_command": "go build -o bin/mcp-server ./cmd/server",
  "build_cwd": "/path/to/project",
  "args": [],
  "timeout": 30,
  "call_timeout": 300
}
```

**Без сборки (готовый бинарник):**
```json
{
  "name": "prebuilt",
  "binary": "/usr/local/bin/mcp-server",
  "build_command": null,
  "build_cwd": null,
  "args": [],
  "timeout": 30,
  "call_timeout": 300
}
```

### Логи
Логи пишутся в `.logs/mcp_proxy_server.log`

---

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
