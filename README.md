# netbox-opennms-plugin

A [NetBox](https://netboxlabs.com/) plugin that provisions NetBox devices and
virtual machines into [OpenNMS](https://www.opennms.com/) (Horizon 35) via the
OpenNMS REST provisioning API. NetBox is the source of truth; OpenNMS monitoring
is a derived artifact kept in sync from NetBox intent.

> **Status:** early development (walking skeleton). See
> `_bmad-output/planning-artifacts/epics.md` for the roadmap.

## Compatibility

| | |
| --- | --- |
| NetBox | 4.6.x |
| Python | 3.12+ |
| OpenNMS | Horizon 35 |
| License | Apache-2.0 |

## Installation (development)

```bash
pip install -e .
```

Then enable the plugin in NetBox's `configuration.py`:

```python
PLUGINS = ["netbox_opennms"]

PLUGINS_CONFIG = {
    "netbox_opennms": {
        "opennms_url": "https://opennms.example.org/opennms",
        "opennms_username": "provision-svc",   # provision/rest role account
        "opennms_password": "********",         # store via your secrets mechanism
        "default_location": "",                 # OpenNMS monitoring location
        "import_mode": "false",                 # rescanExisting value
    },
}
```

A running NetBox **RQ worker** is required for background sync jobs (added in
later stories). For plugin development, set `DEVELOPER = True` in NetBox's
`configuration.py` to enable `makemigrations`.

## Testing

```bash
./manage.py test netbox_opennms
```

## License

Apache-2.0 — see [LICENSE](./LICENSE).
