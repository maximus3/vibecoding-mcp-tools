run-local:
	printf '%s' '{"question":"CLI: проверьте отправку ответа","placeholder":"Введите ответ","hints":["Тут подсказки","Это тоже подсказка"],"quick_answers":["Ок","Готово","Отмена", "Cancel"]}' | uv run --directory /Users/imaximus3/personal/vibecoding-mcp-tools mcp_server.py --local --log-level DEBUG | cat

run-local-web:
	printf '%s' '{"question":"CLI: проверьте отправку ответа","placeholder":"Введите ответ","hints":["Тут подсказки","Это тоже подсказка"],"quick_answers":["Ок","Готово","Отмена", "Cancel"]}' | uv run --directory /Users/imaximus3/personal/vibecoding-mcp-tools mcp_server_web.py --local --log-level DEBUG | cat

proxy-list:
	uv run mcp_proxy_server.py --list-tools

proxy-configure:
	uv run mcp_proxy_server.py --configure

proxy-rebuild:
	uv run mcp_proxy_server.py --rebuild

proxy-test:
	echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | uv run mcp_proxy_server.py

format:
	uv run ruff format .
	uv run ruff check . --fix -e >/dev/null

lint:
	uv run ruff check .