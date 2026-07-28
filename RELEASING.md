# Releasing

Versions follow [SemVer](https://semver.org/), derived from Conventional
Commits since the last tag: `BREAKING CHANGE`/`!` → major, `feat` → minor,
otherwise patch. The version is single-sourced in
`netbox_opennms/__init__.py` (`__version__`); `pyproject.toml` reads it
dynamically.

## Cutting a release

1. Ensure CI on `main` is green.
2. Open a PR bumping `__version__` in `netbox_opennms/__init__.py` to `X.Y.Z`
   (`chore(release): set version to X.Y.Z`) and merge it.
3. Create a GitHub Release with a new tag `vX.Y.Z` on the merged bump commit,
   with curated notes (Highlights / Breaking changes / Fixes — not a raw
   commit dump). Prerelease tags (`vX.Y.Z-rc1`) are marked as prereleases.
4. **Publishing the Release triggers the `Release` workflow** — this repo
   deliberately releases on `release: published`, not on raw tag pushes,
   because the PyPI trusted-publisher binding is registered against the
   workflow filename `release.yml` and the `pypi` environment. Renaming
   either breaks OIDC authentication until the binding on pypi.org is
   updated.
5. The workflow asserts the tag matches `__version__`, builds the wheel +
   sdist with `make build`, verifies the wheel ships the plugin templates,
   and publishes to [PyPI](https://pypi.org/project/netbox-opennms-plugin/)
   via **Trusted Publishing** (OIDC — no API token secrets).

## Provenance

Uploads use `pypa/gh-action-pypi-publish`, which generates
[PEP 740](https://peps.python.org/pep-0740/) publish attestations. Each file's
provenance — linking it to the exact workflow run that built it — is shown on
its PyPI file page and can be checked with
[`pypi-attestations`](https://pypi.org/project/pypi-attestations/):

    pypi-attestations verify pypi --repository https://github.com/no42-org/netbox-opennms-plugin \
      pypi:netbox_opennms_plugin-X.Y.Z-py3-none-any.whl

If the release pipeline changes, this file changes in the same PR.
