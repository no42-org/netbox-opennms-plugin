# netbox-opennms-plugin

A [NetBox](https://netboxlabs.com/) plugin that provisions NetBox devices and
virtual machines into [OpenNMS](https://www.opennms.com/) (Horizon 35) via the
OpenNMS REST provisioning API. NetBox is the source of truth; OpenNMS monitoring
is a derived artifact kept in sync from NetBox intent.

You attach a **Monitoring Profile** to a Device or VM (its management IP, extra
interfaces, monitored services, and an optional monitoring location), then
**Sync** — the plugin renders the complete OpenNMS *requisition* for that
object's Foreign Source (grouped by site + role) and imports it. Remove, bulk
sync, and role/site *moves* are all expressed as the same render-and-replace, so
re-syncing is idempotent and never duplicates a node.

## Compatibility

| | |
| --- | --- |
| NetBox | 4.6.1+ |
| Python | 3.12+ |
| OpenNMS | Horizon 35 |
| License | Apache-2.0 |

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

## Running the sync worker

Sync, Remove, and Move run as NetBox **background Jobs** — they never block the
request, and a bare OpenNMS `202 ACCEPTED` is reported honestly as *submitted for
import*, never "provisioned". A NetBox **RQ worker must be running** for those
jobs to execute:

```bash
python manage.py rqworker
```

If no worker is running, the Monitoring Profile page shows a warning and jobs
stay pending until one starts. Each object's last-sync state (submitted /
succeeded-accepted / removed / failed, with the triggering user, time, and any
error) is shown on the Monitoring Profile **and** on the Device/VM detail page,
backed by the NetBox Job log as the audit trail.

## OpenNMS-side setup

The plugin writes requisitions; it does **not** configure OpenNMS polling. For
monitoring to actually happen:

- **Provisioning account** — `opennms_username` needs a role that can read/write
  requisitions and trigger imports (e.g. the OpenNMS provisioning/REST role).
- **Poller packages** — OpenNMS only *polls* a monitored service if a matching
  **poller package** exists for it. The plugin declares the services you choose
  (auto-detection is intentionally disabled), but it cannot create poller
  packages — ensure your `poller-configuration.xml` covers them.
- **Minions / monitoring locations** — a node assigned to a non-`Default`
  monitoring location is only polled if a **Minion** is registered at that
  location. The plugin best-effort warns when a chosen location is unknown to
  OpenNMS, but cannot create it. The built-in `Default` location is polled by the
  OpenNMS core (no Minion required).

## Foreign Source naming

A node's OpenNMS Foreign Source is derived (not configured) as
`netbox:{site.slug}:{role.slug}` (`no-site` / `no-role` when absent). Node
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
```

`make` targets run NetBox in a throwaway Postgres/Redis container, so no local
NetBox install is needed. Set `DEVELOPER = True` in NetBox's `configuration.py`
if you run `makemigrations` outside the harness.

## License

Apache-2.0 — see [LICENSE](./LICENSE). Every source file carries an SPDX header.
