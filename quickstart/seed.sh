#!/usr/bin/env bash
# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
# Seed the quickstart NetBox with test Devices/VMs/Profiles (idempotent).
# Run from anywhere; it locates the compose project from this script's dir.
set -euo pipefail
cd "$(dirname "$0")"
exec docker compose exec -T netbox \
  /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < seed.py
