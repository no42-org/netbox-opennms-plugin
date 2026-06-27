# netbox-opennms developer / CI entrypoints. CI invokes these targets, never the
# underlying tooling directly, so local and CI runs stay in sync.
.PHONY: help test makemigrations regen-counts lint verify build clean

COMPOSE := docker compose -f compose.yml
# Pinned ruff image so lint matches CI without a host install.
RUFF := ghcr.io/astral-sh/ruff:0.15.20
# Pinned Python image for builds — no host toolchain required.
BUILD_IMAGE := python:3.12-slim
# The build runs as root in the container; chown artifacts back to the caller so
# host/CI can clean them without sudo.
OWNER := $(shell id -u):$(shell id -g)
PY := /opt/netbox/venv/bin/python

help: ## List targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  %-16s %s\n", $$1, $$2}'

test: ## Run the plugin test suite in a throwaway NetBox stack
	$(COMPOSE) run --rm netbox; rc=$$?; $(COMPOSE) down -v; exit $$rc

makemigrations: ## Generate plugin migrations and verify none are missing
	$(COMPOSE) run --rm netbox \
		'$(PY) manage.py makemigrations netbox_opennms && \
		 $(PY) manage.py makemigrations --check --dry-run netbox_opennms'; \
		rc=$$?; $(COMPOSE) down -v; exit $$rc

regen-counts: ## Regenerate netbox_opennms/tests/query_counts.json baselines
	$(COMPOSE) run --rm -e UPDATE_QUERY_COUNTS=1 netbox \
		'$(PY) manage.py test netbox_opennms -v1'; \
		rc=$$?; $(COMPOSE) down -v; exit $$rc

lint: ## Ruff lint (pinned image, no host install)
	docker run --rm -v "$(CURDIR)":/io -w /io $(RUFF) check .

verify: lint test ## Lint + test (the CI entrypoint)

build: ## Build the wheel + sdist into dist/ (pinned Python image)
	docker run --rm -v "$(CURDIR)":/src -w /src $(BUILD_IMAGE) \
		sh -c 'pip install --quiet build && python -m build && \
		chown -R $(OWNER) dist build *.egg-info 2>/dev/null || true'

clean: ## Tear down the stack and volumes
	$(COMPOSE) down -v --remove-orphans
