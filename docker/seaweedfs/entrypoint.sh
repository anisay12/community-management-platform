#!/bin/sh
# S3-compatible development storage (SeaweedFS).
# Credentials come from the environment; no anonymous identity is declared,
# so any unsigned request is rejected.
set -eu
: "${S3_ACCESS_KEY:?S3_ACCESS_KEY is required}"
: "${S3_SECRET_KEY:?S3_SECRET_KEY is required}"
printf '{"identities":[{"name":"app","credentials":[{"accessKey":"%s","secretKey":"%s"}],"actions":["Admin","Read","Write","List","Tagging"]}]}' \
  "$S3_ACCESS_KEY" "$S3_SECRET_KEY" > /tmp/s3.json
exec weed server -dir=/data -s3 -s3.port=8333 -s3.config=/tmp/s3.json
