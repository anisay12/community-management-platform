import structlog

_SHARED_PROCESSORS = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]


def configure_structlog() -> None:
    structlog.configure(
        processors=[*_SHARED_PROCESSORS, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def build_logging(level: str = "INFO", json: bool = True) -> dict:
    renderer = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "structured": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processors": [
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.format_exc_info,
                    renderer,
                ],
                "foreign_pre_chain": _SHARED_PROCESSORS,
            }
        },
        "handlers": {"stderr": {"class": "logging.StreamHandler", "formatter": "structured"}},
        "root": {"handlers": ["stderr"], "level": level},
        "loggers": {
            "django.request": {"level": "WARNING"},
            "django.db.backends": {"level": "WARNING"},
        },
    }
