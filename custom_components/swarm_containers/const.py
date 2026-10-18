"""Constants for the Swarm Containers integration."""

from __future__ import annotations

DOMAIN = "swarm_containers"
VERSION = "0.2.3"

CONF_API_TOKEN = "api_token"
CONF_ENDPOINT_ID = "endpoint_id"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_VERIFY_SSL = "verify_ssl"
CONF_IMAGE_CHECK = "image_check"
CONF_IMAGE_CHECK_INTERVAL = "image_check_interval"
CONF_STANDALONE = "standalone_containers"

DEFAULT_SCAN_INTERVAL = 60
MIN_SCAN_INTERVAL = 30
MAX_SCAN_INTERVAL = 900

# Registry checks are slow (about half a second per image), so they run on their own timer.
DEFAULT_IMAGE_CHECK_INTERVAL = 6 * 3600
MIN_IMAGE_CHECK_INTERVAL = 3600
MAX_IMAGE_CHECK_INTERVAL = 7 * 86400

# How many container stats requests may be in flight at once.
MAX_CONCURRENT_STATS = 6
MAX_CONCURRENT_IMAGE_CHECKS = 4
REQUEST_TIMEOUT = 20
IMAGE_CHECK_TIMEOUT = 90

# Device models; the bundled cards use these to tell devices apart.
MODEL_SWARM = "Swarm"
MODEL_NODE = "Swarm node"
MODEL_STACK = "Swarm stack"
MODEL_SERVICE = "Swarm service"
MODEL_CONTAINER = "Standalone container"

FRONTEND_URL = "/swarm_containers/swarm-cards.js"
