#!/usr/bin/env bash
# Compiles the Sass sources and copies the vendored JavaScript and icon fonts into
# src/core/static/core/dist/. The output is committed; `make assets-check` verifies it is current.
set -euo pipefail

cd "$(dirname "$0")/.."

STATIC=src/core/static/core
DIST="$STATIC/dist"

[ -d node_modules ] || npm ci --no-audit --no-fund

rm -rf "$DIST"
mkdir -p "$DIST/fonts"

# --quiet-deps silences Sass deprecations raised inside Bootstrap / Bootstrap Icons; our own
# SCSS keeps reporting its warnings. The @import deprecation is the one Bootstrap 5.3 forces on
# every consumer, so only that one is muted explicitly.
npx --no-install sass \
  --style=compressed --no-source-map --quiet-deps \
  --silence-deprecation=import \
  --load-path=node_modules \
  "$STATIC/scss/app.scss" "$DIST/app.css"

cp node_modules/bootstrap/dist/js/bootstrap.bundle.min.js "$DIST/bootstrap.bundle.min.js"
cp node_modules/htmx.org/dist/htmx.min.js "$DIST/htmx.min.js"
cp node_modules/bootstrap-icons/font/fonts/bootstrap-icons.woff2 "$DIST/fonts/"
cp node_modules/bootstrap-icons/font/fonts/bootstrap-icons.woff "$DIST/fonts/"

{
  echo "Third-party software shipped in this directory"
  echo "=============================================="
  echo
  echo "Bootstrap 5.3.8 - MIT licence"
  echo "-----------------------------"
  cat node_modules/bootstrap/LICENSE
  echo
  echo "Bootstrap Icons 1.13.2 - MIT licence"
  echo "------------------------------------"
  cat node_modules/bootstrap-icons/LICENSE
  echo
  echo "HTMX 2.0.11 - Zero-Clause BSD licence"
  echo "-------------------------------------"
  cat node_modules/htmx.org/LICENSE
} > "$DIST/LICENSES.txt"
