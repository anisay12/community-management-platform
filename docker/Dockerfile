# syntax=docker/dockerfile:1.7
# Images épinglées par digest. Miroir Docker Hub de Google pour éviter les limites de débit.
ARG PYTHON_IMAGE=mirror.gcr.io/library/python:3.13-slim@sha256:bf44cdfcb76cd3b41e879bc058fc37ec5872002ccfde7fcb765e218cde0cd79c

FROM ${PYTHON_IMAGE} AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
# Proxy d'entreprise : fournir son autorité de certification avec
# `--secret id=extra_ca,src=<bundle.pem>` (facultatif, jamais copié dans l'image).
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then \
      export SSL_CERT_FILE=/run/secrets/extra_ca PIP_CERT=/run/secrets/extra_ca; \
    fi; \
    pip install --disable-pip-version-check uv==0.11.32 \
    && uv sync --frozen --no-dev --no-install-project
COPY manage.py ./
COPY src ./src
# Valeurs factices propres à collectstatic : non conservées dans l'image (pas d'ENV).
RUN DJANGO_SETTINGS_MODULE=config.settings.prod \
    DJANGO_SECRET_KEY=collectstatic-build-only \
    DJANGO_ALLOWED_HOSTS=localhost \
    DATABASE_URL=postgres://build:build@localhost:5432/build \
    REDIS_URL=redis://localhost:6379/0 \
    S3_BUCKET=build \
    /app/.venv/bin/python manage.py collectstatic --noinput

FROM ${PYTHON_IMAGE} AS runtime
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod
USER app
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-", "--forwarded-allow-ips", "*"]
