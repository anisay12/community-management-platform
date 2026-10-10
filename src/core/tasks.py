import structlog
from celery import shared_task
from django.db import transaction

logger = structlog.get_logger(__name__)


@shared_task
def ping() -> str:
    return "pong"


def delay_on_commit(task, *args, **kwargs) -> None:
    """Queue ``task`` with ``args`` once the current transaction commits.

    The data is already committed then: a broker outage is logged, never raised, so the
    request that wrote it does not answer a server error.
    """

    def send():
        try:
            task.delay(*args, **kwargs)
        except Exception:  # broker down: the write stands, the task is lost and logged
            logger.warning("tasks.enqueue_failed", task=task.name, exc_info=True)

    transaction.on_commit(send)
