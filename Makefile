.PHONY: dev lint format typecheck test test-fast test-cov test-unit test-int test-nfr test-all smoke docs-check docker-smoke qa build verify ci dev-setup clean

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

smoke:
	bash scripts/ci/smoke-test.sh

format:
	python -m ruff format .

typecheck:
	python -m mypy src test

test:
	@set -e; python -m pytest -q || (test $$? -eq 5 && echo 'No tests collected (bootstrap stage)' && exit 0)

test-fast:
	@set -e; python -m pytest -q test/static test/unit || (test $$? -eq 5 && echo 'No tests collected (bootstrap stage)' && exit 0)

test-unit:
	@set -e; python -m pytest -q test/unit || (test $$? -eq 5 && echo 'No tests collected (bootstrap stage)' && exit 0)

test-int:
	@set -e; python -m pytest -q test/integration || (test $$? -eq 5 && echo 'No tests collected (bootstrap stage)' && exit 0)

test-nfr:
	@set -e; python -m pytest -q test/nfr || (test $$? -eq 5 && echo 'No tests collected (bootstrap stage)' && exit 0)

test-all:
	@set -e; python -m pytest -q || (test $$? -eq 5 && echo 'No tests collected (bootstrap stage)' && exit 0)

test-cov:
	python -m pytest --cov=src/zammad_pdf_archiver --cov-report=term-missing --cov-report=html:htmlcov

docs-check:
	@for p in README.md docs/01-architecture.md docs/08-operations.md docs/api.md docs/config-reference.md docs/PRD.md; do \
		test -f $$p || (echo "Missing docs: $$p" && exit 1); \
	done; \
	echo "docs-check: OK"

docker-smoke:
	docker build -t zammad-pdf-archiver:local .

qa: lint smoke
	python -m ruff check src --select C901
	python -m mypy . --config-file pyproject.toml
	python -m pytest -q test/static test/unit test/integration test/nfr

build:
	python -m build

verify: qa build

ci: lint typecheck test

clean:
	rm -rf build dist .eggs *.egg-info .pytest_cache .coverage htmlcov .mypy_cache
	rm -rf .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.py[co]' -delete 2>/dev/null || true
