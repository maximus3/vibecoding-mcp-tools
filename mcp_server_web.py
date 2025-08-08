"""MCP сервер с тулой `ask_user`.

Тула открывает локальное окно в браузере для ввода ответа (текстом или голосом через Web Speech API),
и возвращает результат обратно в LLM. Работает локально, с одноразовым токеном сессии,
таймаутом ожидания и корректной очисткой ресурсов.
"""

import argparse
import asyncio
import contextlib
import json
import logging
import pathlib
import socket
import sys
import time
import typing
import uuid
import webbrowser

import fastapi
import mcp.server.fastmcp.server as fastmcp_server
import pydantic
import starlette.middleware.cors as starlette_cors
import uvicorn


logger = logging.getLogger('ask_user_mcp_web')


def _setup_logging(level: str = 'INFO') -> None:
    """Инициализация логгера: в stderr и файл `.logs/mcp_server_web.log`."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(log_level)

    formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(name)s | %(message)s')

    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        sh = logging.StreamHandler(stream=sys.stderr)
        sh.setLevel(log_level)
        sh.setFormatter(formatter)
        logger.addHandler(sh)

    try:
        project_root = pathlib.Path(__file__).resolve().parent
        logs_dir = project_root / '.logs'
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / 'mcp_server_web.log'
        if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
            fh = logging.FileHandler(log_file, encoding='utf-8')
            fh.setLevel(log_level)
            fh.setFormatter(formatter)
            logger.addHandler(fh)
    except Exception:
        logger.exception('Не удалось настроить файловый логгер (web)')


def _build_html(
    question: str,
    placeholder: str | None,
    quick_answers: list[str] | None,
    hints: list[str] | None,
    token: str,
) -> str:
    """Возвращает HTML-страницу (минималистичный UI) со встроенным JS.

    - Поддержка тёмной/светлой темы через prefers-color-scheme
    - Кнопки быстрых ответов (quick_answers)
    - Текстовые подсказки (hints)
    - Микрофон (Web Speech API) с понятным уведомлением при недоступности
    """
    data = {
        'question': question,
        'placeholder': placeholder or '',
        'quick_answers': quick_answers or [],
        'hints': hints or [],
        'token': token,
    }
    data_json = json.dumps(data, ensure_ascii=False)

    # Небольшой, строгий, самодостаточный HTML. Вставляем данные как константу INITIAL_STATE.
    # ruff: noqa: E501
    return f"""
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Ответьте на вопрос</title>
  <style>
    :root {{
      --bg: #ffffff;
      --fg: #111111;
      --muted: #666666;
      --primary: #0057ff;
      --danger: #d33;
      --ok: #2d8a34;
      --surface: #f5f5f7;
      --border: #d0d0d0;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #0c0c0d;
        --fg: #f0f0f0;
        --muted: #aaaaaa;
        --primary: #6ca0ff;
        --danger: #ff6b6b;
        --ok: #5ed16a;
        --surface: #161618;
        --border: #2a2a2e;
      }}
    }}
    html, body {{ margin: 0; padding: 0; background: var(--bg); color: var(--fg); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Helvetica Neue', Arial, 'Apple Color Emoji', 'Segoe UI Emoji', 'Segoe UI Symbol', sans-serif; }}
    .overlay {{ position: fixed; inset: 0; display: grid; place-items: center; background: color-mix(in oklab, var(--bg) 70%, black 30%); padding: 24px; }}
    .modal {{ width: min(720px, 92vw); background: var(--surface); border: 1px solid var(--border); border-radius: 12px; box-shadow: 0 12px 32px rgba(0,0,0,.35); padding: 20px; }}
    .title {{ font-size: 20px; font-weight: 700; margin: 4px 0 12px; }}
    .question {{ font-size: 18px; font-weight: 700; margin: 0 0 12px; color: var(--fg); background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; }}
    .muted {{ color: var(--muted); font-size: 13px; }}
    .row {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
    .quick-answers {{ margin: 8px 0 16px; display: flex; flex-wrap: wrap; gap: 10px; }}
    .qa-title {{ font-weight: 700; margin: 6px 0 4px; }}
    .hint-btn {{ padding: 8px 12px; border-radius: 10px; border: 2px solid var(--primary); background: transparent; color: var(--primary); cursor: pointer; font-weight: 600; }}
    .hint-btn:hover {{ background: var(--primary); color: white; }}
    .hints-text {{ color: var(--muted); font-size: 13px; margin: 4px 0 8px; }}
    textarea {{ width: 100%; min-height: 100px; resize: vertical; padding: 10px 12px; border-radius: 8px; border: 1px solid var(--border); background: var(--bg); color: var(--fg); font-size: 14px; }}
    .controls {{ display: flex; gap: 10px; align-items: center; margin-top: 12px; }}
    .btn {{ padding: 10px 14px; border-radius: 10px; border: 1px solid var(--border); background: var(--bg); color: var(--fg); cursor: pointer; font-weight: 600; }}
    .btn.primary {{ background: var(--primary); border-color: var(--primary); color: white; }}
    .btn.danger {{ background: var(--danger); border-color: var(--danger); color: white; }}
    .status {{ font-size: 13px; color: var(--muted); min-width: 160px; }}
    .ok {{ color: var(--ok); }}
    .error {{ color: var(--danger); }}
    .footer {{ margin-top: 10px; font-size: 12px; color: var(--muted); }}
    .confirm {{ margin-top: 12px; padding: 10px 12px; border: 1px solid var(--border); background: var(--bg); border-radius: 8px; }}
  </style>
</head>
<body>
  <div class="overlay">
    <div class="modal">
      <div class="title">Ответьте на вопрос</div>
      <div class="question" id="question"></div>
      <div class="hints-text" id="hintsText"></div>
      <div class="qa-title" id="qaTitle" style="display:none;">Быстрые ответы</div>
      <div class="quick-answers" id="quickAnswers"></div>
      <textarea id="answer" placeholder=""></textarea>
      <div class="controls">
        <button class="btn" id="micBtn">🎙️ Микрофон</button>
        <button class="btn primary" id="sendBtn">Отправить</button>
        <span class="status" id="status"></span>
      </div>
      <div class="footer" id="footer"></div>
      <div class="confirm" id="confirm" style="display:none;">Ответ отправлен, это окно можно закрыть.</div>
    </div>
  </div>

  <script>
  const INITIAL_STATE = {data_json};
  const pageStart = performance.now();
  const questionEl = document.getElementById('question');
  const hintsTextEl = document.getElementById('hintsText');
  const qaTitleEl = document.getElementById('qaTitle');
  const quickAnswersEl = document.getElementById('quickAnswers');
  const answerEl = document.getElementById('answer');
  const micBtn = document.getElementById('micBtn');
  const sendBtn = document.getElementById('sendBtn');
  const statusEl = document.getElementById('status');
  const footerEl = document.getElementById('footer');
  const confirmEl = document.getElementById('confirm');

  questionEl.textContent = INITIAL_STATE.question || '';
  answerEl.placeholder = INITIAL_STATE.placeholder || '';
  // Auto-focus textarea on open
  setTimeout(() => {{ try {{ answerEl.focus(); }} catch (_) {{}} }}, 0);

  // Quick answers (buttons)
  if (Array.isArray(INITIAL_STATE.quick_answers) && INITIAL_STATE.quick_answers.length) {{
    qaTitleEl.style.display = '';
    INITIAL_STATE.quick_answers.forEach((h) => {{
      const b = document.createElement('button');
      b.className = 'hint-btn';
      b.type = 'button';
      b.textContent = h;
      b.addEventListener('click', () => {{
        answerEl.value = h;
        sourceChoice = 'text';
      }});
      quickAnswersEl.appendChild(b);
    }});
  }} else {{
    quickAnswersEl.style.display = 'none';
  }}

  // Textual hints
  if (Array.isArray(INITIAL_STATE.hints) && INITIAL_STATE.hints.length) {{
    hintsTextEl.textContent = 'Подсказки: ' + INITIAL_STATE.hints.join(' • ');
  }} else {{
    hintsTextEl.style.display = 'none';
  }}

  // Voice recognition
  let sourceChoice = 'text';
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognition = null;
  let recognizing = false;
  if (!SpeechRecognition) {{
    footerEl.textContent = 'Распознавание речи недоступно в этом браузере. Доступен текстовый ввод.';
  }} else {{
    recognition = new SpeechRecognition();
    recognition.lang = 'ru-RU';
    recognition.continuous = false;
    recognition.interimResults = true;

    recognition.onstart = () => {{ recognizing = true; statusEl.textContent = 'Слушаем…'; }};
    recognition.onerror = (e) => {{ recognizing = false; statusEl.textContent = 'Ошибка распознавания'; console.error(e); }};
    recognition.onend = () => {{ recognizing = false; statusEl.textContent = 'Пауза'; }};
    recognition.onresult = (event) => {{
      let finalText = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {{
        const r = event.results[i];
        if (r.isFinal) {{ finalText += r[0].transcript; }}
      }}
      if (finalText) {{
        answerEl.value = finalText.trim();
        sourceChoice = 'voice';
        statusEl.textContent = 'Распознано';
      }}
    }};
  }}

  micBtn.addEventListener('click', async () => {{
    if (!recognition) {{
      alert('В этом браузере нет Web Speech API. Пожалуйста, введите ответ вручную.');
      return;
    }}
    try {{
      if (!recognizing) {{
        recognition.start();
      }} else {{
        recognition.stop();
      }}
    }} catch (err) {{
      console.error(err);
      alert('Не удалось получить доступ к микрофону. Введите ответ вручную.');
    }}
  }});

  answerEl.addEventListener('input', () => {{ sourceChoice = 'text'; }});

  function tryAutoClose() {{
    // Попытаться закрыть окно автоматически; если не получилось — показать подтверждение
    const markConfirmed = () => {{
      confirmEl.style.display = 'block';
    }};
    try {{
      window.close();
    }} catch (_) {{}}
    setTimeout(() => {{
      if (!window.closed) {{
        try {{ window.open('', '_self'); window.close(); }} catch (_) {{}}
      }}
    }}, 50);
    setTimeout(() => {{
      if (!window.closed) {{ markConfirmed(); }}
    }}, 200);
  }}

  async function submitAnswer() {{
    const answer = (answerEl.value || '').trim();
    if (!answer) {{
      alert('Введите ответ или продиктуйте его.');
      return;
    }}
    const payload = {{
      token: INITIAL_STATE.token,
      answer,
      source: sourceChoice,
      duration_ms: Math.round(performance.now() - pageStart),
    }};
    try {{
      const res = await fetch('/submit', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(payload),
      }});
      if (!res.ok) {{
        const text = await res.text();
        throw new Error('Сервер ответил ошибкой: ' + text);
      }}
      statusEl.textContent = '';
      sendBtn.disabled = true;
      micBtn.disabled = true;
      tryAutoClose();
    }} catch (e) {{
      console.error(e);
      alert('Ошибка отправки ответа: ' + (e && e.message ? e.message : e));
    }}
  }}

  sendBtn.addEventListener('click', submitAnswer);
  answerEl.addEventListener('keydown', (e) => {{
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {{ submitAnswer(); }}
  }});
  </script>
</body>
</html>
"""


# ruff: noqa: E501


def _find_free_port() -> int:
    """Находит свободный порт на 127.0.0.1.

    Используем безопасное привязывание к порту 0 с последующим чтением фактического порта.
    Небольшая гонка возможна, но вероятность крайне мала для локального сценария.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return int(s.getsockname()[1])


class AskUserResult(pydantic.BaseModel):
    answer: str
    source: typing.Literal['text', 'voice']
    duration_ms: int


async def _run_web_dialog_session(  # noqa: C901
    question: str,
    placeholder: str | None,
    quick_answers: list[str] | None,
    hints: list[str] | None,
    timeout_seconds: int,
) -> 'AskUserResult':
    """Запускает временный локальный веб‑сервер и возвращает ответ пользователя.

    Используется для локального режима (`--local`).
    """
    loop = asyncio.get_running_loop()
    answer_future: asyncio.Future[dict[str, typing.Any]] = loop.create_future()
    session_token = str(uuid.uuid4())
    session_started = time.monotonic()

    app = fastapi.FastAPI()

    app.add_middleware(
        starlette_cors.CORSMiddleware,
        allow_origin_regex=r'^http://127\.0\.0\.1:\\d+$',
        allow_methods=['POST'],
        allow_headers=['*'],
    )

    def session_url(port_value: int) -> str:
        return f'http://127.0.0.1:{port_value}/?t={session_token}'

    @app.get('/')
    async def index(t: str | None = None) -> fastapi.responses.Response:  # type: ignore[override]
        if t != session_token:
            return fastapi.responses.JSONResponse({'detail': 'invalid session token'}, status_code=403)
        html = _build_html(
            question=question,
            placeholder=placeholder,
            quick_answers=quick_answers,
            hints=hints,
            token=session_token,
        )
        return fastapi.responses.HTMLResponse(content=html)

    @app.post('/submit')
    async def submit(request: fastapi.Request) -> fastapi.responses.Response:  # type: ignore[override]
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return fastapi.responses.JSONResponse({'detail': 'invalid json'}, status_code=400)

        if not isinstance(payload, dict):
            return fastapi.responses.JSONResponse({'detail': 'invalid payload'}, status_code=400)

        if payload.get('token') != session_token:
            return fastapi.responses.JSONResponse({'detail': 'invalid session token'}, status_code=403)

        raw_answer = payload.get('answer')
        raw_source = payload.get('source')
        if not isinstance(raw_answer, str) or not raw_answer.strip():
            return fastapi.responses.JSONResponse({'detail': 'answer is required'}, status_code=400)
        if raw_source not in {'text', 'voice'}:
            return fastapi.responses.JSONResponse({'detail': 'source must be "text" or "voice"'}, status_code=400)

        duration_ms = int((time.monotonic() - session_started) * 1000)
        result = {
            'answer': raw_answer.strip(),
            'source': typing.cast('str', raw_source),
            'duration_ms': duration_ms,
        }
        if not answer_future.done():
            answer_future.set_result(result)
        return fastapi.responses.JSONResponse({'status': 'ok'})

    port = _find_free_port()

    config = uvicorn.Config(
        app=app,
        host='127.0.0.1',
        port=port,
        log_level='warning',
        access_log=False,
    )
    server = uvicorn.Server(config)

    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.2)
    webbrowser.open_new_tab(session_url(port))

    try:
        result_dict = await asyncio.wait_for(answer_future, timeout=float(timeout_seconds))
    except TimeoutError as exc:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(server_task, timeout=3.0)
        raise TimeoutError('Ожидание ответа пользователя истекло.') from exc
    except Exception:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(server_task, timeout=3.0)
        raise
    else:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(server_task, timeout=3.0)
        return AskUserResult(**result_dict)


