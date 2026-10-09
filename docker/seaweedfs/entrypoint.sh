#!/bin/sh
# Stockage S3-compatible de développement (SeaweedFS).
# Les identifiants viennent de l'environnement ; aucune identité anonyme n'est déclarée,
# donc toute requête non signée est refusée.
set -eu
: "${S3_ACCESS_KEY:?S3_ACCESS_KEY manquant}"
: "${S3_SECRET_KEY:?S3_SECRET_KEY manquant}"
printf '{"identities":[{"name":"app","credentials":[{"accessKey":"%s","secretKey":"%s"}],"actions":["Admin","Read","Write","List","Tagging"]}]}' \
  "$S3_ACCESS_KEY" "$S3_SECRET_KEY" > /tmp/s3.json
exec weed server -dir=/data -s3 -s3.port=8333 -s3.config=/tmp/s3.json
