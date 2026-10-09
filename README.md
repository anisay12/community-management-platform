# Plateforme Communautés TALAN

Plateforme interne de communautés professionnelles : communautés, contenus, documents, retours d'expérience, événements, puis formations et compétences.

**État : lot L0 livré (socle technique).** Aucune fonctionnalité métier n'est encore disponible. L'application démarre, se supervise et passe sa chaîne d'intégration continue ; les comptes, communautés et contenus arrivent dans les lots suivants (voir la spécification).

## Prérequis

- Docker ≥ 24 avec Docker Compose v2
- [uv](https://docs.astral.sh/uv/) ≥ 0.4 (pour lancer les tests hors conteneur)
- Make

## Démarrage rapide

```bash
cp .env.example .env
docker compose up -d --build --wait
curl -fsS localhost:8000/readyz
# {"status": "ok", "checks": {"database": true, "redis": true}}
```

Les migrations ne sont jamais appliquées automatiquement par l'application : le service `migrate` les applique au démarrage de l'environnement de développement, et `docker compose run --rm migrate` les rejoue.

Derrière un proxy d'entreprise qui intercepte TLS, construire l'image en fournissant l'autorité de certification (elle n'est pas copiée dans l'image) :

```bash
docker build --secret id=extra_ca,src=/chemin/ca.pem -f docker/Dockerfile --target runtime -t talan-communities:dev .
docker compose up -d --no-build --wait
```

## Commandes utiles

| Commande | Effet |
|---|---|
| `make lint` | Ruff (lint + format) et détection des migrations manquantes |
| `make test` | Migrations sur base vide, tests et couverture (seuil 85 %) |
| `make test-integration` | Test du stockage S3 privé (variables `S3_INTEGRATION_*` requises) |
| `make security` | Audit des dépendances, Bandit, recherche de secrets (gitleaks) |
| `make image` | Construction de l'image, scan Trivy, validation de la configuration Nginx |
| `make ci` | Toute la chaîne, comme en intégration continue |

`make test` attend PostgreSQL sur `localhost:5432` (base `talan_test`, utilisateur `postgres`) et Redis sur `localhost:6379`, ou les URL fournies par `DATABASE_URL` et `REDIS_URL`.

## Services locaux

| Service | Adresse |
|---|---|
| Application | http://localhost:8000 |
| Stockage S3 (SeaweedFS) | http://localhost:8333 |
| Mailpit (e-mails de développement) | http://localhost:8025 |
| PostgreSQL | localhost:5432 |
| Redis | localhost:6379 |

## Documentation

- [Cadrage du projet](docs/cadrage.md)
- [Spécification du MVP](.internal/specs/2026-10-09-mvp-connaissance-design.md)
- [Décisions d'architecture (ADR)](docs/decisions/INDEX.md)
- [Guide de développement](docs/development.md)