def create_server() -> fastmcp_server.FastMCP:  # noqa: C901
    """Создаёт и настраивает FastMCP-сервер с тулой `ask_user`."""
    server = fastmcp_server.FastMCP(
        name='ask-user-mcp',
        instructions='Локальная туса ask_user для опроса человека (text/voice) через браузер.',
    )

    @server.tool(
        name='ask_user',
        title='Ask User',
        description=(
            'Открывает локальную страницу (127.0.0.1) с вопросом, полем ввода и голосовым вводом. '
            'Возвращает ответ пользователя.'
        ),
    )
    async def ask_user(  # noqa: C901
        question: str,
        placeholder: str | None = None,
        quick_answers: list[str] | None = None,
        hints: list[str] | None = None,
        timeout_seconds: int = 180,
    ) -> AskUserResult:
        """Запрашивает ответ у пользователя через локальную веб-страницу.

        Параметры:
        - question: обязательный вопрос
        - placeholder: необязательный плейсхолдер
        - quick_answers: необязательные быстрые ответы (кнопки, подставляют текст в поле)
        - hints: необязательные текстовые подсказки к вопросу/ответу (не кликабельные)
        - timeout_seconds: таймаут ожидания ответа (по умолчанию 180)

        Результат:
        - answer: итоговый ответ (строка)
        - source: 'text' | 'voice'
        - duration_ms: длительность сессии до отправки
        """
        loop = asyncio.get_running_loop()
        answer_future: asyncio.Future[dict[str, typing.Any]] = loop.create_future()
        session_token = str(uuid.uuid4())
        session_started = time.monotonic()

        # FastAPI приложение
        app = fastapi.FastAPI()

        # CORS: разрешаем только http://127.0.0.1:<port>
        app.add_middleware(
            starlette_cors.CORSMiddleware,
            allow_origin_regex=r'^http://127\.0\.0\.1:\\d+$',
            allow_methods=['POST'],
            allow_headers=['*'],
        )

        # Вспом.
        def session_url(port_value: int) -> str:
            return f'http://127.0.0.1:{port_value}/?t={session_token}'

        # Роуты
        @app.get('/')
        async def index(t: str | None = None) -> fastapi.responses.Response:
            if t != session_token:
                return fastapi.responses.JSONResponse({'detail': 'invalid session token'}, status_code=403)
            html = _build_html(
                question=question,
                placeholder=placeholder,
                quick_answers=quick_answers,
                hints=hints,
                token=session_token,
            )
            return fastapi.responses.HTMLResponse(content=html)

        @app.post('/submit')
        async def submit(request: fastapi.Request) -> fastapi.responses.Response:
            try:
                payload = await request.json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                return fastapi.responses.JSONResponse({'detail': 'invalid json'}, status_code=400)

            if not isinstance(payload, dict):
                return fastapi.responses.JSONResponse({'detail': 'invalid payload'}, status_code=400)

            if payload.get('token') != session_token:
                return fastapi.responses.JSONResponse({'detail': 'invalid session token'}, status_code=403)

            raw_answer = payload.get('answer')
            raw_source = payload.get('source')
            if not isinstance(raw_answer, str) or not raw_answer.strip():
                return fastapi.responses.JSONResponse({'detail': 'answer is required'}, status_code=400)
            if raw_source not in {'text', 'voice'}:
                return fastapi.responses.JSONResponse({'detail': 'source must be "text" or "voice"'}, status_code=400)

            # Длительность считаем на сервере, чтобы не зависеть от клиента
            duration_ms = int((time.monotonic() - session_started) * 1000)
            result = {
                'answer': raw_answer.strip(),
                'source': typing.cast('str', raw_source),
                'duration_ms': duration_ms,
            }
            if not answer_future.done():
                answer_future.set_result(result)
            return fastapi.responses.JSONResponse({'status': 'ok'})

        # Выбираем свободный порт и настраиваем сервер
        port = _find_free_port()

        # CORS уже ограничен allow_origin_regex = 127.0.0.1:<port>

        config = uvicorn.Config(
            app=app,
            host='127.0.0.1',
            port=port,
            log_level='warning',
            access_log=False,
        )
        server = uvicorn.Server(config)

        # Запускаем HTTP сервер в фоне
        server_task = asyncio.create_task(server.serve())

        # Дадим шанс серверу стартовать, затем откроем системный браузер
        await asyncio.sleep(0.2)
        webbrowser.open_new_tab(session_url(port))

        # Ждём результата или таймаута, при любом исходе останавливаем сервер
        try:
            result_dict = await asyncio.wait_for(answer_future, timeout=float(timeout_seconds))
        except TimeoutError as exc:
            # Таймаут — корректно останавливаем сервер и пробрасываем MCP-ошибку TimeoutError
            server.should_exit = True
            with contextlib.suppress(Exception):
                await asyncio.wait_for(server_task, timeout=3.0)
            msg = 'Ожидание ответа пользователя истекло.'
            raise TimeoutError(msg) from exc
        except Exception:
            server.should_exit = True
            with contextlib.suppress(Exception):
                await asyncio.wait_for(server_task, timeout=3.0)
            raise
        else:
            server.should_exit = True
            with contextlib.suppress(Exception):
                await asyncio.wait_for(server_task, timeout=3.0)
            return AskUserResult(**result_dict)

    return server


