# ADR-0001 — Servir les fichiers privés via Nginx (X-Accel-Redirect)

- Statut : accepté
- Date : 2026-10-09
- Contexte de décision : revue critique de la spécification MVP (`.internal/specs/2026-10-09-mvp-connaissance-design.md`, § 8)

## Contexte

Les documents des communautés sont stockés dans un stockage objet privé (MinIO en développement, S3 ou Azure Blob en production). La plateforme doit garantir qu'aucune URL ne permet de contourner ses permissions (§ 5 du prompt maître). La première version de la spec redirigeait le navigateur vers une URL présignée valable 60 secondes.

Une URL présignée est un jeton porteur : pendant sa durée de validité, toute personne qui la possède (copiée dans une messagerie, conservée dans un journal de proxy ou dans l'historique du navigateur) télécharge le fichier sans contrôle. La prévisualisation PDF s'affiche alors depuis le domaine du stockage, où l'application maîtrise mal les en-têtes de sécurité.

## Décision

L'application vérifie le droit d'accès à chaque requête, journalise le téléchargement, puis renvoie une réponse vide portant l'en-tête `X-Accel-Redirect: /_protected/<storage_key>`. Nginx sert le fichier depuis un emplacement `internal` qui relaie la lecture vers le stockage privé avec des identifiants de service. L'application fixe `Content-Disposition`, `Content-Type`, `X-Content-Type-Options: nosniff`, `Cache-Control: private, no-store` et, pour la prévisualisation, `Content-Security-Policy: sandbox`.

## Justification

- Aucune URL de stockage n'est jamais transmise au navigateur : la fenêtre d'exposition d'un lien fuité disparaît.
- Chaque téléchargement est réautorisé : un membre retiré d'une communauté perd l'accès immédiatement.
- Les en-têtes de sécurité sont contrôlés par l'application, sur son propre domaine.
- Coût : le trafic de fichiers transite par Nginx. Au volume estimé (environ 250 Go sur 3 ans, fichiers ≤ 100 Mo), c'est négligeable.

## Conséquences

- Nginx devient obligatoire devant l'application dans tous les environnements hors développement local ; en développement, un repli `FileResponse` en streaming n'est actif que si `DEBUG`.
- La configuration Nginx doit savoir s'authentifier auprès du stockage (identifiants de service ou URL signée générée côté serveur, jamais renvoyée au client) ; l'implémentation dépend du fournisseur retenu (décision D1).
- Un CDN public ne peut pas être utilisé pour les documents privés.
- Tests obligatoires : accès direct au bucket refusé, `/_protected/` inaccessible depuis l'extérieur, perte d'accès après retrait de la communauté.
