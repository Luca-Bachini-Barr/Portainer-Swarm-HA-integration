"""Constants for the Swarm Containers integration."""

from __future__ import annotations

DOMAIN = "swarm_containers"

CONF_API_TOKEN = "api_token"
CONF_ENDPOINT_ID = "endpoint_id"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_VERIFY_SSL = "verify_ssl"

DEFAULT_SCAN_INTERVAL = 60
MIN_SCAN_INTERVAL = 30
MAX_SCAN_INTERVAL = 900

# How many container stats requests may be in flight at once.
MAX_CONCURRENT_STATS = 6
REQUEST_TIMEOUT = 20
