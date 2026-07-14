.PHONY: help install lock upgrade sync format format-check lint lint-ci lint-fix lint-loc lint-readme typecheck typecheck-fast typecheck-stop typecheck-fresh test test-fast test-unit test-integration test-cov test-all check ci-local precommit clean dev run-dev run-prod docker-build docker-up docker-down docker-logs

.DEFAULT_GOAL := help

DOCKER_COMPOSE := $(shell if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then echo "docker compose"; elif command -v docker-compose >/dev/null 2>&1; then echo "docker-compose"; else echo "docker compose"; fi)

# Cap pytest-xdist workers so `-n auto` cannot fan out to every core on a
# many-core host (e.g. 32 cores -> 32 heavy worker processes -> OOM on a
# RAM-constrained machine). Override on the command line: `make test-fast
# PYTEST_MAXPROCESSES=8`, or set 0/empty workers via `make test` (single process).
PYTEST_MAXPROCESSES ?= 4

help: ## Display this help message
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install: ## Install project and development dependencies with uv
	uv sync --group dev

sync: install ## Alias for install

lock: ## Resolve and update uv.lock
	uv lock

upgrade: ## Upgrade locked dependencies
	uv lock --upgrade

format: ## Format Python code
	uv run ruff format vep_link tests scripts

format-check: ## Check formatting without writing
	uv run ruff format --check vep_link tests scripts

lint: ## Lint Python code
	uv run ruff check vep_link tests scripts

lint-ci: ## Lint Python code without modifying files
	uv run ruff check vep_link tests scripts --output-format=github

lint-fix: ## Lint and apply safe fixes
	uv run ruff check vep_link tests scripts --fix

lint-loc: ## Enforce per-file line budget (see AGENTS.md "File Size Discipline")
	uv run python scripts/check_file_size.py

lint-readme: ## Enforce the GeneFoundry README Standard v1
	uv run python scripts/check_readme.py

typecheck: ## Type check package
	uv run mypy vep_link

typecheck-fast: ## Type check with mypy daemon and fallback
	@tmp_log=$$(mktemp); \
	if uv run dmypy run -- vep_link >$$tmp_log 2>&1; then \
		cat $$tmp_log; \
	elif grep -Eq "Daemon crashed!|INTERNAL ERROR" $$tmp_log; then \
		echo "dmypy crashed; retrying with a fresh daemon..."; \
		uv run dmypy stop >/dev/null 2>&1 || true; \
		if uv run dmypy run -- vep_link >$$tmp_log 2>&1; then \
			cat $$tmp_log; \
		else \
			cat $$tmp_log; \
			echo "Falling back to plain mypy..."; \
			uv run dmypy stop >/dev/null 2>&1 || true; \
			uv run mypy vep_link; \
		fi; \
	else \
		cat $$tmp_log; \
		rm -f $$tmp_log; \
		exit 1; \
	fi; \
	rm -f $$tmp_log

typecheck-stop: ## Stop mypy daemon
	uv run dmypy stop

typecheck-fresh: ## Clear mypy cache and run typecheck
	rm -rf .mypy_cache
	uv run mypy vep_link

test: ## Run deterministic unit tests quickly
	uv run pytest tests/unit -q

test-fast: ## Run deterministic unit tests in parallel (xdist workers capped by PYTEST_MAXPROCESSES)
	uv run pytest tests/unit -q -n auto --maxprocesses=$(PYTEST_MAXPROCESSES)

test-unit: ## Run unit tests in parallel (xdist workers capped by PYTEST_MAXPROCESSES)
	uv run pytest tests/unit -q -n auto --maxprocesses=$(PYTEST_MAXPROCESSES)

test-integration: ## Run live integration tests against Ensembl REST
	VEP_LINK_RUN_INTEGRATION=1 uv run pytest tests/integration -q -m integration

test-cov: ## Run unit tests with coverage
	uv run pytest tests/unit --cov=vep_link --cov-branch --cov-report=term-missing --cov-report=html --cov-report=xml

test-all: test-cov ## Alias for full test run with coverage

check: format lint ## Format and lint

ci-local: format-check lint-ci lint-loc lint-readme typecheck-fast test-fast ## Run fast local CI-equivalent checks

precommit: ci-local ## Run checks expected before commit

clean: ## Remove local caches and generated reports
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml

dev: ## Run FastAPI host (/health) + mounted MCP HTTP locally
	uv run vep-link serve --transport unified --host 127.0.0.1 --port 8000 --dev

run-dev: dev ## Backwards-compatible alias for dev

run-prod: ## Run production server (unified HTTP host + mounted MCP)
	uv run vep-link serve --transport unified --host 0.0.0.0 --port 8000

docker-build: ## Build Docker image
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml build

docker-up: ## Start Docker development stack
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml up -d

docker-down: ## Stop Docker development stack
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml down

docker-logs: ## Follow Docker logs
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml logs -f
