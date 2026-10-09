# Guide de développement

## Organisation du code

```
src/
├── config/              # configuration Django : réglages, URL, WSGI, Celery
│   └── settings/        # base.py (commun), dev.py, test.py, prod.py
└── core/                # briques transverses : sondes, middleware, logs, tâches
```

Chaque application métier (à partir du lot L1) suivra la même découpe : `models.py`, `selectors.py` (lectures, toujours filtrées par `visible_to(user)`), `services.py` (écritures transactionnelles et audit), `policies.py` (décisions d'autorisation), `views.py`, `tasks.py`, `tests/`.

## Réglages par environnement

| Module | Usage |
|---|---|
| `config.settings.dev` | Développement local (défaut de `manage.py`), logs lisibles, `DEBUG` |
| `config.settings.test` | Tests : cache mémoire, stockage mémoire, Celery exécuté immédiatement |
| `config.settings.prod` | Recette et production (défaut de l'image) : HTTPS, HSTS, cookies sécurisés ; refuse de démarrer sans `DJANGO_ALLOWED_HOSTS` |

## Variables d'environnement

| Variable | Rôle | Défaut | Secrète |
|---|---|---|---|
| `DJANGO_SETTINGS_MODULE` | Module de réglages | `config.settings.dev` (`manage.py`), `config.settings.prod` (image) | non |
| `DJANGO_SECRET_KEY` | Clé de signature Django | aucune (obligatoire) | **oui** |
| `DJANGO_ALLOWED_HOSTS` | Noms d'hôte acceptés, séparés par des virgules | vide (refusé en prod) | non |
| `DJANGO_DEBUG` | Mode debug | `false` | non |
| `DATABASE_URL` | Connexion PostgreSQL | aucune (obligatoire) | **oui** (mot de passe) |
| `DB_CONN_MAX_AGE` | Durée de vie des connexions (s) | `60` | non |
| `REDIS_URL` | Cache, verrous, broker Celery | aucune (obligatoire) | **oui** si authentifié |
| `S3_BUCKET` | Bucket des documents | aucune (obligatoire) | non |
| `S3_ENDPOINT_URL` | Point d'accès S3-compatible | vide (AWS) | non |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | Identifiants du stockage | vide | **oui** |
| `S3_REGION` | Région | vide | non |
| `EMAIL_URL` | Serveur SMTP (format `smtp://user:pass@hôte:port`) | `consolemail://` | **oui** si identifiants |
| `DEFAULT_FROM_EMAIL` | Expéditeur | `communautes@localhost` | non |
| `METRICS_TOKEN` | Jeton d'accès à `/metrics` (vide = désactivé) | vide | **oui** |
| `LOG_LEVEL` | Niveau de log | `INFO` | non |
| `LOG_JSON` | Logs au format JSON | `true` (`false` en dev) | non |

Les valeurs de `.env.example` sont réservées au développement local et ne doivent jamais être réutilisées ailleurs.

## Migrations

Jamais appliquées automatiquement au démarrage. En développement : `docker compose run --rm migrate`. En CI : `make test` applique toutes les migrations sur une base vide et `make lint` échoue si une migration manque.

## Tests

- `make test` : tests unitaires et d'intégration Django sur PostgreSQL réel, couverture minimale 85 %.
- `make test-integration` : vérifie que le bucket est privé (lecture anonyme refusée) contre un stockage S3-compatible réel. En local, avec Compose démarré :

  ```bash
  S3_INTEGRATION_ENDPOINT=http://localhost:8333 S3_INTEGRATION_BUCKET=talan-documents \
    S3_INTEGRATION_ACCESS_KEY=dev-only-s3 S3_INTEGRATION_SECRET_KEY=dev-only-s3-secret \
    make test-integration
  ```

## Observabilité

- `GET /healthz` : le processus répond (aucun accès à la base).
- `GET /readyz` : PostgreSQL et Redis répondent ; 503 sinon, avec le nom du composant en échec uniquement.
- `GET /metrics` : métriques Prometheus, accessibles seulement avec `Authorization: Bearer <METRICS_TOKEN>` ; bloqué par Nginx côté public, à collecter sur le réseau interne.
- Chaque requête porte un identifiant `X-Request-ID` (repris s'il est fourni et valide, sinon généré), renvoyé dans la réponse et présent dans chaque ligne de log JSON.

## Stockage de développement

MinIO n'étant plus distribué sur Docker Hub, l'environnement de développement utilise **SeaweedFS** comme stockage S3-compatible (`docker/seaweedfs/entrypoint.sh`). Aucune identité anonyme n'est déclarée : toute requête non signée est refusée. En production, le stockage managé de l'hébergeur retenu le remplace (décision D1 du cadrage).

## Mise à jour des images et des actions épinglées

Les images tierces sont épinglées par digest et tirées via `mirror.gcr.io` (miroir Docker Hub de Google, sans limite de débit anonyme). Pour mettre à jour un digest :

```bash
docker pull mirror.gcr.io/library/postgres:16-alpine
docker inspect --format '{{index .RepoDigests 0}}' mirror.gcr.io/library/postgres:16-alpine
```

Les actions GitHub sont épinglées par SHA de commit :

```bash
git ls-remote https://github.com/actions/checkout 'refs/tags/v4*'
```

Reporter la nouvelle valeur dans `compose.yaml`, `Makefile`, `docker/Dockerfile` ou `.github/workflows/ci.yml`, puis lancer `make ci`.
