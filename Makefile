# Images tierces épinglées par digest (miroir Docker Hub de Google).
GITLEAKS_IMAGE ?= mirror.gcr.io/zricethezav/gitleaks:latest@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f
TRIVY_IMAGE ?= mirror.gcr.io/aquasec/trivy:latest@sha256:af6acf9a6b85dfe389a1941505c0ce9efef52a4719635e1a962f022a3d855daa
NGINX_IMAGE ?= mirror.gcr.io/library/nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10
# Options de build supplémentaires (ex. proxy d'entreprise : --secret id=extra_ca,src=ca.pem).
DOCKER_BUILD_OPTS ?=
# Options supplémentaires pour le scan Trivy (ex. proxy : --network host -e HTTPS_PROXY -e SSL_CERT_FILE=...).
TRIVY_RUN_OPTS ?=

.PHONY: lint test test-integration security image ci

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run python manage.py makemigrations --check --dry-run --settings=config.settings.test

test:
	uv run python manage.py migrate --noinput --settings=config.settings.test
	uv run pytest --cov --cov-report=term-missing --cov-fail-under=85

test-integration:
	uv run pytest -m integration -v

security:
	uv export --frozen --no-dev --format requirements-txt > .audit-requirements.txt
	uv run pip-audit --strict --disable-pip -r .audit-requirements.txt; status=$$?; rm -f .audit-requirements.txt; exit $$status
	uv run bandit -q -r src -x '*/tests/*' -ll
	docker run --rm -v "$(CURDIR):/repo" $(GITLEAKS_IMAGE) detect --source /repo --redact --no-banner

image:
	docker build $(DOCKER_BUILD_OPTS) -f docker/Dockerfile --target runtime -t talan-communities:ci .
	docker run --rm $(TRIVY_RUN_OPTS) -v /var/run/docker.sock:/var/run/docker.sock $(TRIVY_IMAGE) image --exit-code 1 --severity CRITICAL --ignore-unfixed talan-communities:ci
	docker run --rm --add-host web:127.0.0.1 -v "$(CURDIR)/docker/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro" $(NGINX_IMAGE) nginx -t

ci: lint test security image
