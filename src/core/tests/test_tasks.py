from unittest import mock

import pytest
import structlog
from django.conf import settings
from kombu.exceptions import OperationalError

from core.tasks import delay_on_commit, ping


def test_ping_task_runs():
    assert ping.delay().get(timeout=5) == "pong"


def test_celery_reliability_settings():
    assert settings.CELERY_TASK_ACKS_LATE is True
    assert settings.CELERY_TASK_REJECT_ON_WORKER_LOST is True
    assert settings.CELERY_WORKER_PREFETCH_MULTIPLIER == 1
    assert settings.CELERY_TASK_SOFT_TIME_LIMIT == 240
    assert settings.CELERY_TASK_TIME_LIMIT == 300


@pytest.mark.django_db
def test_delay_on_commit_queues_once_the_transaction_commits(django_capture_on_commit_callbacks):
    task = mock.Mock()
    with django_capture_on_commit_callbacks(execute=True):
        delay_on_commit(task, 1, kind="x")
        task.delay.assert_not_called()
    task.delay.assert_called_once_with(1, kind="x")


@pytest.mark.django_db
def test_delay_on_commit_logs_a_broker_outage(django_capture_on_commit_callbacks):
    task = mock.Mock()
    task.name = "app.task"
    task.delay.side_effect = OperationalError("broker down")
    with (
        structlog.testing.capture_logs() as logs,
        django_capture_on_commit_callbacks(execute=True),
    ):
        delay_on_commit(task, 1)  # does not raise: the write has committed
    assert [(log["event"], log["task"]) for log in logs] == [("tasks.enqueue_failed", "app.task")]
