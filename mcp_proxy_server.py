"""MCP Proxy Server - агрегирует тулы из нескольких MCP серверов.

Позволяет:
- Агрегировать тулы из нескольких внешних MCP-серверов (бинарников)
- Выбирать какие тулы экспортировать через GUI (PyQt6)
- Автоматически собирать бинарники при необходимости
- Поддерживать любые системы сборки (ya make, cargo, go build и т.д.)
"""

import argparse
import asyncio
import contextlib
import json
import logging
import pathlib
import subprocess
import sys
import typing

import mcp.server.fastmcp.server as fastmcp_server
import pydantic
from PyQt6 import QtWidgets


class ServerConfig(pydantic.BaseModel):
    """Конфигурация одного MCP сервера."""

    name: str
    binary: str
    build_command: str | None = None
    build_cwd: str | None = None
    args: list[str] = pydantic.Field(default_factory=list)


class ProxyConfig(pydantic.BaseModel):
    """Конфигурация MCP Proxy сервера."""

    servers: list[ServerConfig]
    enabled_tools: list[str] = pydantic.Field(default_factory=list)


class ToolInfo(pydantic.BaseModel):
    """Информация о туле."""

    name: str
    description: str | None = None
    server_name: str
    input_schema: dict[str, typing.Any] = pydantic.Field(default_factory=dict)


# Логгер модуля
logger = logging.getLogger('mcp_proxy_server')


