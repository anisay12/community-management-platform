# Spécification MVP « Communautés & connaissance » — Plateforme Communautés TALAN

> Statut : **à relire** · Date : 2026-10-09
> Source : [document de cadrage](../../docs/cadrage.md) (§ 9.1, lots L0 à L11).
> Décision validée : périmètre « Connaissance d'abord » (formations, compétences et quiz en release 2).
> Décisions encore ouvertes, appliquées ici avec leur valeur par défaut : D1 hébergement agnostique, D2 auth locale + OIDC prêt, D4 thème neutre à tokens, D5 français i18n-ready, D6 accès manager agrégé (hors MVP de toute façon), D8 durées de conservation par défaut.

---

## 1. Objectif et non-objectifs

**Objectif** : un collaborateur peut rejoindre des communautés, y lire et publier des contenus (discussions, questions, annonces, articles, REX), déposer et télécharger des documents en sécurité, s'inscrire à des événements, être notifié et tout retrouver par la recherche. Un responsable anime et mesure sa communauté. Un administrateur gouverne la plateforme.

**Hors MVP (explicitement)** : formations, parcours, compétences, quiz/tests blancs, certifications, mentorat, tableau de bord manager, SSO en production, Teams/calendrier d'entreprise, IA, temps réel WebSocket, multilingue effectif, événements récurrents, feuilles de présence, exports PDF.

Le modèle de données MVP **réserve** les points d'extension (ex. `Event.kind = training`, `Tag` partagé) sans créer de tables vides.

---

## 2. Conventions transverses

