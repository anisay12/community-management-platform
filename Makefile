# Third-party images pinned by digest (Google's Docker Hub mirror).
GITLEAKS_IMAGE ?= mirror.gcr.io/zricethezav/gitleaks:latest@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f
TRIVY_IMAGE ?= mirror.gcr.io/aquasec/trivy:latest@sha256:af6acf9a6b85dfe389a1941505c0ce9efef52a4719635e1a962f022a3d855daa
NGINX_IMAGE ?= mirror.gcr.io/library/nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10
# Extra build options (e.g. corporate proxy: --secret id=extra_ca,src=ca.pem).
DOCKER_BUILD_OPTS ?=
# Extra options for the Trivy scan (e.g. proxy: --network host -e HTTPS_PROXY -e SSL_CERT_FILE=...).
TRIVY_RUN_OPTS ?=

.PHONY: lint test test-integration security image ci messages i18n-check dev-admin assets assets-check test-a11y

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run python manage.py makemigrations --check --dry-run --settings=config.settings.test

messages:
	cd src && uv run python ../manage.py makemessages -l fr --no-location --no-obsolete --ignore=.venv --settings=config.settings.test
	cd src && uv run python ../manage.py compilemessages --settings=config.settings.test

# Fails if the French catalogue has untranslated or fuzzy entries, or is out of date.
i18n-check:
	@cd src && uv run python ../manage.py makemessages -l fr --no-location --no-obsolete --ignore=.venv --settings=config.settings.test
	@git diff --exit-code src/locale
	@test -z "$$(msgattrib --untranslated src/locale/fr/LC_MESSAGES/django.po)" || { echo 'Untranslated entries in the fr catalogue'; exit 1; }
	@test -z "$$(msgattrib --only-fuzzy src/locale/fr/LC_MESSAGES/django.po)" || { echo 'Fuzzy entries in the fr catalogue'; exit 1; }

# Compiles the Sass sources and vendors Bootstrap / HTMX / icon fonts into src/core/static/core/dist/.
assets:
	@bash scripts/build-assets.sh

# Fails if the committed build output is not what `make assets` produces.
assets-check: assets
	@test -z "$$(git status --porcelain src/core/static/core/dist)" || { echo 'Compiled assets are out of date: run `make assets` and commit src/core/static/core/dist'; git status --short src/core/static/core/dist; exit 1; }

test:
	cd src && uv run python ../manage.py compilemessages --settings=config.settings.test
	uv run python manage.py migrate --noinput --settings=config.settings.test
	uv run pytest --cov --cov-report=term-missing --cov-fail-under=85

# Browser accessibility checks (axe-core + keyboard) with Chromium; needs `playwright install chromium` once.
test-a11y:
	@test -d node_modules/axe-core || npm ci --no-audit --no-fund
	uv run pytest -m a11y -p no:cacheprovider --no-cov

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
	docker run --rm --add-host web:127.0.0.1 --add-host s3:127.0.0.1 -e STORAGE_ORIGIN=http://s3:8333 -e NGINX_ENVSUBST_FILTER=^STORAGE_ -v "$(CURDIR)/docker/nginx/default.conf:/etc/nginx/templates/default.conf.template:ro" $(NGINX_IMAGE) nginx -t

# Development only: creates a superuser (EMAIL=you@example.com make dev-admin) in the running web container.
dev-admin:
	@test -n "$(EMAIL)" || { echo 'Usage: EMAIL=you@example.com make dev-admin'; exit 1; }
	docker compose exec web python manage.py create_dev_admin --email "$(EMAIL)"

ci: lint i18n-check assets-check test test-a11y security image