def _setup_logging(level: str = 'INFO') -> None:
    """Инициализация логгера: в stderr и файл `.logs/mcp_proxy_server.log`."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(log_level)

    formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(name)s | %(message)s')

    # Консольный хендлер
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        sh = logging.StreamHandler(stream=sys.stderr)
        sh.setLevel(log_level)
        sh.setFormatter(formatter)
        logger.addHandler(sh)

    # Файловый хендлер
    try:
        project_root = pathlib.Path(__file__).resolve().parent
        logs_dir = project_root / '.logs'
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / 'mcp_proxy_server.log'
        if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
            fh = logging.FileHandler(log_file, encoding='utf-8')
            fh.setLevel(log_level)
            fh.setFormatter(formatter)
            logger.addHandler(fh)
    except Exception:
        logger.exception('Не удалось настроить файловый логгер')


def load_config() -> ProxyConfig:
    """Загружает конфигурацию из proxy_config.json."""
    project_root = pathlib.Path(__file__).resolve().parent
    config_path = project_root / 'proxy_config.json'

    if not config_path.exists():
        logger.warning('Файл конфигурации не найден: %s', config_path)
        # Создаём дефолтную конфигурацию
        default_config = ProxyConfig(servers=[], enabled_tools=[])
        save_config(default_config)
        return default_config

    try:
        with config_path.open('r', encoding='utf-8') as f:
            data = json.load(f)
        return ProxyConfig.model_validate(data)
    except Exception:
        logger.exception('Ошибка загрузки конфигурации из %s', config_path)
        raise


def save_config(config: ProxyConfig) -> None:
    """Сохраняет конфигурацию в proxy_config.json."""
    project_root = pathlib.Path(__file__).resolve().parent
    config_path = project_root / 'proxy_config.json'

    try:
        with config_path.open('w', encoding='utf-8') as f:
            json.dump(config.model_dump(), f, ensure_ascii=False, indent=2)
        logger.info('Конфигурация сохранена в %s', config_path)
    except Exception:
        logger.exception('Ошибка сохранения конфигурации в %s', config_path)
        raise


def build_binary(server: ServerConfig) -> None:
    """Собирает бинарник если указана команда сборки."""
    if not server.build_command:
        logger.debug('Сборка не требуется для %s', server.name)
        return

    build_cwd = pathlib.Path(server.build_cwd) if server.build_cwd else pathlib.Path.cwd()
    logger.info('Сборка %s: команда=%s, cwd=%s', server.name, server.build_command, build_cwd)

    try:
        result = subprocess.run(
            server.build_command,
            shell=True,
            cwd=str(build_cwd),
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info('Сборка %s завершена успешно', server.name)
        if result.stdout:
            logger.debug('Stdout: %s', result.stdout)
    except subprocess.CalledProcessError as exc:
        logger.error('Ошибка сборки %s: %s', server.name, exc.stderr)
        raise RuntimeError(f'Не удалось собрать {server.name}: {exc.stderr}') from exc


async def get_tools_from_server(server: ServerConfig) -> list[ToolInfo]:
    """Получает список тулов от одного MCP сервера."""
    binary_path = pathlib.Path(server.binary)

    # Проверяем наличие бинарника
    if not binary_path.exists():
        logger.warning('Бинарник не найден: %s', binary_path)
        # Пытаемся собрать
        if server.build_command:
            build_binary(server)
        else:
            error_msg = f'Бинарник {binary_path} не найден и команда сборки не указана'
            raise FileNotFoundError(error_msg)

    # Проверяем повторно после сборки
    if not binary_path.exists():
        error_msg = f'Бинарник {binary_path} не найден даже после сборки'
        raise FileNotFoundError(error_msg)

    logger.debug('Запрос тулов от %s (binary=%s)', server.name, binary_path)

    # Определяем команду для запуска
    # Если это Python скрипт - запускаем через Python
    cmd_args = []
    if binary_path.suffix == '.py':
        cmd_args = [sys.executable, str(binary_path), *server.args]
    else:
        cmd_args = [str(binary_path), *server.args]

    # Запускаем процесс
    proc = await asyncio.create_subprocess_exec(
        *cmd_args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Сначала отправляем initialize
    init_request = {
        'jsonrpc': '2.0',
        'id': 0,
        'method': 'initialize',
        'params': {
            'protocolVersion': '2024-11-05',
            'capabilities': {},
            'clientInfo': {'name': 'mcp-proxy', 'version': '0.1.0'},
        },
    }
    init_bytes = (json.dumps(init_request) + '\n').encode('utf-8')

    # Затем отправляем initialized notification
    initialized_notif = {'jsonrpc': '2.0', 'method': 'notifications/initialized'}
    initialized_bytes = (json.dumps(initialized_notif) + '\n').encode('utf-8')

    # Затем отправляем tools/list
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}}
    request_bytes = (json.dumps(request) + '\n').encode('utf-8')

    # Объединяем все запросы
    all_requests = init_bytes + initialized_bytes + request_bytes

    try:
        stdout_data, stderr_data = await asyncio.wait_for(proc.communicate(input=all_requests), timeout=30.0)
    except TimeoutError as exc:
        logger.exception('Таймаут получения тулов от %s', server.name)
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        raise TimeoutError(f'Таймаут получения тулов от {server.name}') from exc

    if proc.returncode != 0:
        stderr_text = stderr_data.decode('utf-8', errors='replace') if stderr_data else ''
        logger.error('Ошибка получения тулов от %s (код %s): %s', server.name, proc.returncode, stderr_text)
        raise RuntimeError(f'Ошибка получения тулов от {server.name}: {stderr_text}')

    # Парсим ответ
    stdout_text = stdout_data.decode('utf-8', errors='replace') if stdout_data else ''
    try:
        # MCP может вернуть несколько JSON объектов, разделённых переводом строки
        lines = [line.strip() for line in stdout_text.strip().split('\n') if line.strip()]
        response = None
        for line in lines:
            try:
                obj = json.loads(line)
                if obj.get('id') == 1 and 'result' in obj:
                    response = obj
                    break
            except json.JSONDecodeError:
                continue

        if not response:
            logger.error('Не найден ответ с id=1 от %s: %s', server.name, stdout_text)
            raise RuntimeError(f'Некорректный ответ от {server.name}')

        result = response.get('result', {})
        tools_list = result.get('tools', [])

        tools = []
        for tool in tools_list:
            tools.append(
                ToolInfo(
                    name=tool['name'],
                    description=tool.get('description'),
                    server_name=server.name,
                    input_schema=tool.get('inputSchema', {}),
                )
            )

        logger.info('Получено %s тулов от %s', len(tools), server.name)
        return tools

    except Exception:
        logger.exception('Ошибка парсинга ответа от %s: %s', server.name, stdout_text)
        raise


async def call_tool_on_server(server: ServerConfig, tool_name: str, arguments: dict[str, typing.Any]) -> typing.Any:
    """Вызывает тулу на указанном сервере."""
    binary_path = pathlib.Path(server.binary)

    if not binary_path.exists():
        error_msg = f'Бинарник {binary_path} не найден'
        raise FileNotFoundError(error_msg)

    logger.debug('Вызов тулы %s на %s', tool_name, server.name)

    # Определяем команду для запуска
    cmd_args = []
    if binary_path.suffix == '.py':
        cmd_args = [sys.executable, str(binary_path), *server.args]
    else:
        cmd_args = [str(binary_path), *server.args]

    # Запускаем процесс
    proc = await asyncio.create_subprocess_exec(
        *cmd_args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Сначала initialize
    init_request = {
        'jsonrpc': '2.0',
        'id': 0,
        'method': 'initialize',
        'params': {
            'protocolVersion': '2024-11-05',
            'capabilities': {},
            'clientInfo': {'name': 'mcp-proxy', 'version': '0.1.0'},
        },
    }
    init_bytes = (json.dumps(init_request) + '\n').encode('utf-8')

    # Затем initialized notification
    initialized_notif = {'jsonrpc': '2.0', 'method': 'notifications/initialized'}
    initialized_bytes = (json.dumps(initialized_notif) + '\n').encode('utf-8')

    # Отправляем запрос tools/call
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': tool_name, 'arguments': arguments}}
    request_bytes = (json.dumps(request) + '\n').encode('utf-8')

    all_requests = init_bytes + initialized_bytes + request_bytes

    try:
        stdout_data, stderr_data = await asyncio.wait_for(proc.communicate(input=all_requests), timeout=300.0)
    except TimeoutError as exc:
        logger.exception('Таймаут вызова тулы %s на %s', tool_name, server.name)
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        raise TimeoutError(f'Таймаут вызова тулы {tool_name} на {server.name}') from exc

    if proc.returncode != 0:
        stderr_text = stderr_data.decode('utf-8', errors='replace') if stderr_data else ''
        logger.error('Ошибка вызова тулы %s на %s (код %s): %s', tool_name, server.name, proc.returncode, stderr_text)
        raise RuntimeError(f'Ошибка вызова тулы {tool_name} на {server.name}: {stderr_text}')

    # Парсим ответ
    stdout_text = stdout_data.decode('utf-8', errors='replace') if stdout_data else ''
    try:
        lines = [line.strip() for line in stdout_text.strip().split('\n') if line.strip()]
        response = None
        for line in lines:
            try:
                obj = json.loads(line)
                if obj.get('id') == 1:
                    response = obj
                    break
            except json.JSONDecodeError:
                continue

        if not response:
            logger.error('Не найден ответ с id=1 от %s: %s', server.name, stdout_text)
            raise RuntimeError(f'Некорректный ответ от {server.name}')

        if 'error' in response:
            error = response['error']
            logger.error('Ошибка от сервера %s: %s', server.name, error)
            raise RuntimeError(f'Ошибка от {server.name}: {error.get("message", error)}')

        result = response.get('result', {})
        logger.info('Тула %s на %s вызвана успешно', tool_name, server.name)
        return result

    except Exception:
        logger.exception('Ошибка парсинга ответа от %s: %s', server.name, stdout_text)
        raise


def show_tools_selector_gui(tools: list[ToolInfo], current_enabled: list[str]) -> list[str]:
    """Показывает GUI для выбора тулов (PyQt6)."""
    _ = QtWidgets.QApplication([])

    dialog = QtWidgets.QDialog()
    dialog.setWindowTitle('Выбор тулов для MCP Proxy')
    dialog.resize(800, 600)

    layout = QtWidgets.QVBoxLayout(dialog)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)

    title = QtWidgets.QLabel('Выберите тулы для экспорта')
    font = title.font()
    font.setPointSize(font.pointSize() + 4)
    font.setBold(True)
    title.setFont(font)
    layout.addWidget(title)

    # Группируем тулы по серверам
    servers_map: dict[str, list[ToolInfo]] = {}
    for tool in tools:
        servers_map.setdefault(tool.server_name, []).append(tool)

    scroll = QtWidgets.QScrollArea()
    scroll.setWidgetResizable(True)
    scroll_widget = QtWidgets.QWidget()
    scroll_layout = QtWidgets.QVBoxLayout(scroll_widget)

    checkboxes: dict[str, QtWidgets.QCheckBox] = {}

    for server_name, server_tools in servers_map.items():
        # Группа для каждого сервера
        group_box = QtWidgets.QGroupBox(f'Сервер: {server_name}')
        group_layout = QtWidgets.QVBoxLayout(group_box)

        # Кнопки Select All / Deselect All для группы
        group_buttons = QtWidgets.QHBoxLayout()
        select_all_btn = QtWidgets.QPushButton('Select All')
        deselect_all_btn = QtWidgets.QPushButton('Deselect All')

        def make_select_all(tools_list: list[ToolInfo]) -> typing.Callable[[], None]:
            def _cb() -> None:
                for t in tools_list:
                    if t.name in checkboxes:
                        checkboxes[t.name].setChecked(True)

            return _cb

        def make_deselect_all(tools_list: list[ToolInfo]) -> typing.Callable[[], None]:
            def _cb() -> None:
                for t in tools_list:
                    if t.name in checkboxes:
                        checkboxes[t.name].setChecked(False)

            return _cb

        select_all_btn.clicked.connect(make_select_all(server_tools))  # type: ignore[arg-type]
        deselect_all_btn.clicked.connect(make_deselect_all(server_tools))  # type: ignore[arg-type]

        group_buttons.addWidget(select_all_btn)
        group_buttons.addWidget(deselect_all_btn)
        group_buttons.addStretch(1)
        group_layout.addLayout(group_buttons)

        # Чекбоксы для тулов
        for tool in server_tools:
            checkbox = QtWidgets.QCheckBox(tool.name)
            if tool.description:
                checkbox.setToolTip(tool.description)
            checkbox.setChecked(tool.name in current_enabled if current_enabled else True)
            checkboxes[tool.name] = checkbox
            group_layout.addWidget(checkbox)

        scroll_layout.addWidget(group_box)

    scroll_layout.addStretch(1)
    scroll.setWidget(scroll_widget)
    layout.addWidget(scroll)

    # Кнопки внизу
    buttons_layout = QtWidgets.QHBoxLayout()
    buttons_layout.addStretch(1)
    cancel_button = QtWidgets.QPushButton('Отмена')
    save_button = QtWidgets.QPushButton('Сохранить')
    save_button.setStyleSheet(
        'QPushButton { background: #0057ff; color: white; padding: 8px 14px; border-radius: 10px; }\n'
        'QPushButton:hover { background: #2a7bff; }'
    )
    buttons_layout.addWidget(cancel_button)
    buttons_layout.addWidget(save_button)
    layout.addLayout(buttons_layout)

    selected_tools: list[str] = []

    def on_save() -> None:
        for tool_name, checkbox in checkboxes.items():
            if checkbox.isChecked():
                selected_tools.append(tool_name)
        dialog.accept()

    def on_cancel() -> None:
        dialog.reject()

    save_button.clicked.connect(on_save)  # type: ignore[arg-type]
    cancel_button.clicked.connect(on_cancel)  # type: ignore[arg-type]

    dialog.exec()

    return selected_tools


async def list_all_tools(config: ProxyConfig) -> list[ToolInfo]:
    """Получает список всех тулов от всех серверов."""
    all_tools = []
    for server in config.servers:
        try:
            tools = await get_tools_from_server(server)
            all_tools.extend(tools)
        except Exception:
            logger.exception('Ошибка получения тулов от %s', server.name)

    return all_tools


def create_proxy_server(config: ProxyConfig, all_tools: list[ToolInfo]) -> fastmcp_server.FastMCP:
    """Создаёт MCP Proxy сервер."""
    server = fastmcp_server.FastMCP(
        name='mcp-proxy',
        instructions='MCP Proxy Server: агрегирует тулы из нескольких MCP серверов.',
    )

    # Фильтруем тулы согласно enabled_tools
    enabled_tools = all_tools
    if config.enabled_tools:
        enabled_tools = [t for t in all_tools if t.name in config.enabled_tools]

    logger.info('Экспортируем %s тулов', len(enabled_tools))

    # Создаём маппинг тула -> сервер
    tool_to_server: dict[str, ServerConfig] = {}
    for tool in enabled_tools:
        for srv in config.servers:
            if srv.name == tool.server_name:
                tool_to_server[tool.name] = srv
                break

    # Регистрируем каждую тулу
    for tool in enabled_tools:
        srv = tool_to_server.get(tool.name)
        if not srv:
            logger.warning('Сервер для тулы %s не найден', tool.name)
            continue

        # Создаём функцию-обёртку для каждой тулы
        def make_tool_handler(
            tool_name: str, server_cfg: ServerConfig
        ) -> typing.Callable[..., typing.Awaitable[typing.Any]]:
            async def handler(**kwargs: typing.Any) -> typing.Any:
                return await call_tool_on_server(server_cfg, tool_name, kwargs)

            return handler

        # Регистрируем тулу в FastMCP
        server.tool(
            name=tool.name,
            description=tool.description or f'Tool {tool.name} from {tool.server_name}',
        )(make_tool_handler(tool.name, srv))

    return server


async def async_main(args: argparse.Namespace) -> None:
    """Асинхронный main."""
    config = load_config()

    # --rebuild: пересобрать все бинарники
    if args.rebuild:
        logger.info('Пересборка всех бинарников...')
        for server in config.servers:
            if server.build_command:
                try:
                    build_binary(server)
                except Exception:
                    logger.exception('Ошибка сборки %s', server.name)
        logger.info('Пересборка завершена')
        return

    # Получаем список всех тулов
    all_tools = await list_all_tools(config)

    if not all_tools:
        logger.warning('Не найдено ни одной тулы во всех серверах')
        return

    # --list-tools: вывести список тулов
    if args.list_tools:
        logger.info('Доступные тулы:')
        for tool in all_tools:
            print(f'  - {tool.name} (сервер: {tool.server_name})')
            if tool.description:
                print(f'    Описание: {tool.description}')
        return

    # --configure: показать GUI для выбора
    if args.configure:
        selected = show_tools_selector_gui(all_tools, config.enabled_tools)
        if selected:
            config.enabled_tools = selected
            save_config(config)
            logger.info('Конфигурация сохранена: %s тулов выбрано', len(selected))
        else:
            logger.info('Выбор отменён')
        return


def main() -> None:
    parser = argparse.ArgumentParser(description='MCP Proxy Server')
    parser.add_argument('--configure', action='store_true', help='Открыть GUI для выбора тулов')
    parser.add_argument('--list-tools', action='store_true', help='Вывести список всех доступных тулов')
    parser.add_argument('--rebuild', action='store_true', help='Пересобрать все бинарники')
    parser.add_argument('--log-level', choices=('DEBUG', 'INFO', 'WARNING', 'ERROR'), default='INFO')
    args = parser.parse_args()

    _setup_logging(args.log_level)
    logger.info('Старт MCP Proxy Server')

    # Для команд, требующих async
    if args.rebuild or args.list_tools or args.configure:
        asyncio.run(async_main(args))
        return

    # Для запуска MCP сервера - используем синхронный путь
    config = load_config()

    # Получаем тулы синхронно через asyncio.run
    all_tools = asyncio.run(list_all_tools(config))

    if not all_tools:
        logger.warning('Не найдено ни одной тулы во всех серверах')
        return

    # Если enabled_tools пустой - включаем все тулы
    if not config.enabled_tools:
        logger.info('enabled_tools пустой - включаем все тулы')

    # Создаём и запускаем proxy сервер
    proxy = create_proxy_server(config, all_tools)
    proxy.run(transport='stdio')


if __name__ == '__main__':
    main()