| Sujet | Règle |
|---|---|
| Identifiants | `id` bigint interne ; `public_id` UUIDv4 unique, seul identifiant exposé dans URL et API ; `slug` lisible pour les communautés |
| Horodatage | `created_at`, `updated_at` (UTC) sur toutes les tables métier ; affichage dans le fuseau de l'utilisateur (`UserProfile.timezone`, défaut `Europe/Paris`) |
| Archivage | `archived_at` nullable ; les sélecteurs excluent l'archivé par défaut ; aucune suppression `CASCADE` de contenu métier depuis une communauté (`on_delete=PROTECT`) |
| Couche d'accès | `selectors.py` (lectures, toujours via `visible_to(user)`), `services.py` (écritures transactionnelles), `policies.py` (`can_<action>(user, obj) -> bool`) |
| Erreurs métier | Exception `DomainError(code, message)` traduite en message utilisateur (toast) ou en 400/409 API |
| Objet privé non autorisé | Réponse **404** (ne révèle pas l'existence) ; action interdite sur objet visible : **403** |
| Texte riche | Markdown restreint (titres, listes, liens, code, citations, tableaux), rendu serveur puis nettoyé par `nh3` ; pas d'HTML brut |
| Pagination | Curseur (`?cursor=`) pour les flux ; numérotée pour catalogues et résultats de recherche (taille 20, max 50) |
| Audit | Toute action listée § 11 crée un `AuditEvent` dans la même transaction |
| i18n | Toutes les chaînes passent par `gettext` ; locale `fr` seule activée |

---

## 3. Lot L0 — Socle technique

**Livrables** : projet `src/config` (settings `base/dev/test/prod` lus depuis l'environnement via `django-environ`), `compose.yaml` (web, worker, beat, postgres 16, redis 7, minio, clamav, mailpit), Dockerfile multi-étapes non-root, `uv.lock`, `.env.example`, CI GitHub Actions (ou script `make ci` exécutable localement tant que GitHub n'est pas connecté).

**Contrôles CI** : `ruff check`, `ruff format --check`, `pytest` (avec PostgreSQL réel en service), `python manage.py makemigrations --check`, migrations depuis une base vide, `pip-audit`, `bandit -q`, `gitleaks`, build de l'image, `trivy image` (bloquant sur CRITICAL).

**Observabilité** : middleware `X-Request-ID` (génère ou propage, ajouté aux logs et à la réponse) ; logs JSON (`structlog`) ; `/healthz` (processus vivant) et `/readyz` (base + Redis joignables) ; métriques `/metrics` (`django-prometheus`) protégées par réseau/jeton.

**Acceptation** : `docker compose up` puis `make demo` rend l'application utilisable sur `http://localhost:8000` ; CI verte ; `grep` gitleaks sans fuite.

---

## 4. Lot L1 — Comptes, rôles, audit

### Modèles

- `accounts.User(AbstractBaseUser, PermissionsMixin)` : `email` (unique, comparaison insensible à la casse via contrainte `Lower(email)`), `first_name`, `last_name`, `status` ∈ {`pending`, `active`, `suspended`, `deactivated`}, `is_staff`, `last_seen_at` (mis à jour au plus 1 fois / 5 min), `public_id`.
- `accounts.UserProfile` (1–1) : `job_title`, `bio` (≤ 2 000 car.), `avatar` (stockage objet, optionnel), `timezone`, `interests` (M2M `Tag`), `profile_visibility` ∈ {`private`, `communities`, `company`} (défaut `company` pour nom/poste/communautés ; bio et intérêts selon le choix), `is_discoverable` (défaut vrai).
- `organizations.OrganizationUnit` : `name`, `code` unique, `parent` (FK nullable, arbre).
- `organizations.Employment` (1–1 User) : `unit`, `manager` (FK User nullable, ≠ soi-même, CHECK).
- Rôles globaux = `Group` Django : `collaborateur`, `createur_communautes`, `admin_fonctionnel`, `admin_technique`, `auditeur`. Le statut « manager » est **déduit** de `Employment.manager`.
- `audit.AuditEvent` : `actor` (FK nullable, `SET_NULL`), `action` (code), `target_type`, `target_id`, `community` (FK nullable), `changes` (JSON avant/après restreint aux champs non sensibles), `ip_hash` (HMAC de l'IP, pas l'IP brute), `request_id`, `created_at`. Aucune vue de modification ; rôle DB applicatif sans `DELETE` sur cette table en prod (documenté).

### Authentification

- Connexion e-mail + mot de passe ; `argon2` ; validateurs : longueur ≥ 12, similarité, mots de passe communs ; `django-axes` : 5 échecs → verrou 15 min par (e-mail, IP), message neutre (« identifiants invalides »).
- Réinitialisation par e-mail (jeton Django, 1 h), réponse identique que le compte existe ou non.
- MFA TOTP (`django-otp`) obligatoire pour `admin_fonctionnel`, `admin_technique`, `is_staff` ; l'admin Django est servi sur un chemin configurable et exige MFA.
- Sessions : stockage `cached_db`, expiration d'inactivité 8 h, `SESSION_COOKIE_SECURE`, `HttpOnly`, `SameSite=Lax`, rotation à la connexion ; suspension → suppression des sessions de l'utilisateur.
- OIDC : `mozilla-django-oidc` configuré mais désactivé (`OIDC_ENABLED=false`) ; rapprochement par e-mail ; création de compte `pending` si inconnu.
- Création des comptes au MVP : par l'admin fonctionnel (formulaire + import CSV contrôlé : e-mail, prénom, nom, unité, e-mail manager), e-mail d'activation (lien 72 h). Pas d'inscription libre.

### Pages

Connexion, mot de passe oublié, activation, profil (lecture/édition), préférences, pages d'erreur 403/404/429/500 au design de l'application (la 500 est statique, sans dépendance à la base).

### Acceptation (tests)

Compte `pending` ou `suspended` → redirection connexion avec message ; collaborateur → 404 sur l'admin ; brute force → verrou ; changement de rôle → `AuditEvent`; import CSV : lignes invalides rapportées sans création partielle (transaction).

---

## 5. Lot L2 — Design system et navigation

- Bootstrap 5.3 compilé avec variables Sass surchargées par **tokens** (`--tl-color-primary`, `--tl-font-sans`…) dans `core/static/core/tokens.scss` ; un seul fichier à modifier à réception de la charte. Logo : emplacement `brand/logo.svg` avec placeholder textuel « Communautés » (aucun logo Talan inventé).
- Mode clair par défaut ; mode sombre via `prefers-color-scheme` (tokens dédiés).
- Composants en templates inclus (`{% include "components/card.html" with … %}`) : carte de contenu, carte de communauté, état vide (illustration + action), alerte, toast (aria-live), modale accessible, pagination, badge de rôle, avatar, fil d'Ariane, onglets, formulaire (libellé, aide, erreur liée par `aria-describedby`).
- Navigation principale MVP : Accueil, Communautés, Ressources, Événements, Retours d'expérience, Recherche (champ), Notifications (cloche + compteur), Profil (menu), Administration (si autorisé). Les entrées Formations, Compétences, Tests blancs ne sont **pas affichées** au MVP (pas d'écran vide factice).
- Mobile : barre supérieure + menu latéral (offcanvas) ; zones tactiles ≥ 44 px.
- HTMX : réactions, abonnement, filtres de catalogue, chargement de flux, marquage des notifications ; chaque interaction HTMX a un repli fonctionnel sans JS pour les actions principales (formulaire POST classique).
- Accessibilité : un `h1` par page, landmarks, lien d'évitement, focus visible, contraste AA ; `axe-core` via Playwright en CI sur les pages principales (bloquant sur violations « serious »/« critical »).

