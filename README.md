# netbox-opennms-plugin

A [NetBox](https://netboxlabs.com/) plugin that provisions NetBox devices and
virtual machines into [OpenNMS](https://www.opennms.com/) (Horizon 36) via the
OpenNMS REST provisioning API. NetBox is the source of truth; OpenNMS monitoring
is a derived artifact kept in sync from NetBox intent.

You author a **Requisition** — one user-named OpenNMS Foreign Source — that owns
its OpenNMS **detectors** and **policies** (discovered live from your OpenNMS
instance, with a curated preset overlay for labels/defaults, or a freeform class),
a set of declared **services** (e.g. ICMP, SNMP), and a live
NetBox **filter** that selects its member Devices/VMs (by role, tag, site, status,
custom field, …). Every member is monitored: its management IP is its **primary
IP** unless overridden, OpenNMS auto-discovers services via the detectors, and the
declared services are the guaranteed-present floor. A per-object **Monitoring
Override** is the escape hatch (exclude an object, pin a different management IP,
add extra interfaces — each with an SNMP role of **Primary / Secondary /
Not-eligible** (`snmp-primary` P/S/N; at most one Primary per node) — add/suppress
a service, or change its location).

Requisition filters must be **disjoint**: an object matched by more than one
Requisition's filter is a **conflict** — Sync of every involved Requisition is
blocked (their OpenNMS state stays untouched) until you resolve the overlap, so a
node always lives in exactly one Foreign Source and nothing ever moves or
disappears implicitly. A **dry-run** shows, per node, exactly what a Sync would
add / remove / change against the live OpenNMS state before you commit.

Per-node status is **graded**: a **Critical** (red) — a filter conflict — **blocks
Sync**; a **Warning** (yellow) is advisory and does **not**. A member with **no
management IP** is a Warning: rather than silently skipping it, the plugin
provisions an **inventory-only node with no IP interface** (it will not be actively
monitored) and surfaces the warning in the Sync preview, the dry-run, and the
Device/VM page — exclude the object if you don't want it in OpenNMS at all.

**Sync** renders the complete OpenNMS *foreign-source definition* + *requisition*
and imports it. Membership is a live NetBox query, so adding/removing a Device or
changing an attribute the filter matches simply re-resolves the Requisition;
render-and-replace makes every re-sync idempotent and never duplicates a node.

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
        # Default OpenNMS monitoring location for requisitions that don't set one.
        # Empty means OpenNMS's built-in "Default" location.
        "default_location": "",
        # rescanExisting value passed to the import step: one of
        # "true" | "false" | "dbonly".
        "import_mode": "false",
        # Periodic drift reconciler (hourly): clears OpenNMS Foreign Sources the
        # plugin has pushed but NetBox no longer monitors — when a Requisition is
        # renamed or deleted, or its last member leaves. Ownership is tracked per
        # pushed Foreign Source, so it only ever touches requisitions the plugin
        # created, never a foreign one. "true" / "false"; needs an RQ worker.
        "reconcile_orphans": "true",
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

### Kubernetes (Helm chart)

The [`netbox-community/netbox`](https://github.com/netbox-community/netbox-chart)
chart's base image does not include the plugin, so bake it into a custom image,
then reference that image and enable the plugin.

1. Build an image with the plugin (match the NetBox version to the chart's
   `appVersion`):

   ```dockerfile
   # Dockerfile
   FROM netboxcommunity/netbox:v4.6
   RUN /opt/netbox/venv/bin/pip install netbox-opennms-plugin==0.1.0
   ```

   ```bash
   docker build -t registry.example.org/netbox-opennms:v4.6 .
   docker push registry.example.org/netbox-opennms:v4.6
   ```

2. In `values.yaml`, point the chart at that image, enable the plugin, and turn
   on the worker (the sync / drift-reconciler jobs need it):

   ```yaml
   image:
     repository: registry.example.org/netbox-opennms
     tag: v4.6

   plugins:
     - netbox_opennms          # the package must already be in the image (step 1)

   pluginsConfig:
     netbox_opennms:
       opennms_url: "https://opennms.example.org/opennms"
       opennms_username: "provision-svc"
       opennms_password: "********"     # prefer a Secret via extraConfig
       default_location: ""
       import_mode: "false"
       reconcile_orphans: "true"

   worker:
     enabled: true
   ```

3. Install or upgrade, then confirm the migrations ran:

   ```bash
   helm repo add netbox https://netbox-community.github.io/netbox-chart/
   helm upgrade --install netbox netbox/netbox -f values.yaml
   kubectl logs deploy/netbox | grep netbox_opennms   # → Applying netbox_opennms... OK
   ```

Keep `opennms_password` out of plaintext values — load the plugin config from a
`Secret` via the chart's `extraConfig`. On upgrades, rebuild the image with the
new plugin version, bump `image.tag`, and `helm upgrade`.

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

If no worker is running, the Requisition and Sync pages show a warning
and jobs stay pending until one starts. Each object's last-sync state (submitted /
succeeded-accepted / removed / failed, with the triggering user, time, and any
error) is shown on the **Device/VM detail page**, backed by the NetBox Job log as
the audit trail.

## OpenNMS-side setup

The plugin writes requisitions; it does **not** configure OpenNMS polling. For
monitoring to actually happen:

- **Provisioning account** — `opennms_username` needs a role that can read/write
  requisitions and trigger imports (e.g. the OpenNMS provisioning/REST role).
- **Detectors → poller packages** — the requisition's detectors tell OpenNMS which
  services to auto-discover, but OpenNMS only *polls* a discovered service if a
  matching **poller package** exists for it. The plugin cannot create poller
  packages — ensure your `poller-configuration.xml` covers the services your
  detectors discover (and the requisition's declared services).
- **Detector/policy discovery** — the detector and policy editors are populated
  live from your instance (`GET /rest/foreignSourcesConfig/{detectors,policies}`,
  the same API the OpenNMS UI uses), so the available classes and their parameters
  reflect what that OpenNMS actually has (including plugin-provided detectors). The
  built-in preset registry is only a curation overlay (friendly labels, sensible
  defaults, a shortlist). If OpenNMS is unreachable while editing, the editor
  degrades to the curated presets and notes it — you can still save (freeform class
  entry always works). Discovered results are cached briefly and refreshed at Sync.
- **Asset & metadata enrichment** — a Requisition can carry NetBox inventory into
  OpenNMS through two channels. **Asset mappings** map a NetBox attribute (serial,
  model, site, …) to a **fixed** OpenNMS node **asset field** (the `OnmsAssetRecord`
  set, discovered from `/foreignSourcesConfig/assets`; validated at save). **Metadata
  entries** attach an arbitrary `context`/`key`/`value` triad at **node / interface /
  service** scope (`context` defaults to `requisition`; a custom context must be
  `X-`-prefixed) — the open channel for anything without a fixed asset field, and the
  home for custom fields (`cf_<name>`). Values resolve per member; an unresolved value
  is simply omitted.
- **Minions / monitoring locations** — a node assigned to a non-`Default`
  monitoring location is only polled if a **Minion** is registered at that
  location. The plugin best-effort warns when a chosen location is unknown to
  OpenNMS, but cannot create it. The built-in `Default` location is polled by the
  OpenNMS core (no Minion required).

## Requisitions, membership, and node identity

A **Requisition**'s name *is* its OpenNMS Foreign Source name — user-chosen (it
must be Foreign-Source- and URL-path-safe: no whitespace or `# % & + ? / \ : * ' "`),
not derived. Its membership is a NetBox **filter** (FilterSet parameters, e.g.
`{"role": ["switch"], "tag": ["critical"]}`) applied to Devices and/or
VirtualMachines per its *object types*; you can seed the filter by **importing a
NetBox Saved Filter** (a one-time copy — no live link). A filter must actually
constrain each selected object type, so a typo or empty value can't silently
become a fleet-wide catch-all.

Filters must be **disjoint**. When several Requisitions match the same object it
is a **conflict**: the object is rendered into none of them and Sync of every
involved Requisition is blocked (frozen — the OpenNMS state stays exactly as last
synced) until you resolve it. Resolve by narrowing a filter — typically with a
negated parameter, e.g. `{"role": ["switch"], "tag__n": ["critical"]}` — or by
excluding the object (an excluded object never conflicts; it is monitored
nowhere). Conflicts are shown on the Requisition page, the Sync preview, the
dry-run, and the affected Device/VM page. The REST API follows the same
save-never-blocks rule but has **no warning channel** — after automated writes,
check the Sync preview (or the requisition page) for conflicts.

Node identity is the pair *(Foreign Source, type-qualified Foreign ID)* —
`device-{pk}` / `vm-{pk}` — so a re-sync updates a node in place and renaming a
Device only relabels it (never a duplicate). Moving an object between Requisitions
(a filter change) changes its Foreign Source, which OpenNMS treats as a new node;
the per-node **dry-run** surfaces such moves — and every add / remove / change
against the live OpenNMS state — before you Sync.

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
