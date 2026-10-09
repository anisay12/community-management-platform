import redis
import structlog
from django.conf import settings
from django.db import DatabaseError, connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

logger = structlog.get_logger(__name__)


@never_cache
@require_GET
def healthz(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


def _check_database() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() == (1,)
    except DatabaseError:
        logger.warning("readiness_check_failed", component="database", exc_info=True)
        return False


def _check_redis() -> bool:
    try:
        client = redis.Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1
        )
        return bool(client.ping())
    except redis.RedisError:
        logger.warning("readiness_check_failed", component="redis", exc_info=True)
        return False


@never_cache
@require_GET
def readyz(request: HttpRequest) -> JsonResponse:
    checks = {"database": _check_database(), "redis": _check_redis()}
    ok = all(checks.values())
    return JsonResponse(
        {"status": "ok" if ok else "unavailable", "checks": checks},
        status=200 if ok else 503,
    )
