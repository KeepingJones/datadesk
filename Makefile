.PHONY: install test serve backtest holdout kill-port typecheck

install:
	uv sync --extra dev

test:
	uv run pytest tests/

typecheck:
	uv run mypy datadesk/

serve: kill-port
	uv run python main.py serve

backtest:
	uv run python main.py backtest

holdout:
	uv run python main.py holdout

kill-port:
	-fuser -k 8000/tcp || true
