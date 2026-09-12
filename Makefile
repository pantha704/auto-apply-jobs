.PHONY: help install test lint check clean docker docker-run install-dev

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Install runtime dependencies in active venv
	pip install -r requirements.txt

install-dev:  ## Install runtime + dev dependencies in active venv
	pip install -r requirements.txt -r requirements-dev.txt

test:  ## Run the full test suite
	python -m pytest tests/ -v --tb=short

test-q:  ## Run tests quietly (summary only)
	python -m pytest tests/ -q

lint:  ## Syntax-check every .py file
	python -m compileall -q .

check:  lint test  ## Lint then test (CI default)

clean:  ## Remove caches and temp files
	rm -rf __pycache__ .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete 2>/dev/null || true

docker:  ## Build the Docker image
	docker build -t jobhunt-linkedin .

docker-run:  ## Run the dashboard in Docker (dev only, loopback, auth on)
	docker run --rm -p 127.0.0.1:8787:8787 \
		-v jobhunt-state:/var/lib/jobhunt \
		jobhunt-linkedin

dashboard:  ## Start the local dashboard (no auth, port 8787)
	JOBHUNT_DASHBOARD_AUTH_DISABLED=1 \
	JOBHUNT_CONTROL_DB=/tmp/jobhunt-controlplane.db \
	JOBHUNT_VAULT_KEY=/tmp/jobhunt-controlplane.key \
	uvicorn controlplane.app:app --host 127.0.0.1 --port 8787

playwright-install:  ## Install Playwright browsers (for local dev, not needed in Docker)
	python -m playwright install chromium