def _run_local_from_stdin() -> int:
    """Локальный режим: читает JSON из stdin, открывает веб‑страницу и печатает ответ в stdout.

    Вход (stdin, JSON):
    {
      "question": str,
      "placeholder": str | null,
      "quick_answers": list[str] | null,
      "hints": list[str] | null,
      "timeout_seconds": int | null
    }
    Код возврата:
      0 — успех, stdout содержит JSON {answer, source, duration_ms}
      1 — ошибка/таймаут
    """
    try:
        stdin_text = sys.stdin.read()
        params = json.loads(stdin_text) if stdin_text else {}
    except Exception:
        logger.exception('Ошибка чтения параметров из stdin (local)')
        return 1

    question = params.get('question') or ''
    if not isinstance(question, str) or not question.strip():
        logger.error('Отсутствует обязательный параметр question')
        return 1

    placeholder = params.get('placeholder') if isinstance(params.get('placeholder'), str) else None
    quick_answers = params.get('quick_answers') if isinstance(params.get('quick_answers'), list) else None
    hints = params.get('hints') if isinstance(params.get('hints'), list) else None
    timeout_seconds_raw = params.get('timeout_seconds')
    timeout_seconds = int(timeout_seconds_raw) if isinstance(timeout_seconds_raw, int) else 180

    try:
        result = asyncio.run(
            _run_web_dialog_session(
                question=question,
                placeholder=placeholder,
                quick_answers=typing.cast('list[str] | None', quick_answers),
                hints=typing.cast('list[str] | None', hints),
                timeout_seconds=timeout_seconds,
            )
        )
    except TimeoutError:
        logger.error('Таймаут ожидания ответа в локальном режиме')  # noqa: TRY400
        return 1
    except Exception:
        logger.exception('Ошибка локального режима')
        return 1

    try:
        sys.stdout.write(json.dumps(result.model_dump(), ensure_ascii=False))
        sys.stdout.flush()
    except Exception:
        logger.exception('Ошибка записи результата в stdout')
        return 1

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--local', action='store_true')
    parser.add_argument('--log-level', choices=('DEBUG', 'INFO', 'WARNING', 'ERROR'), default='WARNING')
    args = parser.parse_args()

    _setup_logging(args.log_level)
    logger.info('Старт процесса: режим=%s, log_level=%s', 'local' if args.local else 'server', args.log_level)

    if args.local:
        exit_code = _run_local_from_stdin()
        raise SystemExit(exit_code)

    server = create_server()
    server.run(transport='stdio')


if __name__ == '__main__':
    main()
