run-local:
	printf '%s' '{"question":"CLI: проверьте отправку ответа","placeholder":"Введите ответ","hints":["Тут подсказки","Это тоже подсказка"],"quick_answers":["Ок","Готово","Отмена", "Cancel"]}' | uv run --directory /Users/imaximus3/personal/vibecoding-mcp-tools mcp_server.py --local --log-level DEBUG | cat

run-local-web:
	printf '%s' '{"question":"CLI: проверьте отправку ответа","placeholder":"Введите ответ","hints":["Тут подсказки","Это тоже подсказка"],"quick_answers":["Ок","Готово","Отмена", "Cancel"]}' | uv run --directory /Users/imaximus3/personal/vibecoding-mcp-tools mcp_server_web.py --local --log-level DEBUG | cat

format:
	uv run ruff format .
	uv run ruff check . --fix -e >/dev/null

lint:
	uv run ruff check .