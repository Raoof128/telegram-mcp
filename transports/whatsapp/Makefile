# WhatsVault — developer entry points.
# Every target is safe to run locally and is the same command CI runs.

PY      := .venv/bin/python
PIP     := .venv/bin/pip
RUFF    := .venv/bin/ruff
PYTEST  := .venv/bin/pytest

.DEFAULT_GOAL := help
.PHONY: help venv install lint format format-check typecheck test test-cov audit secrets build clean check

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

venv: ## Create the virtualenv
	python3 -m venv .venv

install: venv ## Install the project and dev dependencies
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

lint: ## Lint (no changes written)
	$(RUFF) check src apps tests

format: ## Apply formatting and safe lint fixes
	$(RUFF) format src apps tests
	$(RUFF) check --fix src apps tests

format-check: ## Verify formatting without writing (CI)
	$(RUFF) format --check src apps tests

test: ## Run the full test suite
	$(PYTEST)

test-cov: ## Run tests with a coverage report
	$(PYTEST) --cov --cov-report=term-missing --cov-report=xml

audit: ## Report the security-boundary suite specifically
	$(PYTEST) tests/adversarial -v

secrets: ## Fail if anything secret-shaped is tracked or staged
	$(PYTEST) tests/test_no_secrets.py -v

build: ## Build the wheel and sdist
	$(PY) -m build

clean: ## Remove build and test artefacts
	rm -rf build dist .pytest_cache .coverage coverage.xml src/*.egg-info
	find . -name __pycache__ -type d -not -path "./.venv/*" -prune -exec rm -rf {} +

check: lint format-check test ## Everything CI enforces
