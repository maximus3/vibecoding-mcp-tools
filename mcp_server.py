"""Локальный GUI MCP-сервер (PyQt6) с тулой ask_user.

Диалог запускается в отдельном подпроцессе. В дочерний процесс
передаём JSON с параметрами через stdin, ответ получаем через stdout.
"""

import argparse
import asyncio
import contextlib
import json
import logging
import pathlib
import sys
import time
import typing

import mcp.server.fastmcp.server as fastmcp_server
import pydantic
from PyQt6 import QtCore, QtGui, QtWidgets


class AskUserResult(pydantic.BaseModel):
    answer: str
    source: typing.Literal['text']
    duration_ms: int


# Логгер модуля
logger = logging.getLogger('ask_user_mcp_local')


def _setup_logging(level: str = 'INFO') -> None:
    """Инициализация логгера: в stderr и файл `.logs/mcp_server.log`."""
    # Приводим уровень
    log_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(log_level)

    # Форматтер
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
        log_file = logs_dir / 'mcp_server.log'
        if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
            fh = logging.FileHandler(log_file, encoding='utf-8')
            fh.setLevel(log_level)
            fh.setFormatter(formatter)
            logger.addHandler(fh)
    except Exception:
        logger.exception('Не удалось настроить файловый логгер')


def create_server() -> fastmcp_server.FastMCP:
    """Создаёт и настраивает FastMCP-сервер с тулой `ask_user` (локальный GUI на PyQt6)."""
    server = fastmcp_server.FastMCP(
        name='ask-user-mcp-local',
        instructions='Локальная тула ask_user: окно PyQt6 с текстовым вводом и отправкой по Enter.',
    )

    @server.tool(
        name='ask_user',
        title='Ask User (Local GUI)',
        description=(
            'Открывает локальное окно (PyQt6) с вопросом, полем ввода и подсказками. Возвращает ответ пользователя.'
        ),
    )
    async def ask_user(
        question: str,
        placeholder: str | None = None,
        quick_answers: list[str] | None = None,
        hints: list[str] | None = None,
        timeout_seconds: int = 180,
    ) -> AskUserResult:
        """Запрашивает ответ у пользователя через локальное окно PyQt6.

        Параметры:
        - question: обязательный вопрос
        - placeholder: необязательный плейсхолдер (серый текст в поле ввода до начала ввода)
        - quick_answers: необязательные быстрые ответы (кнопки, подставляют текст в поле)
        - hints: необязательные текстовые подсказки к вопросу/ответу (не кликабельные)
        - timeout_seconds: таймаут ожидания ответа (по умолчанию 180)

        Результат:
        - answer: итоговый ответ (строка)
        - source: всегда 'text'
        - duration_ms: длительность сессии до отправки (на стороне сервера)
        """
        start_monotonic = time.monotonic()

        payload = {
            'question': question,
            'placeholder': placeholder or '',
            'quick_answers': quick_answers or [],
            'hints': hints or [],
        }
        payload_bytes = json.dumps(payload, ensure_ascii=False).encode('utf-8')

        # Запускаем дочерний процесс с этим же модулем и спец-режимом --local
        python_exe = sys.executable
        module_path = str(pathlib.Path(__file__).resolve())

        logger.debug('Запуск диалога (timeout=%s c)...', timeout_seconds)

        proc = await asyncio.create_subprocess_exec(
            python_exe,
            '-u',
            module_path,
            '--local',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            outs, errs = await asyncio.wait_for(proc.communicate(input=payload_bytes), timeout=float(timeout_seconds))
        except TimeoutError as exc:
            logger.exception('Таймаут ожидания ответа пользователя')
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            raise TimeoutError('Ожидание ответа пользователя истекло.') from exc

        # Проверим код возврата дочернего процесса
        return_code = proc.returncode
        logger.debug('Диалог завершён: return_code=%s', return_code)
        if return_code != 0:
            # Расшифруем stderr заранее
            stderr_text = errs.decode('utf-8', errors='replace') if errs else ''
            # 2 — может означать закрытие окна пользователем или системную ошибку аргументов
            # (argparse тоже возвращает 2)
            if return_code == 2 and not stderr_text.strip():  # noqa: PLR2004
                logger.warning('Пользователь закрыл окно без отправки ответа')
                raise RuntimeError('Диалог закрыт пользователем без отправки ответа.')
            # Иная ошибка — пробросим stderr для диагностики
            logger.error('Ошибка диалога (код %s): %s', return_code, stderr_text.strip())
            raise RuntimeError(f'Ошибка диалога (код {return_code}). {stderr_text}'.strip())

        stdout_text = outs.decode('utf-8', errors='replace') if outs else ''
        try:
            result_obj = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            logger.exception('Некорректный JSON от диалога: %r', stdout_text)
            raise RuntimeError('Некорректный ответ от диалога (не JSON).') from exc

        if not isinstance(result_obj, dict):
            logger.error('Некорректный тип ответа от диалога: %r', type(result_obj))
            raise TypeError('Некорректный ответ от диалога (тип данных).')

        raw_answer = result_obj.get('answer')
        if not isinstance(raw_answer, str) or not raw_answer.strip():
            logger.error('Пустой ответ пользователя (после диалога)')
            raise RuntimeError('Пустой ответ пользователя.')

        duration_ms = int((time.monotonic() - start_monotonic) * 1000)
        logger.info('Ответ получен: duration_ms=%s', duration_ms)
        return AskUserResult(answer=raw_answer.strip(), source='text', duration_ms=duration_ms)

    return server


def _run_dialog_from_stdin() -> int:  # noqa: C901
    """Запускает окно PyQt6, получает параметры из stdin (JSON), печатает ответ в stdout.

    Возвращает код выхода процесса:
    - 0: ответ успешно отправлен (stdout содержит JSON: {answer, source, duration_ms})
    - 2: окно закрыто пользователем без отправки
    - 1: иная ошибка
    """
    # Tkinter удалён: оставляем только PyQt6

    def open_dialog_window(params: dict[str, typing.Any]) -> int:  # noqa: C901
        question_text = params.get('question') or ''
        placeholder_text = params.get('placeholder') or ''
        quick_answers_list = params.get('quick_answers') or []
        hints_list = params.get('hints') or []

        start_monotonic = time.monotonic()
        app = QtWidgets.QApplication([])
        try:
            screens = QtWidgets.QApplication.screens()
            primary = QtGui.QGuiApplication.primaryScreen()
            logger.debug(
                'GUI env: screens=%s, primary=%s',
                len(screens) if screens is not None else 'None',
                getattr(primary, 'name', lambda: 'None')(),
            )
        except Exception:
            logger.exception('Не удалось получить информацию об экранах')

        dialog = QtWidgets.QDialog()
        dialog.setWindowTitle('Ответьте на вопрос')
        dialog.resize(680, 420)

        submitted_flag = {'value': False}

        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QtWidgets.QLabel('Ответьте на вопрос')
        font = title.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        label = QtWidgets.QLabel(question_text)
        label.setWordWrap(True)
        # Сделаем вопрос более заметным: крупнее, жирнее, с палитрой-зависимым стилем
        label_font = label.font()
        label_font.setPointSize(label_font.pointSize() + 2)
        label_font.setBold(True)
        label.setFont(label_font)

        pal = dialog.palette()
        window_color = pal.color(QtGui.QPalette.ColorRole.Window)
        is_dark = False
        try:
            is_dark = window_color.lightness() < 128  # noqa: PLR2004
        except Exception:
            logger.exception('Ошибка определения темной темы')
            with contextlib.suppress(Exception):
                is_dark = window_color.value() < 128  # fallback # noqa: PLR2004

        if is_dark:
            label_style = (
                'QLabel { background: #161618; color: #f0f0f0; border: 1px solid #2a2a2e;'
                ' border-radius: 10px; padding: 10px 12px; }'
            )
        else:
            label_style = (
                'QLabel { background: #f5f5f7; color: #111111; border: 1px solid #d0d0d0;'
                ' border-radius: 10px; padding: 10px 12px; }'
            )
        label.setStyleSheet(label_style)
        layout.addWidget(label)

        # Текстовые подсказки (не кликабельные)
        if isinstance(hints_list, list) and hints_list:
            hints_label = QtWidgets.QLabel('Подсказки: ' + ' • '.join(str(h) for h in hints_list))
            hints_label.setWordWrap(True)
            hints_label.setStyleSheet('color: #777777;')
            layout.addWidget(hints_label)

        # Быстрые ответы (кнопки)
        if isinstance(quick_answers_list, list) and quick_answers_list:
            qa_title = QtWidgets.QLabel('Быстрые ответы')
            qa_title_font = qa_title.font()
            qa_title_font.setBold(True)
            qa_title.setFont(qa_title_font)
            layout.addWidget(qa_title)

            qa_scroll = QtWidgets.QScrollArea()
            qa_scroll.setWidgetResizable(True)
            qa_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            qa_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

            qa_widget = QtWidgets.QWidget()
            qa_layout = QtWidgets.QHBoxLayout(qa_widget)
            qa_layout.setContentsMargins(0, 0, 0, 0)
            qa_layout.setSpacing(8)
            for qa in quick_answers_list:
                qa_text = str(qa)
                btn = QtWidgets.QPushButton(qa_text)
                btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                # Выделим кнопки: выразительная рамка и инверсия на hover
                btn.setStyleSheet(
                    'QPushButton { padding: 8px 12px; border-radius: 10px; border: 2px solid #0057ff;'
                    ' color: #0057ff; background: transparent; font-weight: 600; }'
                    ' QPushButton:hover { background: #0057ff; color: white; }'
                )

                def make_cb(h: str) -> typing.Callable[[], None]:
                    def _cb() -> None:
                        text_edit.setPlainText(h)
                        text_edit.setFocus()
                        text_edit.selectAll()

                    return _cb

                btn.clicked.connect(make_cb(qa_text))  # type: ignore[arg-type]
                qa_layout.addWidget(btn)
            qa_layout.addStretch(1)
            qa_scroll.setWidget(qa_widget)
            layout.addWidget(qa_scroll)

        text_edit = QtWidgets.QPlainTextEdit()
        text_edit.setMinimumHeight(140)
        if placeholder_text:
            with contextlib.suppress(Exception):
                text_edit.setPlaceholderText(placeholder_text)
        layout.addWidget(text_edit)

        info_label = QtWidgets.QLabel('Cmd+Enter / Ctrl+Enter — отправить; Enter — новая строка; Esc — закрыть')
        info_label.setStyleSheet('color: #777777;')

        counter_label = QtWidgets.QLabel('Символов: 0, Строк: 1')
        counter_label.setStyleSheet('color: #777777;')

        status_row = QtWidgets.QHBoxLayout()
        status_row.addWidget(info_label)
        status_row.addStretch(1)
        status_row.addWidget(counter_label)
        layout.addLayout(status_row)

        error_label = QtWidgets.QLabel('')
        error_label.setStyleSheet('color: #d33;')
        error_label.setVisible(False)
        layout.addWidget(error_label)

        buttons_layout = QtWidgets.QHBoxLayout()
        buttons_layout.addStretch(1)
        cancel_button = QtWidgets.QPushButton('Отмена')
        send_button = QtWidgets.QPushButton('Отправить')
        send_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        send_button.setStyleSheet(
            'QPushButton { background: #0057ff; color: white; padding: 8px 14px; border-radius: 10px; }\n'
            'QPushButton:hover { background: #2a7bff; }'
        )
        buttons_layout.addWidget(cancel_button)
        buttons_layout.addWidget(send_button)
        layout.addLayout(buttons_layout)

        def submit() -> None:
            text_value = (text_edit.toPlainText() or '').strip()
            if not text_value:
                QtWidgets.QMessageBox.warning(dialog, 'Пустой ответ', 'Введите ответ перед отправкой.')
                return
            submitted_flag['value'] = True
            duration_ms_local = int((time.monotonic() - start_monotonic) * 1000)
            result = {
                'answer': text_value,
                'source': 'text',
                'duration_ms': duration_ms_local,
            }
            logger.debug(
                'Отправка ответа пользователем: %s символов, duration_ms=%s',
                len(text_value),
                duration_ms_local,
            )
            sys.stdout.write(json.dumps(result, ensure_ascii=False))
            sys.stdout.flush()
            dialog.accept()

        send_button.clicked.connect(submit)  # type: ignore[arg-type]
        cancel_button.clicked.connect(dialog.reject)  # type: ignore[arg-type]
        for seq in ('Meta+Return', 'Meta+Enter', 'Ctrl+Return', 'Ctrl+Enter'):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(seq), dialog)
            shortcut.activated.connect(submit)  # type: ignore[arg-type]

        def update_counters() -> None:
            text = text_edit.toPlainText()
            num_chars = len(text)
            num_lines = text.count('\n') + 1 if text else 1
            counter_label.setText(f'Символов: {num_chars}, Строк: {num_lines}')
            send_button.setEnabled(bool(text.strip()))
            if text.strip():
                error_label.setVisible(False)

        send_button.setEnabled(False)
        text_edit.textChanged.connect(update_counters)  # type: ignore[attr-defined]
        update_counters()

        # Обработка закрытия окна крестиком
        class _Filter(QtCore.QObject):
            def eventFilter(self, watched: QtCore.QObject | None, event: QtCore.QEvent | None) -> bool:  # noqa: N802
                if event is not None and event.type() == QtCore.QEvent.Type.Close and not submitted_flag['value']:
                    logger.debug('Диалог закрыт (QEvent.Close) без отправки')
                    submitted_flag['value'] = False
                return super().eventFilter(watched, event)

        flt = _Filter(dialog)
        dialog.installEventFilter(flt)

        def on_rejected() -> None:
            logger.debug('Диалог отклонён пользователем (rejected)')

        def on_accepted() -> None:
            logger.debug('Диалог подтверждён (accepted)')

        def on_finished(code: int) -> None:
            logger.debug('Диалог завершён (finished), code=%s', code)

        dialog.rejected.connect(on_rejected)  # type: ignore[arg-type]
        dialog.accepted.connect(on_accepted)  # type: ignore[arg-type]
        dialog.finished.connect(on_finished)  # type: ignore[arg-type]

        logger.debug('Показываем диалоговое окно пользователю')
        dialog.show()
        # Фокус сразу в поле ввода
        try:
            QtCore.QTimer.singleShot(0, text_edit.setFocus)
        except Exception:
            logger.exception('Ошибка фокуса в поле ввода')
            with contextlib.suppress(Exception):
                text_edit.setFocus()
        logger.debug('Старт цикла событий Qt')
        app.exec()
        logger.debug('Цикл событий Qt завершён; submitted=%s', submitted_flag['value'])

        return 0 if submitted_flag['value'] else 2

    # 1) Прочитать параметры
    try:
        stdin_text = sys.stdin.read()
        params = json.loads(stdin_text) if stdin_text else {}
    except Exception:
        logger.exception('Ошибка чтения параметров из stdin')
        return 1

    # 2) Открываем окно
    logger.debug('Диалог: окно открыто')
    return open_dialog_window(params)


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--local', action='store_true')
    parser.add_argument('--log-level', choices=('DEBUG', 'INFO', 'WARNING', 'ERROR'), default='WARNING')
    args = parser.parse_args()

    # Логи
    _setup_logging(args.log_level)

    # Выбор UI на уровне процесса
    logger.info('Старт процесса: режим=%s, log_level=%s', 'local' if args.local else 'server', args.log_level)

    if args.local:
        exit_code = _run_dialog_from_stdin()
        raise SystemExit(exit_code)

    server = create_server()
    server.run(transport='stdio')


if __name__ == '__main__':
    main()
