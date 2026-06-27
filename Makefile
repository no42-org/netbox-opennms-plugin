# netbox-opennms developer / CI entrypoints. CI invokes these targets, never the
# underlying tooling directly, so local and CI runs stay in sync.
.PHONY: help test makemigrations regen-counts lint verify clean

COMPOSE := docker compose -f compose.yml
# Pinned ruff image so lint matches CI without a host install.
RUFF := ghcr.io/astral-sh/ruff:0.15.20
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

clean: ## Tear down the stack and volumes
	$(COMPOSE) down -v --remove-orphans
