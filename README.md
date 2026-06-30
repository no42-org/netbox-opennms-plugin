# netbox-opennms-plugin

A [NetBox](https://netboxlabs.com/) plugin that provisions NetBox devices and
virtual machines into [OpenNMS](https://www.opennms.com/) (Horizon 36) via the
OpenNMS REST provisioning API. NetBox is the source of truth; OpenNMS monitoring
is a derived artifact kept in sync from NetBox intent.

You author a **Monitoring Profile** once — a reusable template of OpenNMS
**detectors** and **policies** (from a built-in preset registry, or freeform
classes) — and **assign** it to a *(site[, role])* scope. Every Device/VM in that
scope is then monitored: its management IP is its **primary IP**, and OpenNMS
auto-discovers services via the profile's detectors. A per-object **Monitoring
Override** is the escape hatch (exclude an object, pin a different management IP,
add extra interfaces/services, or change its location).

**Sync** renders the complete OpenNMS *foreign-source definition* + *requisition*
for a Foreign Source (grouped by site + role) and imports it. Membership is a
live NetBox query, so adding/removing a Device or changing its role/site simply
re-resolves the scope; render-and-replace makes every re-sync idempotent and
never duplicates a node.

## Compatibility

| | |
| --- | --- |
| NetBox | 4.6.1+ |
| Python | 3.12+ |
| OpenNMS | Horizon 36 |
| License | MIT |

## Installation

Install into the same Python environment as NetBox:

```bash
pip install netbox-opennms-plugin
```

Enable the plugin and configure it in NetBox's `configuration.py`:

```python
PLUGINS = ["netbox_opennms"]

PLUGINS_CONFIG = {
    "netbox_opennms": {
        # Base URL of the OpenNMS instance, including the context path.
        "opennms_url": "https://opennms.example.org/opennms",
        # A provisioning/REST role account (NOT stored on any NetBox model).
        "opennms_username": "provision-svc",
        "opennms_password": "********",          # use your secrets mechanism
        # Default OpenNMS monitoring location for profiles that don't set one.
        # Empty means OpenNMS's built-in "Default" location.
        "default_location": "",
        # rescanExisting value passed to the import step: one of
        # "true" | "false" | "dbonly".
        "import_mode": "false",
    },
}
```

Then apply migrations and restart NetBox (and its worker):

```bash
python manage.py migrate
```

### `import_mode` values

| Value | Effect on import |
| --- | --- |
| `false` (default) | Import without rescanning nodes already known to OpenNMS. |
| `true` | Import and rescan existing nodes (re-run detectors/policies). |
| `dbonly` | Update the OpenNMS database only; do not schedule a scan. |

## Try it (Web UI)

For a throwaway NetBox deployment with the plugin installed — to click **Sync to
OpenNMS** in the browser and watch a node appear — see
[`quickstart/`](quickstart/): `docker compose --profile opennms up -d`
brings up NetBox (UI + worker) **and** a disposable OpenNMS Horizon 36.

## Running the sync worker

Sync, Remove, and Move run as NetBox **background Jobs** — they never block the
request, and a bare OpenNMS `202 ACCEPTED` is reported honestly as *submitted for
import*, never "provisioned". A NetBox **RQ worker must be running** for those
jobs to execute:

```bash
python manage.py rqworker
```

If no worker is running, the Monitoring Assignment and Sync pages show a warning
and jobs stay pending until one starts. Each object's last-sync state (submitted /
succeeded-accepted / removed / failed, with the triggering user, time, and any
error) is shown on the **Device/VM detail page**, backed by the NetBox Job log as
the audit trail.

## OpenNMS-side setup

The plugin writes requisitions; it does **not** configure OpenNMS polling. For
monitoring to actually happen:

- **Provisioning account** — `opennms_username` needs a role that can read/write
  requisitions and trigger imports (e.g. the OpenNMS provisioning/REST role).
- **Detectors → poller packages** — the profile's detectors tell OpenNMS which
  services to auto-discover, but OpenNMS only *polls* a discovered service if a
  matching **poller package** exists for it. The plugin cannot create poller
  packages — ensure your `poller-configuration.xml` covers the services your
  detectors discover.
- **Minions / monitoring locations** — a node assigned to a non-`Default`
  monitoring location is only polled if a **Minion** is registered at that
  location. The plugin best-effort warns when a chosen location is unknown to
  OpenNMS, but cannot create it. The built-in `Default` location is polled by the
  OpenNMS core (no Minion required).

## Foreign Source naming

A node's OpenNMS Foreign Source is derived (not configured) as
`netbox.{site.slug}.{role.slug}` (`no-site` / `no-role` when absent). Node
identity is the pair *(Foreign Source, type-qualified Foreign ID)* —
`device-{pk}` / `vm-{pk}` — so a re-sync updates in place and a role/site change
is handled as a move, never a duplicate.

## Development

```bash
# Editable install against a local NetBox checkout
pip install -e .

# Reproducible test stack (Docker) — see compose.yml / Makefile
make verify          # ruff lint + full test suite
make test            # tests only
make makemigrations  # generate + verify migrations
make build           # build the wheel + sdist into dist/
make integration     # live round-trip against a disposable OpenNMS Horizon 36
```

CI (GitHub Actions, `.github/workflows/ci.yml`) runs `make verify` (matrixed over
the supported NetBox version) and `make build` (asserting the wheel ships its
templates) on every PR. The live `make integration` round-trip runs nightly /
on demand — it boots a throwaway OpenNMS and is skipped by `make verify` unless
`OPENNMS_LIVE_URL` is set, so the unit suite never depends on OpenNMS.

`make` targets run NetBox in a throwaway Postgres/Redis container, so no local
NetBox install is needed. Set `DEVELOPER = True` in NetBox's `configuration.py`
if you run `makemigrations` outside the harness.

## License

MIT — see [LICENSE](./LICENSE). Every source file carries an SPDX header.
