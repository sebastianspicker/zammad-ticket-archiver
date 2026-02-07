.PHONY: dev lint test

dev:
	docker compose -f docker-compose.dev.yml up --build

lint:
	python -m ruff check .

test:
	@set -e; python -m pytest -q || (test $$? -eq 5 && echo 'No tests collected (bootstrap stage)' && exit 0)

