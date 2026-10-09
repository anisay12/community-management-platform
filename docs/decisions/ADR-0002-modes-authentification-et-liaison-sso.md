# ADR-0002 — Modes d'authentification et liaison SSO par identifiant immuable

- Statut : accepté
- Date : 2026-10-09
- Contexte de décision : revue critique de la spécification MVP (`.internal/specs/2026-10-09-mvp-connaissance-design.md`, § 4)

## Contexte

Le fournisseur d'identité de Talan (probablement Microsoft Entra ID) n'est pas accessible au démarrage du projet. Le MVP utilise donc des comptes locaux avec mot de passe. Le SSO sera activé plus tard. Deux risques apparaissent à la bascule : des mots de passe locaux qui restent actifs et contournent les règles de l'entreprise (MFA, désactivation au départ), et un rapprochement des comptes par e-mail, alors qu'une adresse peut être réattribuée à une autre personne.

## Décision

- Un réglage `AUTH_MODE` prend trois valeurs : `local` (MVP), `mixed` (transition : SSO proposé, mot de passe encore accepté) et `sso_only`.
- Un modèle `ExternalIdentity` (`provider`, `subject`, unique) relie un compte à l'identifiant immuable de l'IdP (claim `oid` ou `sub`). L'e-mail ne sert qu'à la première liaison d'un compte existant ; ensuite seul `subject` fait foi.
- En `sso_only`, les mots de passe locaux sont rendus inutilisables et le formulaire local est refusé côté serveur, sauf pour un unique compte d'urgence (« bris de glace ») protégé par MFA, dont chaque connexion est auditée et alertée.
- La désactivation suit l'IdP : contrôle à chaque connexion et synchronisation nocturne.

## Justification

- Aucune porte d'entrée par mot de passe ne survit au passage au SSO, sans pour autant perdre l'accès en cas de panne de l'IdP.
- Un e-mail réattribué ne donne jamais accès au compte de l'ancien titulaire.
- Le développement du MVP n'est pas bloqué par l'absence d'IdP.

## Conséquences

- Le modèle `ExternalIdentity` et le réglage `AUTH_MODE` sont créés dès le lot L1, même si seul le mode `local` est utilisé au MVP.
- Une commande de migration rend les mots de passe inutilisables lors du passage en `sso_only` ; elle est tracée dans l'audit.
- Les tests couvrent : refus du formulaire local en `sso_only`, liaison par `subject`, création d'un compte `pending` pour un utilisateur inconnu, compte d'urgence seul autorisé.
