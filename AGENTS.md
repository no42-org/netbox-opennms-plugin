# Agent guide — netbox-opennms-plugin

NetBox plugin that renders monitoring **intent** (Requisitions = a live NetBox
filter over Devices/VMs) into OpenNMS requisitions and imports them over the
REST provisioning API. Sync runs as NetBox background Jobs; every sync is
idempotent render-and-replace.

## Commands (everything runs in Docker — no host Python/venv needed)

- `make verify` — ruff + full unit suite in a throwaway NetBox stack (the CI gate)
- `make test` / `make lint` — the two halves individually
- `make build` — wheel + sdist into `dist/` (pinned python image)
- `make integration` — live OpenNMS Horizon 36 round-trip (slow; boots OpenNMS)
- `make makemigrations` — generate plugin migrations + assert none missing
- Single test: `docker compose -f compose.yml run --rm netbox \
  '/opt/netbox/venv/bin/python manage.py test netbox_opennms.tests.<module>.<TestCase> -v2'`
  (then `docker compose -f compose.yml down -v`)

## Gotchas

- Version is single-sourced in `netbox_opennms/__init__.py` (`__version__`);
  `pyproject.toml` reads it via AST — never bump it anywhere else.
- `.github/workflows/release.yml` and the `pypi` environment are bound to PyPI
  Trusted Publishing — renaming either breaks OIDC (see RELEASING.md).
- Query-count regression tests compare against
  `netbox_opennms/tests/query_counts.json`; after intentional query changes run
  `make regen-counts`.
- Ruff excludes `*/migrations/*`; everything else must pass `E,F,I,UP,B`.
- Every new/edited source file needs the SPDX header
  (`SPDX-License-Identifier: MIT`).

## Commits

Conventional Commits; `git commit -s` (DCO, human identity) plus an
`Assisted-by: <Agent>:<model>` trailer on AI-assisted commits. PRs start from
an issue and are squash-merged. See CONTRIBUTING.md.
