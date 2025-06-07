import os
import logging

# --- Global Logging Configuration ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Main Webserver Configuration ---
CACHE_EXPIRATION_SECONDS = int(os.getenv("CACHE_EXPIRATION_SECONDS", "300"))
THIS_SERVER_BASE_URL = os.getenv("THIS_SERVER_BASE_URL", "http://127.0.0.1:8000")

# --- Rate Limiter Service Configuration ---
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "2"))
RATE_LIMIT_PERIOD_SECONDS = int(os.getenv("RATE_LIMIT_PERIOD_SECONDS", "5"))
MAIN_SERVER_REGISTER_URL = os.getenv("MAIN_SERVER_REGISTER_URL", "http://127.0.0.1:8000/register")
MAIN_SERVER_DEREGISTER_URL = os.getenv("MAIN_SERVER_DEREGISTER_URL", "http://127.0.0.1:8000/deregister")
# THIS_SERVICE_BASE_URL will be dynamically set by Docker Compose based on the service name
# For scaled services, Docker Compose sets the HOSTNAME env var to the container's hostname (e.g., rate_limiting_service-1)
# We use a default for local development outside Docker Compose.
THIS_SERVICE_BASE_URL = os.getenv("THIS_SERVICE_BASE_URL", f"http://{os.getenv('HOSTNAME', '127.0.0.1')}:8001")

# --- Redis Configuration ---
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))