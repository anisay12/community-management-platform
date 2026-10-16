from core.logging import build_logging

from .base import *

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

LOG_JSON = env.bool("LOG_JSON", default=False)
LOGGING = build_logging(level=LOG_LEVEL, json=LOG_JSON)

REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}
