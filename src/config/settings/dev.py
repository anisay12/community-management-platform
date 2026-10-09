from core.logging import build_logging

from .base import *

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]  # noqa: S104 (dev en conteneur)

LOG_JSON = env.bool("LOG_JSON", default=False)
LOGGING = build_logging(level=LOG_LEVEL, json=LOG_JSON)
