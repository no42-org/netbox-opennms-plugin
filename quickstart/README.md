# Quickstart: NetBox + the OpenNMS plugin (Web UI)

A throwaway, single-command NetBox deployment with **netbox-opennms** installed,
for manually exercising the plugin through the Web UI — create a Monitoring
Profile, click **Sync to OpenNMS**, and watch the node appear. Optionally boots a
disposable OpenNMS Horizon 36 so the whole loop is self-contained.

> ⚠️ For testing only — fixed throwaway secrets, no TLS, ephemeral data. Not for
> production. For a real install see the repo [README](../README.md).

## Run it

From this directory (`quickstart/`):

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

The plugin lives under **Plugins → Monitoring Profiles** (and a **OpenNMS Sync
Status** panel appears on Device/VM pages). Direct links:

- Monitoring Profiles — http://localhost:8000/plugins/opennms/monitoring-profiles/
- OpenNMS connection test — http://localhost:8000/plugins/opennms/connection-test/

## Seed test data

Once NetBox is up, load a set of sample Devices, VMs, and Monitoring Profiles so
there's something to sync immediately:

```bash
./seed.sh
# …or:  docker compose exec -T netbox \
#         /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < seed.py
```

It's **idempotent** (everything is `get_or_create`'d). It builds 3 sites × a few
roles, devices with multiple interfaces + additional IPs, VMs on a site-scoped
cluster, monitored services, and 8 Monitoring Profiles — including a multi-node
Foreign Source mixing devices **and** VMs (`netbox.raleigh.router`), a profile
with an OpenNMS-unknown location (trips the no-Minion warning), a disabled
profile, and one unmonitored object. Edit [`seed.py`](./seed.py) to taste.

After seeding, **Plugins → Monitoring Profiles** lists them; try **Sync all**, or
change a device's site/role and re-sync to watch a Foreign Source *move*.

## Do a sync (Web UI)

1. *(or run [`./seed.sh`](#seed-test-data) and skip to step 4)* **Create the
   prerequisites** (Organization/DCIM): a Site, a Device Role, a Manufacturer +
   Device Type, then a **Device** in that site/role.
2. Add an **Interface** to the device and assign it an **IP address**.
3. **Plugins → Monitoring Profiles → Add**: pick the device as the object, set its
   **Management IP** (optionally add monitored services / a location).
4. Open the profile (or the Device page) and click **Sync to OpenNMS**.
5. The **OpenNMS Sync Status** panel shows `submitted` → `succeeded-accepted`
   (the worker runs the job). With the bundled OpenNMS, open its UI → **Info →
   Nodes** and you'll see the device, grouped under Foreign Source
   `netbox.<site>.<role>`.

> Test connectivity first at
> http://localhost:8000/plugins/opennms/connection-test/ — it probes OpenNMS
> reachability + credentials before you sync.

## Point at your own OpenNMS

Edit [`configuration/plugins.py`](./configuration/plugins.py) — set `opennms_url`,
`opennms_username`, `opennms_password` — then `docker compose up -d` (no
`--profile opennms`). The plugin needs an account that can read/write requisitions
and trigger imports.

## How it's wired

- The plugin is **mounted** from the repo root (`../ → /source`) and added to
  `PYTHONPATH`, reusing the image's bundled `requests`/`lxml`. No rebuild on code
  changes — restart the `netbox` + `netbox-worker` containers to pick them up.
- `netbox` runs the web UI (gunicorn); `netbox-worker` runs `manage.py rqworker`
  — **required**, because Sync/Remove/Move run as background jobs (the profile
  page warns when no worker is running).
- `configuration/plugins.py` is mounted at `/etc/netbox/config/plugins.py` to set
  `PLUGINS` + `PLUGINS_CONFIG`.

## Tear down

```bash
docker compose --profile opennms down -v      # -v also drops the data volumes
```