---

## 6. Lot L3 — Communautés

### Modèles

- `CommunityCategory` : `name` unique, `slug`, `description`, `icon` (nom d'icône Bootstrap Icons), `order`, `is_active`. Données initiales : les 12 catégories du prompt (migration de données, modifiables ensuite).
- `Community` : `name` (unique parmi non archivées), `slug` unique, `category` (FK `PROTECT`), `tagline` (≤ 160), `description` (Markdown), `rules` (Markdown), `objectives` (Markdown), `cover_image` (optionnel), `access_mode` ∈ {`open`, `request`, `invite`}, `listed` (bool ; pour `invite`, afficher ou non le titre dans le catalogue), `allow_member_uploads` (bool), `require_post_review` (bool, défaut faux), `status` ∈ {`active`, `suspended`, `archived`}, `created_by`, `tags` (M2M), `search_vector`, `member_count` (dénormalisé, mis à jour par service).
- `CommunityMembership` : `community`, `user`, `role` ∈ {`member`, `contributor`, `expert`, `moderator`, `animator`, `owner`} ; unique (`community`, `user`) ; `joined_at` ; `notification_level` ∈ {`all`, `highlights`, `none`}.
- `MembershipRequest` : `community`, `user`, `message` (≤ 500), `status` ∈ {`pending`, `accepted`, `rejected`, `cancelled`}, `decided_by`, `decided_at`, `decision_note` ; index unique partiel sur (`community`, `user`) où `status = pending`.
- `CommunityInvitation` : `community`, `invited_user`, `invited_by`, `role` (≤ `animator`), `status` ∈ {`pending`, `accepted`, `declined`, `revoked`, `expired`}, `expires_at` (14 j).
- `CommunityCreationRequest` (pour les collaborateurs sans le rôle `createur_communautes`) : nom, catégorie, justification, statut, décideur.

### Règles métier (services)

- Une communauté a **toujours au moins un `owner`** : le dernier owner ne peut ni partir ni être rétrogradé (erreur `last_owner`).
- `join(user, community)` : `open` → membre ; `request` → crée une demande (idempotent si déjà pendante) ; `invite` → refus (`invite_only`).
- `accept_request` / `reject_request` : animateur+ ; notification au demandeur.
- `invite(user)` : animateur+ ; un rôle ≥ `animator` ne peut être attribué que par un owner.
- `leave` : supprime l'adhésion, conserve les contenus (auteur inchangé).
- `change_role` : owner (ou admin fonctionnel) ; audité.
- `suspend` / `archive` : admin fonctionnel (owner peut archiver la sienne) ; une communauté suspendue ou archivée est en lecture seule ; archivée = hors catalogue et hors recherche par défaut.
- Toutes les modifications de membres mettent à jour `member_count` dans la même transaction (`F()`).

### Visibilité (`Community.objects.visible_to(user)`)

- `open` et `request` : visibles (métadonnées) par tout collaborateur actif ; contenu de `request` visible des membres seulement.
- Clarification : une communauté `open` expose son contenu à tout collaborateur actif (lecture), l'écriture exige l'adhésion.
- `invite` : visible des membres ; titre seul visible des autres si `listed`, sinon 404.
- Admin fonctionnel : métadonnées de toutes ; contenu privé seulement via l'action « Accéder en tant qu'administrateur » avec motif obligatoire, auditée.

### Pages

Catalogue (filtres catégorie, mode d'accès, « mes communautés », tri : activité récente / membres / alphabétique ; recherche par nom) ; détail (en-tête, bouton contextuel adhérer / demander / quitter / en attente, onglets : Fil, Ressources, REX, Événements, Membres, À propos) ; gestion (paramètres, membres et rôles, demandes, invitations) ; création.

### Acceptation

Matrice de visibilité testée pour les 3 modes × {non-membre, membre, animateur, admin} ; dernier owner protégé ; deux demandes simultanées → une seule pendante (contrainte) ; changements de rôles audités.

---

## 7. Lot L4 — Publications et interactions

### Modèles

- `Tag` : `name` unique (normalisé minuscule, sans accents pour la clé), `slug`. Création libre par contributeurs+, fusion par admin.
- `Post` : `community` (`PROTECT`), `author` (`SET_NULL` + `author_display` figé pour anonymisation), `kind` ∈ {`discussion`, `question`, `announcement`, `article`}, `title` (≤ 200), `body` (Markdown ≤ 50 000), `body_html` (rendu nettoyé, mis en cache en base), `status` ∈ {`draft`, `pending_review`, `published`, `hidden`, `archived`}, `pinned_at`, `accepted_answer` (FK Comment, pour `question`), `tags`, `last_activity_at`, `comment_count`, `reaction_counts` (dénormalisé par type), `search_vector`.
- `PostRevision` : `post`, `editor`, `title`, `body`, `created_at` (créée à chaque édition d'un post publié de type `article`/`announcement`, et pour tous les types si modifié par un modérateur).
- `Comment` : `post`, `author`, `parent` (FK nullable ; profondeur ≤ 2 vérifiée par service), `body` (≤ 10 000), `status` ∈ {`visible`, `hidden`}, `is_expert_answer` (auto si auteur expert+).
- `Reaction` : `user`, `post` ou `comment` (deux FK nullables + CHECK exactement un), `kind` ∈ {`useful`, `thanks`, `insightful`} (pas de « dislike ») ; unique par (user, cible, kind).
- `Mention` : `source` (post/comment), `mentioned_user` ; résolue à la sauvegarde depuis `@prenom.nom` (autocomplétion parmi les membres de la communauté).
- `Bookmark` : `user`, cible générique restreinte (Post, Document, Rex) via FK nullables + CHECK ; `collection` (FK `BookmarkCollection` nullable) ; unique.
- `ContentReport` : `reporter`, cible (post/comment/document), `reason` ∈ {`inappropriate`, `confidential`, `outdated`, `spam`, `other`}, `details`, `status` ∈ {`open`, `resolved`, `dismissed`}, `handled_by`, `handled_at`, `resolution_note`.

### Règles

- Publier : membre+ (communauté `active`) ; `announcement` : animateur+ ; `article` : contributeur+.
- Si `require_post_review` : statut `pending_review` jusqu'à validation modérateur+.
- Épingler : animateur+, maximum 3 posts épinglés par communauté.
- Éditer : auteur (sur ses contenus, tant que non masqués) ou modérateur+ (révision créée, auteur notifié).
- Masquer (`hidden`) : modérateur+ ; motif obligatoire ; audité ; l'auteur est notifié et voit toujours son contenu marqué « masqué ».
- Question : l'auteur ou un expert+ peut marquer une réponse acceptée.
- Signalement : tout lecteur ; un utilisateur ne peut avoir qu'un signalement ouvert par cible ; 3 signalements ouverts distincts → masquage provisoire automatique + alerte modérateurs (seuil paramétrable).
- Partage entre communautés : **lien de citation** (« publié aussi dans ») créant un post de type `discussion` qui référence l'original ; le contenu original reste soumis à ses propres droits (un lecteur sans accès voit « contenu non accessible »).
- Limites de débit : 10 posts/h, 60 commentaires/h, 120 réactions/h par utilisateur.

### Pages

Fil de communauté (épinglés en tête, filtres par type, « questions sans réponse ») ; fil personnel d'accueil (communautés suivies, tri par activité) ; détail de post (commentaires, réponses, réactions, révisions pour modérateurs) ; éditeur avec aperçu ; file de modération (signalements, contenus en revue) ; favoris.

---

## 8. Lot L5 — Documents

### Modèles

- `Document` : `community` (`PROTECT`), `title`, `description`, `doc_type` ∈ {`guide`, `template`, `presentation`, `reference`, `code_sample`, `other`}, `tags`, `owner` (auteur), `visibility` ∈ {`community`, `restricted`} (restricted : rôles minimum `min_role` pour lire), `download_min_role` (défaut `member`), `status` ∈ {`active`, `archived`, `expired`}, `expires_at` (optionnel), `review_due_at` (optionnel), `current_version` (FK), `download_count`, `search_vector`.
- `DocumentVersion` : `document`, `version_label` (auto `1`, `2`… ou libellé), `storage_key` (unique, aléatoire, sans nom d'origine), `original_filename` (nettoyé), `mime_type` (détecté), `size`, `sha256`, `scan_status` ∈ {`pending`, `clean`, `infected`, `error`}, `scanned_at`, `uploaded_by`, `change_note`, `is_reference` (index unique partiel par document).
- `DocumentLink` : liens typés vers `Post`/`Rex`/`Event` (et plus tard formations).
- `DownloadLog` : `version`, `user`, `created_at` (justification : statistiques des ressources les plus consultées et traçabilité des documents restreints ; rétention 12 mois).

### Flux de dépôt

1. Formulaire (≤ 100 Mo, paramétrable) → upload **en streaming** vers le stockage objet sous préfixe `quarantine/`.
2. Validation synchrone : extension ∈ liste blanche (pdf, docx, xlsx, pptx, odt/ods/odp, txt, md, csv, png, jpg, svg **refusé**, zip, ipynb, sql, py…), type MIME détecté sur le contenu (`python-magic`) cohérent avec l'extension, taille, nom nettoyé.
3. `DocumentVersion` créée `scan_status=pending` ; tâche Celery `scan_document_version` (retries exponentiels × 5) → `clamd` en streaming → `clean` : objet déplacé vers `documents/` ; `infected` : objet supprimé, auteur et modérateurs alertés, audit.
4. Tant que `pending`/`error`, la version n'est **pas téléchargeable** (badge « analyse en cours »).

### Téléchargement

`GET /documents/<public_id>/download/[<version>]` → `policies.can_download` → `DownloadLog` → **302 vers URL présignée 60 s** avec `ResponseContentDisposition=attachment; filename*=…` et `ResponseContentType` forcé. Prévisualisation au MVP : images et PDF uniquement, via même mécanisme avec `inline` et en-tête CSP `sandbox` côté stockage (ou proxy Nginx) ; autres formats : téléchargement uniquement.

Le bucket n'a **aucune politique publique** ; l'URL présignée contient une clé aléatoire non devinable ; une URL expirée renvoie 403 (testé contre MinIO en CI).

### Cycle de vie

Tâche quotidienne : `expires_at` dépassée → `expired` (masqué des listes, toujours accessible au propriétaire et aux animateurs) ; `review_due_at` dépassée → apparaît dans « contenus à réviser » du tableau de bord responsable ; signalement `outdated` → idem.

---

## 9. Lot L6 — REX et articles

### Modèles

- `RexTemplate` : `name`, `description`, sections ordonnées (`RexTemplateSection` : `key`, `label`, `help_text`, `required`). Modèle par défaut fourni (migration de données) avec les sections du § 8 du prompt : contexte, problématique, contraintes, approche, solutions étudiées, décisions et justifications, difficultés, résultats, enseignements, bonnes pratiques, points de vigilance, outils et technologies.
- `RexArticle` : `community`, `template`, `title`, `summary`, `author`, `contributors` (M2M), `status` ∈ {`draft`, `in_review`, `changes_requested`, `published`, `archived`}, `technologies` (M2M `Tag`), `tags`, `client_sector` (liste fermée optionnelle, **jamais le nom du client**), `published_at`, `search_vector`, `sensitivity_flags` (résultat du dernier scan, voir ci-dessous).
- `RexSection` : `article`, `key`, `body` (Markdown) ; unique (`article`, `key`).
- `RexReview` : `article`, `reviewer`, `decision` ∈ {`approve`, `request_changes`}, `comment`, `created_at`.
- `SensitiveTerm` (administrable) : `term`, `kind` ∈ {`client_name`, `project_codename`, `other`}, `is_active`.

### Règles

- Brouillon auto-sauvegardé (HTMX, toutes les 15 s si modifié, champ `version` pour éviter l'écrasement concurrent → 409 + message).
- Soumission → **scan de sensibilité** synchrone (règles locales, aucune donnée envoyée à l'extérieur) : motifs de secrets (clés AWS/Azure/GCP, jetons GitHub, JWT, chaînes de connexion, clés privées PEM), e-mails, téléphones, IBAN, termes `SensitiveTerm`. Résultat : liste de constats affichée à l'auteur avec emplacement ; secrets → **soumission bloquée** ; autres constats → soumission possible mais signalés au relecteur.
- Revue : expert+ de la communauté (pas l'auteur ni un contributeur) ; `approve` → `published` + notification aux membres (niveau `all`) ; `request_changes` → retour à l'auteur.
- Les articles techniques, tutoriels et guides du MVP sont des `Post(kind=article)` ; même scan de sensibilité appliqué à tout `Post` et `Comment` (secrets → blocage, reste → avertissement).

---

## 10. Lot L7 — Événements

### Modèles

- `Event` : `community` (nullable = événement global, créé par admin fonctionnel), `kind` ∈ {`workshop`, `webinar`, `rex_session`, `conference`, `demo`, `hackathon`, `meeting`, `certification_prep`, `training`}, `title`, `description`, `starts_at`, `ends_at` (UTC, CHECK `ends_at > starts_at`), `timezone`, `location` (texte), `online_url` (https uniquement, visible des inscrits), `capacity` (nullable = illimité, CHECK ≥ 1 si défini), `registration_opens_at`, `registration_closes_at`, `status` ∈ {`draft`, `published`, `cancelled`, `completed`}, `organizer`, `speakers` (M2M User), `resources` (via `DocumentLink`), `registered_count` (dénormalisé).
- `EventRegistration` : `event`, `user`, `status` ∈ {`registered`, `waitlisted`, `cancelled`}, `waitlist_position` (nullable), `created_at`, `cancelled_at` ; unique (`event`, `user`).

### Règles (toutes sous `transaction.atomic` + `select_for_update` sur l'`Event`)

- Inscription : événement publié, fenêtre ouverte, lecteur autorisé (membre si communauté non `open`) ; place libre → `registered` ; sinon → `waitlisted` avec position ; réinscription après annulation réutilise la ligne.
- Désinscription d'un `registered` → promotion du premier `waitlisted` (FIFO) → notification + e-mail.
- Annulation de l'événement (organisateur/animateur+) : motif obligatoire, tous les inscrits notifiés, statut `cancelled`.
- Modification de date/lieu d'un événement publié → notification aux inscrits.
- Rappels : tâche Beat toutes les 15 min, rappel J-1 et H-1, idempotent (`ReminderSent(event, user, kind)` unique).
- Export `.ics` par événement (et flux personnel « mes inscriptions » via URL à jeton révocable).
- Liste des participants : visible de l'organisateur, des animateurs+ et, en nombre seulement, des autres.

### Pages

Calendrier global (vue mois/liste, filtres communauté et type), calendrier de communauté, détail (inscription, capacité, liste d'attente, ressources), gestion (édition, participants, annulation), historique « événements passés ».

---

## 11. Lot L8 — Notifications

### Modèles

- `Notification` : `recipient`, `category` ∈ {`community_post`, `reply`, `mention`, `new_resource`, `invitation`, `membership_decision`, `event_reminder`, `event_change`, `announcement`, `moderation_alert`, `review_request`, `system`}, `actor` (nullable), `verb`, `target_type`, `target_id`, `url`, `read_at`, `created_at`, `emailed_at` ; index (`recipient`, `read_at`, `-created_at`).
- `NotificationPreference` : `user`, `category`, `in_app` (bool), `email` ∈ {`immediate`, `daily_digest`, `off`} ; unique (`user`, `category`). Catégories **essentielles** non désactivables en in-app : `invitation`, `membership_decision`, `event_change`, `moderation_alert` (pour les modérateurs), `system`.

### Fonctionnement

- Service `notify(category, recipients, actor, target)` appelé **après commit** (`transaction.on_commit`) ; insertion en masse (`bulk_create`) ; l'auteur de l'action n'est jamais notifié.
- Diffusion en masse (nouvelle publication dans une communauté de 2 000 membres) : faite par une tâche Celery par lots de 500.
- E-mails : tâche Celery (retries × 5, backoff), `immediate` regroupés par fenêtre de 5 min par destinataire ; digest quotidien à 8 h (fuseau utilisateur) ; lien de désabonnement par catégorie.
- Interface : cloche avec compteur (rafraîchi par HTMX toutes les 60 s et à chaque navigation), page de notifications (filtrable, « tout marquer lu »), page de préférences.
- Purge : notifications lues > 90 j supprimées par tâche nocturne.

---

## 12. Lot L9 — Recherche

- Chaque modèle cherchable (`Community`, `Post`, `Comment` via son post, `Document`, `RexArticle`, `Event`, `User` découvrable, `Tag`) possède un `search_vector` (`SearchVectorField`) mis à jour par le service d'écriture (pondération : titre A, tags B, corps C) avec la configuration `french` + extension `unaccent` (configuration texte personnalisée `fr_unaccent`) ; index GIN.
- Suggestions : `pg_trgm` sur titres de communautés, tags et noms d'utilisateurs découvrables (index GIN trigram), à partir de 2 caractères, 8 résultats, debounce 250 ms (HTMX).
- Page de recherche : requête `websearch_to_tsquery`, onglets par type avec compteurs, filtres (communauté, type, tag, période), tri (pertinence `ts_rank_cd` + fraîcheur, ou date), surlignage `ts_headline` sur un extrait ; pagination 20.
- **Droits** : chaque type est interrogé via `visible_to(user)` puis classé ; jamais de post-filtrage. Les contenus `hidden`, `draft`, `pending_review`, archivés et les documents non `clean` sont exclus.
- Historique de recherche : non conservé au MVP (pas de valeur prouvée, donnée personnelle évitée).

---

## 13. Lot L10 — Tableaux de bord et indicateurs

### Définitions (H11 du cadrage)

- **Actif** sur une période : `last_seen_at` ou une action enregistrée dans la période.
- **Contributeur** : a créé un post, commentaire, REX, document ou événement publié dans la période.
- Les indicateurs de communauté comptent les membres **actuels**.

### Agrégats

Table `analytics.DailyCommunityStats` (communauté, jour : membres, nouveaux membres, départs, actifs, contributeurs, posts, commentaires, téléchargements, inscriptions aux événements) et `DailyPlatformStats`, calculées par tâche nocturne idempotente (`upsert` par jour) ; table `UserDailyActivity` (user, jour) alimentée au plus une fois par jour par utilisateur (pour DAU/WAU/MAU), purgée après 13 mois.

### Tableaux

- **Collaborateur (accueil)** : mes communautés, fil récent, événements à venir (mes inscriptions puis suggestions), ressources récemment ajoutées dans mes communautés, mes contributions récentes, notifications non lues, suggestions de communautés expliquées (« partage le tag *Kubernetes* avec vos intérêts »).
- **Responsable** (animateur+) : membres et évolution 90 j (graphique), actifs/contributeurs 28 j, publications, participation aux événements, documents les plus téléchargés, demandes en attente, questions sans réponse > 48 h, contenus signalés, contenus à réviser. **Pas de classement nominatif** des membres.
- **Admin** : utilisateurs actifs (DAU/WAU/MAU), communautés par statut, tendances d'activité, volume documentaire (nombre, taille totale), signalements en attente, files Celery (longueur, échecs 24 h), dernières erreurs de scan.

Graphiques Chart.js avec alternative textuelle (tableau de données accessible sous chaque graphique).

---

## 14. Lot L11 — Durcissement et livraison du MVP

- Commande `manage.py seed_demo` (refusée si `DJANGO_ENV=production`) : 200 utilisateurs fictifs, 12 catégories, 15 communautés, contenus, documents, événements.
- Playwright : parcours P1 à P6 du cadrage sur 3 tailles d'écran.
- Locust : scénario de référence et rapport `docs/performance.md`.
- Sauvegarde : `pg_dump` planifié + réplication/versioning du bucket ; script et procédure de **restauration testée** en environnement de test (`docs/runbooks/backup-restore.md`).
- Documentation : README, installation, configuration, développement, architecture, base de données (généré + commenté), API (OpenAPI servi sur `/api/schema/` et `/api/docs/` pour utilisateurs authentifiés), guide utilisateur, guide administrateur, déploiement, diagnostic, limites connues.

---

## 15. API (MVP)

API REST DRF en lecture/écriture limitée, authentifiée par session (même origine, CSRF) ; utile aux interactions et aux intégrations futures, sans dupliquer toute l'UI :
`/api/v1/communities/`, `/communities/{slug}/members/`, `/posts/`, `/posts/{id}/comments/`, `/documents/`, `/events/`, `/events/{id}/registration/`, `/notifications/`, `/search/`. Pagination, filtres, mêmes `policies` et sélecteurs que l'UI. Limitation de débit DRF par utilisateur. CORS : aucune origine autorisée par défaut.

---

## 16. Gestion des erreurs

| Cas | Comportement |
|---|---|
| Validation de formulaire | Erreurs en ligne, résumé en tête de formulaire avec liens, focus sur le premier champ en erreur |
| `DomainError` | Toast + message explicite (ex. « Cette communauté doit garder au moins un responsable ») |
| Conflit d'édition | 409, propose de recharger en conservant le texte saisi |
| Limite de débit | 429 avec page dédiée et délai de réessai |
| Stockage objet indisponible | Dépôt refusé avec message ; téléchargement → 503 ; `/readyz` reste OK (dégradation partielle), alerte |
| Redis indisponible | Cache désactivé (repli), tâches mises en échec visible dans le tableau admin ; sessions `cached_db` continuent |
| SMTP indisponible | Retries Celery ; l'utilisateur n'est jamais bloqué |
| ClamAV indisponible | Versions restent `pending` ; alerte admin ; rien n'est publié sans analyse |
| Exception non gérée | 500 statique, `request_id` affiché pour le support, événement envoyé au collecteur d'erreurs |

---

## 17. Stratégie de tests

- **Unitaires** (services, policies, scan de sensibilité, rendu Markdown nettoyé).
- **Matrice d'autorisation** : tests paramétrés (rôle × action × état d'objet) pour chaque politique, + tests HTTP vérifiant 404/403.
- **Intégration** : PostgreSQL réel, MinIO et ClamAV en services CI (EICAR pour l'antivirus), Celery en mode `eager` sauf tests dédiés.
- **Concurrence** : inscriptions simultanées à la dernière place (threads + `TransactionTestCase`), demandes d'adhésion doublées.
- **Requêtes** : plafond de requêtes SQL sur pages listes et détail.
- **Migrations** : depuis base vide et `makemigrations --check`.
- **E2E** : Playwright P1–P6, axe-core.
- **Sécurité** : en-têtes (CSP, HSTS), cookies, CSRF, XSS (Markdown malveillant), accès direct à un objet du bucket (403), IDOR sur `public_id`.
- **Couverture** : ≥ 85 % sur `services.py` et `policies.py` (bloquant), suivie globalement.

---

## 18. Ordre d'implémentation

L0 → L1 → L2 → L3 → L4 → L5 → L6 → L7 → L8 → L9 → L10 → L11. Chaque lot est livré avec tests, migration, documentation et instructions de vérification, et laisse l'application fonctionnelle. L8 (notifications) expose `notify()` dès L3 sous forme minimale (in-app) pour éviter de revenir sur les lots précédents.
