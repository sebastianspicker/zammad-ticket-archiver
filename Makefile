.PHONY: dev lint format typecheck test test-fast test-cov qa ci dev-setup clean

dev:
	docker compose -f docker-compose.dev.yml up --build

dev-setup:
	@echo "Setting up development environment..."
	pip install -e ".[dev]"
	pip install pre-commit
	pre-commit install
	@echo "Creating .env from example if not exists..."
	@if [ ! -f .env ]; then cp .env.example .env && echo "Created .env - please edit with your settings"; fi
	@echo "Development setup complete!"

lint:
	python -m ruff check .

format:
	python -m ruff format .

typecheck:
	python -m mypy src test

test:
	@set -e; python -m pytest -q || (test $$? -eq 5 && echo 'No tests collected (bootstrap stage)' && exit 0)

test-fast:
	@set -e; python -m pytest -q test/static test/unit || (test $$? -eq 5 && echo 'No tests collected (bootstrap stage)' && exit 0)

test-cov:
	python -m pytest --cov=src/zammad_pdf_archiver --cov-report=term-missing --cov-report=html:htmlcov

qa: lint
	python -m mypy . --config-file pyproject.toml
	python -m pytest -q test/static test/unit test/integration test/nfr

ci: lint typecheck test

clean:
	rm -rf build dist .eggs *.egg-info .pytest_cache .coverage htmlcov .mypy_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
