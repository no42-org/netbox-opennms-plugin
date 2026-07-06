# Quickstart: NetBox + the OpenNMS plugin (Web UI)

A throwaway, single-command NetBox deployment with **netbox-opennms** installed,
for manually exercising the plugin through the Web UI — create a **Requisition**,
click **Sync to OpenNMS**, and watch the node appear. Optionally boots a
disposable OpenNMS Horizon 36 so the whole loop is self-contained.

> ⚠️ For testing only — fixed throwaway secrets, no TLS, ephemeral data. Not for
> production. For a real install see the repo [README](../README.md).

## Run it

From the repo's [`quickstart/`](../quickstart/) directory:

```bash
# NetBox UI + background worker only (point the plugin at your own OpenNMS)
docker compose up -d

# …or the full self-contained loop incl. a disposable OpenNMS Horizon 36
docker compose --profile opennms up -d
```

First boot runs migrations + creates the superuser and (for OpenNMS) initializes
its schema, so allow a few minutes. Watch readiness with:

```bash
docker compose ps          # wait for netbox (and opennms) = healthy
docker compose logs -f netbox
```

| Service | URL | Login |
| --- | --- | --- |
| NetBox | http://localhost:8000 | `admin` / `admin` |
| OpenNMS *(with `--profile opennms`)* | http://localhost:8980/opennms | `admin` / `admin` |

The plugin lives under **Plugins → Requisitions** (and a **OpenNMS Sync Status**
panel appears on Device/VM pages). Direct links:

- Requisitions — http://localhost:8000/plugins/opennms/requisitions/
- Sync preview — http://localhost:8000/plugins/opennms/sync/
- Connect OpenNMS — http://localhost:8000/plugins/opennms/connection-test/

## Seed test data

Once NetBox is up, load a set of sample Devices, VMs, and Requisitions so there's
something to sync immediately:

```bash
./seed.sh
# …or:  docker compose exec -T netbox \
#         /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < seed.py
```

It's **idempotent** (everything is `get_or_create`'d). It builds 3 sites × a few
roles, devices with multiple interfaces + additional IPs, VMs on a site-scoped
cluster, and 5 **Requisitions** (each a site+role filter with inline detectors and
declared services) — including a multi-node Foreign Source mixing devices **and**
VMs (`netbox.raleigh.router`), a requisition with an OpenNMS-unknown location
(trips the no-Minion warning), per-object overrides (an excluded object, a
suppressed service, extra interfaces), and one unmonitored object. Edit
[`seed.py`](../quickstart/seed.py) to taste.

After seeding, **Plugins → Requisitions** lists them; try **Sync all** (or a
per-requisition **Dry run** first), or change a device's role and re-sync to watch
it move between Foreign Sources.

## Do a sync (Web UI)

1. *(or run [`./seed.sh`](#seed-test-data) and skip to step 4)* **Create the
   prerequisites** (Organization/DCIM): a Site, a Device Role, a Manufacturer +
   Device Type, then a **Device** in that site/role with a **primary IP** (add an
   Interface and assign it an IP address, then set it as the device's primary IP).
2. **Plugins → Requisitions → Add**: name it (e.g. `core-switches`), set a
   **filter** that selects your device — e.g. `{"role": ["<role-slug>"]}`, or
   **Import from Saved Filter** — and add a detector (e.g. ICMP) and declared
   services.
3. Open the Requisition and click **Dry run** to preview what a Sync would push.
4. Click **Sync to OpenNMS** (on the Requisition, the Sync preview, or the Device
   page).
5. The **OpenNMS Sync Status** panel shows `submitted` → `succeeded-accepted`
   (the worker runs the job). With the bundled OpenNMS, open its UI → **Info →
   Nodes** and you'll see the device, grouped under the Requisition's Foreign
   Source name.

> Test connectivity first at
> http://localhost:8000/plugins/opennms/connection-test/ — it probes OpenNMS
> reachability + credentials before you sync.

## Point at your own OpenNMS

Edit [`configuration/plugins.py`](../quickstart/configuration/plugins.py) — set `opennms_url`,
`opennms_username`, `opennms_password` — then `docker compose up -d` (no
`--profile opennms`). The plugin needs an account that can read/write requisitions
and trigger imports.

## How it's wired

- The plugin is **mounted** from the repo root (`../ → /source`) and added to
  `PYTHONPATH`, reusing the image's bundled `requests`/`lxml`. No rebuild on code
  changes — restart the `netbox` + `netbox-worker` containers to pick them up.
- `netbox` runs the web UI (gunicorn); `netbox-worker` runs `manage.py rqworker`
  — **required**, because Sync/Remove/Move run as background jobs (the Requisition
  and Sync pages warn when no worker is running).
- `configuration/plugins.py` is mounted at `/etc/netbox/config/plugins.py` to set
  `PLUGINS` + `PLUGINS_CONFIG`.

## Tear down

```bash
docker compose --profile opennms down -v      # -v also drops the data volumes
```
