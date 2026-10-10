# Lot L4 — Posts and interactions (bilingual EN/FR) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use beads-superpowers:subagent-driven-development (parallel batch mode) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Members publish discussions, questions, announcements and articles in their communities; readers comment on two levels, react, mention colleagues, bookmark and report; facilitators pin; moderators review, hide (audited, with a reason) and work a moderation queue; every reader gets a community feed and a personal home feed, and the visibility rules of framing § 4.4 hold for every post (a non-member gets 404 on a private post).

**Architecture:** New Django app `posts` (models, `selectors.py` with `Post.objects.visible_to(user)`, `policies.py`, services split by area in `services_posts.py` / `services_interactions.py`, Celery tasks for deferred counters and broadcasts, thin views split by area, templates on the L2 design system). The community page gets a **Feed** tab through the `communities/tabs.py` registry (registered from `PostsConfig.ready()`, no edit to `tabs.py`). Markdown goes through the existing `core.markdown.render`; tags reuse `taxonomy.Tag`; notifications through the existing `notifications.services.notify`; audit through `audit.services.record(..., community=...)`.

**Tech Stack:** Django 5.2 LTS, Python 3.13, PostgreSQL 16 (`SearchVectorField` + GIN, `CheckConstraint`, partial unique indexes), Redis cache (rate limits, counter locks), Celery (eager in tests), `markdown-it-py` + `nh3` (from L3), HTMX (from L2), Bootstrap 5.3 components, pytest + pytest-django, Playwright/axe (`a11y` suite).

**Source spec:** `.internal/specs/2026-10-09-mvp-knowledge-design.md` § 7 "Work package L4 — Posts and interactions", § 2 (conventions), § 11 (notification categories), § 16 (errors), § 17 (tests); framing § 4.4 (permission matrix) and the L4 row (acceptance: "Hiding by a moderator is audited; a non-member gets 404 on a private post").

## Global Constraints

- Everything in the L3 plan's global constraints still applies: English code and docs, bilingual UI with a complete French catalogue, 404 for a post the viewer may not see and 403 for a forbidden action on a visible post, no modifying GET, CSRF through `X-CSRFToken` for HTMX, `make lint && make test` (≥ 85 % on `services*.py` and `policies.py`) `&& make i18n-check && make assets-check`, `make test-a11y` for UI tasks, commit trailers `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_011UFaooKLLSUGTLxQZfHT7p`.
- Every list and detail page has a SQL query ceiling test (`django_assert_max_num_queries`).
- Views never touch models for writes; every write is a service; every read decision is a policy.
- No HTML from users: bodies are stored as Markdown and rendered with `core.markdown.render` into `body_html` at write time.
- Parallel tasks each edit `src/locale/fr/LC_MESSAGES/django.po`; the orchestrator merges catalogues with `msgcat` and re-runs `makemessages`. Each task leaves its own catalogue complete. **No other file is edited by two tasks of the same wave.**
- Tasks in Waves 2 and 3 that depend on a service of a parallel task code against the contract below; tests that need the other task's code are marked `@pytest.mark.after_integration` (marker added in Task 1) and the orchestrator runs them after merging the wave.

## Waves

| Wave | Tasks (parallel inside a wave) |
|---|---|
| 1 | Task 1 — Foundation: models, migrations, visibility, policies, shared helpers and empty stubs |
| 2 | Task 2 — Post services · Task 3 — Interaction and report services, counters · Task 4 — Feeds and post detail (read side) |
| 3 | Task 5 — Editor and post actions · Task 6 — Comments, reactions, bookmarks, reports UI · Task 7 — Moderation queue and tag administration |
| 4 | Task 8 — Integration: access matrix, accessibility, docs |

## Decisions taken in this plan (defaults where the spec is silent)

1. **App.** One new app `posts` owns Post, PostRevision, Comment, Reaction, Mention, Bookmark, BookmarkCollection and ContentReport.
2. **Tags.** `taxonomy.Tag` (already used by communities) is the spec's `Tag`. Task 1 adds `key` (lowercase, accent-free via `unicodedata` NFKD, unique) with a data migration that backfills it; the migration fails with the list of colliding names if two existing tags normalise to the same key (none expected outside development). `get_or_create_tag` looks up by key. Merge is a functional-admin action (Task 7) that moves posts and communities to the kept tag and deletes the other.
3. **Bookmark and report targets.** Documents (L5) and REX (L6) do not exist yet. L4 creates `Bookmark.post` and `ContentReport.post`/`ContentReport.comment` only, with the CHECK written as "exactly one of the target FKs"; L5 and L6 add their FK and widen the CHECK in their own migration.
4. **Articles.** The `article` kind ships in L4 with the plain post editor (contributor+). The REX/article review workflow, templates and secret detection belong to L6, which will extend it.
5. **Feed tab.** Registered as `Tab(key="feed", label=_("Feed"), url_name="posts:feed", order=5, is_visible=communities.policies.can_view_content)`, so it is the first tab. The community `detail` URL stays the About page (no change to L3 behaviour or tests).
6. **Home feed.** A new page `/feed/` (navigation entry "Feed", icon `newspaper`, order after Communities) lists published posts of the communities the user is a member of, by `last_activity_at`. "Followed communities" means membership (all notification levels). The site home page is untouched (L10 personalises it).
7. **Visibility of non-published posts.** `draft`: author only. `pending_review`: author and community moderators+ (and functional admins with content access). `hidden`: author (marked "Hidden" with the reason) and moderators+; everyone else 404. `archived`: readable by direct link by anyone who sees the content, read-only, excluded from feeds.
8. **Who moderates.** Community moderator+ or functional admin who can view the content (open community, or a valid "Access as administrator" grant, framing footnote 1). Pin and announcement: animator+ or functional admin (matrix). Review validation: moderator+.
9. **Writes need membership.** Publishing, commenting, reacting and editing one's own content require a current membership in an `active` community; suspended or archived communities answer `read_only`. Reporting and bookmarking need only read access (spec: "any reader").
   - **Decision 9 clarification.** Moderation works whatever the community status: moderators+ (and functional admins who can read the content) resolve or dismiss reports, hide and unhide, and send pending posts back to draft (reject) in active, suspended and archived communities (`can_moderate` and `can_review` check the moderator rights only). Approving a pending post publishes it, so it needs an `active` community (`approve_review` answers `read_only` otherwise, and the post page and moderation queue offer only "Send back" there). Pinning, tag creation, editing posts and accepting answers still need an `active` community, and authors may delete their own drafts only while the community is `active`.
10. **Comment depth.** Two levels: a reply to a reply is attached to the top-level comment (the form says "Reply" everywhere; the service flattens it). Accepted answers are top-level visible comments only.
11. **Expert answer.** `is_expert_answer` is computed once, at creation, from the author's community role (expert+); it does not change if the role changes later.
12. **Reactions.** Toggle per (user, target, kind); a user may not react to their own content (`own_content`). Reactions on hidden or archived targets are refused (`invalid_state`).
13. **Revisions.** Created by the service *before* an edit on a published `article`/`announcement`, and on any edit by someone other than the author. Visible to moderators+ and functional admins only. No revision for comments.
14. **Edit conflicts.** `Post.version` (positive integer) is sent as a hidden field; a mismatch raises `edit_conflict`, which the view answers with **409** re-displaying the form with the submitted text and a "Reload the latest version" link (§ 16). Comments have no version (last write wins).
15. **Mentions.** Handle = `slugify(unidecode-like NFKD(first_name)) + "." + same(last_name)`, lowercase. A mention resolves only when exactly one current member of the community has that handle and that member can view the content; unresolved mentions stay plain text. Rendering keeps `@first.last` as text in a `<span class="mention">` (no profile link in L4). Autocomplete endpoint returns up to 8 members matching a prefix, members only.
16. **Notification categories** (codes from § 11, stored in the existing `Notification.category`): `community_post` (new published discussion/question), `announcement` (published announcement or article), `reply` (comment on your post, reply to your comment), `mention`, `review_request` (post awaiting review → moderators+), `moderation_alert` (report threshold reached, new report → moderators+), `system` (your post was hidden / edited by a moderator / review decided / accepted answer). Recipients of `community_post` and `announcement` are filtered by `notification_level` (`all` gets both, `highlights` gets `announcement` only, `none` nothing); the broadcast runs in `posts.tasks.broadcast_post` (on commit, batches of 500). Replies and mentions ignore the level (spec § 6).
17. **Rate limits.** Fixed one-hour windows in the Redis cache keyed `rl:{bucket}:{user_id}:{hour}`; limits from settings `POSTS_RATE_LIMITS = {"post": 10, "comment": 60, "reaction": 120}`. Exceeding raises `DomainError("rate_limited")` carrying `retry_after` seconds; views answer the existing 429 page with `Retry-After`. If Redis is unavailable the check is skipped (fail open, logged), per § 16 "cache disabled".
18. **Report threshold.** Setting `POSTS_REPORT_AUTOHIDE_THRESHOLD = 3` distinct open reports → provisional hiding (`hidden_by=None`, `hidden_reason="automatic"`), audit `post.auto_hidden`/`comment.auto_hidden`, `moderation_alert` to moderators+. Dismissing all reports of an auto-hidden target restores it. Authors cannot report their own content.
19. **Deferred counters.** `comment_count` and `reaction_counts` (JSON `{kind: n}`) are recomputed by `posts.tasks.recount_target(model, pk)` scheduled on commit with a 3-second countdown and deduplicated by `cache.add("recount:{model}:{pk}", 1, 10)`. Nightly `posts.tasks.verify_counters` at 04:00 fixes drift. `last_activity_at` (post and community) is updated in the business transaction.
20. **Cross-community share.** `share_post` creates a `discussion` in the target community with `shared_from` (FK `SET_NULL`), a title the actor supplies (required; the share form prefills the original's title, so copying it is the actor's explicit choice) and an optional comment as body; the original's title and body are never copied into the new post or its search vector. It requires `can_create_post` in the target and `can_view_post` on the original, and goes through the target's review like any new post. The card shows "Also published in …"; a reader who cannot see the original sees "Content not accessible".
21. **Deletion.** No hard delete in the UI: authors may delete only their own **draft**; published content is hidden or archived.
22. **Author anonymisation.** `author_display` is frozen to the full name at creation; an anonymizer registered with `accounts.privacy.register_anonymizer` replaces it with "Former employee" (FR « Ancien collaborateur ») on posts and comments of the anonymised user.
23. **Idempotency keys.** Not used for posts, comments or reactions (§ 2 does not list them); double submits are bounded by the rate limit and the submit button is disabled after the first click.
24. **Pagination.** Feeds: cursor (`?cursor=` opaque base64 of `last_activity_at|id`), 20 per page, pinned posts (max 3) only on the first page of the unfiltered community feed. Bookmarks, moderation queue and tag admin: numbered, 20 per page.
25. **Search vector.** Post `search_vector` refreshed by the services with the same `simple` config as communities: title A, tag names B, body C. Search UI is L9.

## Shared contracts (every task uses these names verbatim)

### Models (`posts/models.py`, all with `created_at`/`updated_at` UTC; `public_id` UUID on Post, Comment, ContentReport, BookmarkCollection)

- `Post(public_id, community FK communities.Community PROTECT related "posts", author FK user SET_NULL null, author_display CharField(150), kind ∈ Post.Kind{DISCUSSION="discussion", QUESTION="question", ANNOUNCEMENT="announcement", ARTICLE="article"}, title ≤200, body TextField (validator max 50_000), body_html TextField, status ∈ Post.Status{DRAFT, PENDING_REVIEW="pending_review", PUBLISHED, HIDDEN, ARCHIVED}, published_at null, pinned_at null, pinned_by FK null SET_NULL, accepted_answer FK "Comment" null SET_NULL related "+", tags M2M taxonomy.Tag blank related "posts", shared_from FK self null SET_NULL related "shares", last_activity_at, comment_count PositiveInteger 0, reaction_counts JSONField default dict, version PositiveInteger default 1, hidden_at null, hidden_by FK null SET_NULL, hidden_reason TextField blank, status_before_hidden CharField blank, review_note TextField blank, archived_at null, search_vector SearchVectorField null)`. Indexes: (`community`, `status`, `-last_activity_at`), (`community`, `pinned_at`) partial where `pinned_at` not null, (`author`, `-created_at`), GIN `search_vector`. Constraint `post_accepted_answer_only_question` (CHECK `accepted_answer IS NULL OR kind = 'question'`).
- `PostRevision(post FK CASCADE related "revisions", editor FK null SET_NULL, title, body, created_at)`; ordering `-created_at`.
- `Comment(public_id, post FK PROTECT related "comments", author FK SET_NULL null, author_display, parent FK self null PROTECT related "replies", body (max 10_000), body_html, status ∈ Comment.Status{VISIBLE, HIDDEN}, is_expert_answer bool, hidden_at, hidden_by, hidden_reason, reaction_counts JSON)`; index (`post`, `parent`, `created_at`).
- `Reaction(user FK CASCADE, post FK null CASCADE, comment FK null CASCADE, kind ∈ Reaction.Kind{USEFUL, THANKS, INSIGHTFUL}, created_at)`; CHECK `reaction_exactly_one_target`; partial unique (`user`,`post`,`kind`) where post not null (`reaction_unique_post`) and (`user`,`comment`,`kind`) where comment not null (`reaction_unique_comment`).
- `Mention(post FK null CASCADE, comment FK null CASCADE, mentioned_user FK CASCADE, created_at)`; CHECK `mention_exactly_one_source`; partial uniques per source + user.
- `BookmarkCollection(public_id, user FK CASCADE, name ≤80)`; unique (`user`, `Lower(name)`).
- `Bookmark(user FK CASCADE, post FK null CASCADE, collection FK BookmarkCollection null SET_NULL, created_at)`; CHECK `bookmark_exactly_one_target` (only `post` in L4); partial unique (`user`, `post`).
- `ContentReport(public_id, reporter FK SET_NULL null, post FK null PROTECT, comment FK null PROTECT, community FK PROTECT (denormalised for the queue), reason ∈ ContentReport.Reason{INAPPROPRIATE, CONFIDENTIAL, OUTDATED, SPAM, OTHER}, details ≤2000, status ∈ ContentReport.Status{OPEN, RESOLVED, DISMISSED}, handled_by null, handled_at null, resolution_note blank)`; CHECK `report_exactly_one_target`; partial unique (`reporter`,`post`) and (`reporter`,`comment`) where status = open (`one_open_report_per_target_*`).
- `taxonomy.Tag` gains `key CharField(64, unique)`; `taxonomy.services.normalize_tag_key(name) -> str`.

### Selectors (`posts/selectors.py`)

- `PostQuerySet.visible_to(user)` (Task 1): published or archived posts of communities where `communities.policies.can_view_content` holds, expressed in SQL (member communities ∪ open non-archived communities for viewers without the technical-admin/auditor exclusion ∪ communities with a valid admin grant), plus the user's own drafts/pending/hidden posts, plus pending/hidden posts of communities where the user is moderator+. Never filters by community metadata alone.
- `get_visible_post_or_404(user, community_slug, public_id) -> Post` (Task 1).
- Task 4 adds `community_feed(user, community, *, kind=None, unanswered=False, cursor=None) -> FeedPage`, `home_feed(user, *, cursor=None) -> FeedPage`, `comment_thread(user, post, *, moderator=None) -> list[Comment]` (top-level with `prefetched_replies`, hidden comments only for their author and moderators; `moderator` passes an already computed `is_content_moderator`), `FeedPage(items, next_cursor, pinned)`.

### Policies (`posts/policies.py`, `-> bool`, never raise; Task 1)

`can_view_post(user, post)`, `can_create_post(user, community, kind)`, `can_edit_post(user, post)`, `can_delete_draft(user, post)`, `can_moderate(user, community)`, `can_pin(user, community)`, `can_review(user, community)`, `can_view_revisions(user, post)`, `can_accept_answer(user, post)` (published question; its author, expert+ or moderator+ — `may_decide_answers(user, post)` is the same rule without the kind/status check), `can_comment(user, post)`, `can_edit_comment(user, comment)`, `can_react(user, target)`, `can_bookmark(user, post)`, `can_report(user, target)`, `can_share(user, post, target_community)`, `can_create_tag(user, community)` (contributor+ or functional admin), `can_merge_tags(user)` (functional admin).

### Services (`@transaction.atomic`, keyword-only, actor first, enforce policies themselves → `DomainError("forbidden")`)

- `posts/services_posts.py` (Task 2): `create_post(*, actor, community, kind, title, body, tags=(), publish=True) -> Post` (status `draft` if not publish, else `pending_review` when `community.require_post_review` and the actor is below moderator, else `published`; rate limit `post`), `update_post(*, actor, post, title, body, tags, version) -> Post` (`edit_conflict`; revision rule; moderator edit audited `post.edited_by_moderator` + `system` notification to author), `publish_draft(*, actor, post)`, `delete_draft(*, actor, post)`, `approve_review(*, actor, post)` / `reject_review(*, actor, post, note)` (`post.review_approved`/`post.review_rejected`; reject → draft with note), `pin_post` / `unpin_post` (`pin_limit` beyond 3; `post.pinned`/`post.unpinned`), `hide_post(*, actor, post, reason)` / `unhide_post(*, actor, post)` (reason required → `reason_required`; `post.hidden`/`post.unhidden`), `archive_post` (`post.archived`), `accept_answer(*, actor, post, comment)` / `clear_accepted_answer` (question author, expert+ or moderator+; `forbidden` is checked before the state), `share_post(*, actor, post, target_community, title, comment="") -> Post` (`title` is required and chosen by the actor — the UI may prefill it with the original's title; nothing of the original is copied into the new post or its search vector; the target's review rule applies), `set_tags(*, actor, post, names, version=None)` (unknown tag by member below contributor → `tag_creation_forbidden`; bumps `version`, `edit_conflict` when a given `version` is stale).
- `posts/services_interactions.py` (Task 3): `add_comment(*, actor, post, body, parent=None) -> Comment` (rate limit `comment`; depth flattening; `reply` notifications; mentions), `update_comment(*, actor, comment, body)`, `hide_comment(*, actor, comment, reason)` / `unhide_comment` (`comment.hidden`/`comment.unhidden`), `set_reaction(*, actor, target, kind, present: bool) -> bool` (idempotent: the wanted state, returned; rate limit `reaction`), `set_bookmark(*, actor, post, present: bool, collection=None) -> bool` (idempotent; removal needs no read access), `create_collection` / `rename_collection` / `delete_collection` / `move_bookmark`, `ensure_can_report(*, actor, target)` (the form-independent refusals of `report`; the report form is only offered when none applies), `report(*, actor, target, reason, details="") -> ContentReport` (`already_reported`, `own_content`; threshold), `resolve_report(*, actor, report, note="", hide=False)` / `dismiss_report(*, actor, report, note="")` (`report.resolved`/`report.dismissed`).
- `posts/hiding.py` (Task 1): `mark_hidden(target, *, actor, reason, automatic=False)` and `mark_visible(target)` — the shared low-level status change + audit used by Tasks 2 and 3; `is_auto_hidden(target)` and `confirm_hidden(target, *, actor, reason)` (a moderator confirms an automatic hiding: `hidden_by`, reason, audit `*.hidden`).
- `posts/mentions.py` (Task 1): `handle_for(user) -> str`, `extract_handles(text) -> set[str]`, `resolve_mentions(community, text) -> list[User]`, `sync_mentions(source, users) -> list[User]` (returns newly mentioned users).
- `posts/ratelimit.py` (Task 1): `hit(user, bucket: str) -> None` (raises `DomainError("rate_limited")` with attribute `retry_after`).
- `posts/rendering.py` (Task 1): `render_body(text) -> str` (= `core.markdown.render` + mention spans).
- `posts/tasks.py` (Task 1 stubs that return immediately; Task 3 implements): `recount_target(model_label, pk)`, `schedule_recount(obj)`, `verify_counters()`, `broadcast_post(post_id, category)`.
- `taxonomy/services.py` (Task 7): `merge_tags(*, actor, source, target) -> Tag` (audit `tag.merged`).

### DomainError codes (new in L4; L3 codes still valid)

`forbidden`, `read_only`, `not_member`, `invalid_state`, `edit_conflict` (409), `rate_limited` (429), `pin_limit`, `reason_required`, `depth_exceeded` (only if a parent of another post is given), `own_content`, `already_reported`, `tag_creation_forbidden`, `kind_forbidden`, `body_too_long`, `body_required` (empty comment), `invalid_reason` (unknown report reason), `name_required` / `name_too_long` (bookmark collection name; `name_taken` for a duplicate), `title_required` / `title_too_long` (post or share title).

### Audit actions

`post.hidden`, `post.unhidden`, `post.auto_hidden`, `post.archived`, `post.pinned`, `post.unpinned`, `post.review_approved`, `post.review_rejected`, `post.edited_by_moderator`, `comment.hidden`, `comment.unhidden`, `comment.auto_hidden`, `report.resolved`, `report.dismissed`, `tag.merged` — all with `community=` set (except `tag.merged`), hide actions with `changes={"reason": ...}`.

### URL names (namespace `posts`; `posts/urls.py` mounted at `""` in `config/urls.py` and including the stub files below)

- `posts/urls.py` (Task 1 creates, Task 4 fills read routes): `feed` `communities/<slug>/posts/`, `detail` `communities/<slug>/posts/<uuid:public_id>/`, `revisions` `…/<uuid>/revisions/`, `home_feed` `feed/`.
- `posts/urls_editor.py` (Task 5): `create` `communities/<slug>/posts/new/`, `edit` `…/<uuid>/edit/`, `preview` (POST, HTMX) `communities/<slug>/posts/preview/`, `publish`, `delete_draft`, `pin`, `unpin`, `hide`, `unhide`, `archive`, `accept_answer`, `review_decide`, `share` (all POST under `…/<uuid>/<action>/`), `mention_suggestions` `communities/<slug>/posts/mentions/` (GET, HTMX).
- `posts/urls_interactions.py` (Task 6): `comment_create` (POST `…/<uuid>/comments/`), `comment_edit` `comments/<uuid>/edit/`, `comment_hide`, `comment_unhide`, `react` (POST `react/<str:target>/<uuid>/` with `target` ∈ post/comment), `bookmark_toggle` (POST `…/<uuid>/bookmark/`, field `present` = `1`/`0`; `react` also takes `present`, 400 without it), `report` (GET form + POST `report/<str:target>/<uuid>/`), `bookmarks` `bookmarks/`, `collection_*`.
- `posts/urls_moderation.py` (Task 7): `moderation_queue` `communities/<slug>/moderation/`, `report_decide` (POST `reports/<uuid>/decide/`). Tag admin in namespace `manage`: `tag_list` `/manage/tags/`, `tag_merge` (POST).

### Templates and partials (Task 1 creates each stub with a `{% comment %}` naming the owning task)

`posts/_post_actions.html` (Task 5: edit/pin/hide/archive/share buttons, clear the accepted answer; "Accept this answer" is a button per eligible comment in `_comment_actions.html`), `posts/_feed_create_button.html` (Task 5), `posts/_post_interactions.html` (Task 6: reaction bar, bookmark, report), `posts/_comment_actions.html` (Task 6), `posts/_comment_form.html` (Task 6), `posts/_moderation_link.html` (Task 7). Components: `components/post_card.html` (Task 4), `components/reaction_bar.html` (Task 6).

---

### Task 1: Foundation — models, migrations, visibility, policies, helpers, stubs (Wave 1)

**Files:** create `src/posts/{__init__,apps,models,selectors,policies,hiding,mentions,ratelimit,rendering,tasks,admin,services_posts,services_interactions}.py` (both service files with the module docstring and contract signatures raising `NotImplementedError`), `src/posts/{urls,urls_editor,urls_interactions,urls_moderation}.py` (`urlpatterns = []` except `app_name = "posts"` and the includes in `urls.py`), `src/posts/views_errors.py` (`domain_error_response(request, error, *, redirect_to)` mapping `rate_limited` → 429 with `Retry-After`, `edit_conflict` → 409, other codes → error toast + redirect), migrations, the stub partials above, `src/posts/tests/{conftest,test_models,test_visibility,test_policies,test_mentions,test_ratelimit,test_hiding}.py`; modify `src/taxonomy/models.py` + migration (key + backfill) + `services.py` (`normalize_tag_key`, lookup by key), `src/config/settings/base.py` (INSTALLED_APPS, `POSTS_RATE_LIMITS`, `POSTS_REPORT_AUTOHIDE_THRESHOLD`, `POSTS_PIN_LIMIT = 3`), `src/config/urls.py` (mount `posts.urls`), `pyproject.toml` (`after_integration` marker), French catalogue. `PostsConfig.ready()` registers the Feed tab, the navigation entries "Feed" (`posts:home_feed`) and "Bookmarks" (`posts:bookmarks`, user menu order) — hidden until the URL resolves — and the anonymizer.

**Interfaces:** everything under "Shared contracts" that is attributed to Task 1; fixtures in `posts/tests/conftest.py`: `make_post(community, author, **extra)`, `make_comment(post, author, parent=None, **extra)` (ORM only, rendering `body_html`), reusing `make_community`/`add_member` (move them to `src/conftest.py` if needed, without changing their behaviour).

**Acceptance Criteria:**
- All models, constraints and indexes exist; `makemigrations --check` clean; tag key backfill reversible.
- Constraint tests: two reactions same (user, target, kind) → `IntegrityError`; reaction/mention/bookmark/report with zero or two targets → `IntegrityError`; two open reports by the same user on the same target → `IntegrityError`; `accepted_answer` on a non-question → `IntegrityError`.
- Visibility matrix for `visible_to` + `can_view_post` + `get_visible_post_or_404`: community modes open/request/invite × {anonymous, non-member, member, moderator, functional admin without/with/expired grant, technical admin, auditor, suspended employee} × post status draft/pending/published/hidden/archived × author/non-author. A non-member of a private community gets nothing.
- Policy matrix parameterised by role × action × community status (active/suspended/archived) for every policy listed.
- Mentions: handle normalisation (accents, hyphens, case), ambiguity → unresolved, non-member → unresolved. Rate limit: 11th post in the hour refused with `retry_after > 0`; Redis down → allowed.
- `mark_hidden`/`mark_visible` store and restore `status_before_hidden`, record the audit event inside the transaction.
- The community page shows the Feed tab only to viewers with content access once `posts:feed` resolves (tab test uses a temporary URLconf or is marked `after_integration`).
- `make lint && make test && make i18n-check && make assets-check` pass.

**Steps:**
- [ ] Write constraint and visibility tests (failing), then models + migrations.
- [ ] Policies with the parameterised matrix tests.
- [ ] `hiding.py`, `mentions.py`, `ratelimit.py`, `rendering.py`, `views_errors.py` with unit tests.
- [ ] Stubs (services, tasks, URLs, partials), app config registrations, settings, URL mount.
- [ ] Tag key migration; `makemessages`, translate; run gates.
- [ ] Commit `feat(posts): post models, visibility rules, policies and shared helpers`.

---

### Task 2: Post services (Wave 2, parallel with Tasks 3 and 4)

**Files:** fill `src/posts/services_posts.py`; create `src/posts/tests/test_services_posts.py`; French catalogue (error messages, notification-free).

**Interfaces:** `services_posts.*` as in the contract; uses `hiding`, `mentions`, `ratelimit`, `rendering`, `tasks.broadcast_post` (call through `transaction.on_commit(lambda: broadcast_post.delay(...))`; the stub is a no-op until Task 3).

**Acceptance Criteria:**
- Kind rules: member → discussion/question; contributor+ → article; animator+/functional admin → announcement (`kind_forbidden`); non-member → `not_member`; suspended/archived community → `read_only`.
- Review: with `require_post_review`, a member's post is `pending_review`, moderators+ are notified (`review_request`), approve publishes (and broadcasts), reject returns to draft with the note; a moderator's own post is published directly.
- Edit: author while not hidden; moderator+ any; revision rules (Decision 13); `edit_conflict` on stale version; `version` incremented; `body_html`, `search_vector`, mentions updated; author notified on moderator edit.
- Pin: animator+, 4th pin → `pin_limit`, unpin frees a slot; hide/unhide require moderator+, reason mandatory, audited, author notified, author still reads it; archive audited.
- Accept answer: question author or expert+; comment must be a visible top-level comment of the same post; replacing an accepted answer works.
- Share: requires view on the original and publish rights in the target; creates a discussion with `shared_from`.
- `last_activity_at` of the post and the community updated on publish; tags: contributor+ may create, members only pick existing.
- Coverage ≥ 85 % on the file. `make lint && make test && make i18n-check` pass.
- Commit `feat(posts): post lifecycle, review, pinning, hiding and sharing services`.

---

### Task 3: Interaction and report services, counters, broadcast (Wave 2, parallel with Tasks 2 and 4)

**Files:** fill `src/posts/services_interactions.py` and `src/posts/tasks.py`; modify `src/config/settings/base.py` (**only** the `CELERY_BEAT_SCHEDULE` entry `posts-verify-counters` at 04:00); create `src/posts/tests/{test_services_interactions,test_reports,test_tasks,test_concurrency}.py`; French catalogue.

**Acceptance Criteria:**
- Comments: commenter must be a member of an active community and the post published (not archived/hidden); reply to a reply attaches to the top-level parent; `is_expert_answer` set for expert+; `reply` notification to the post author (top-level) or parent author (reply), never to the actor; mentions notified (`mention`) once per newly mentioned user; edit by author or moderator+; hide/unhide audited with reason.
- Reactions: toggle on/off, three kinds independent, own content refused, hidden/archived target refused, 121st reaction in the hour → `rate_limited`.
- Bookmarks: toggle, collections CRUD per user (unique name case-insensitive), moving a bookmark; only posts the user can view.
- Reports: one open report per user per target (`already_reported`), own content refused; at the threshold of distinct open reports the target is provisionally hidden (audited `*.auto_hidden`) and moderators+ receive `moderation_alert`; resolve (optionally hiding) and dismiss audited; dismissing every open report of an auto-hidden target restores it.
- Counters: `recount_target` sets exact `comment_count` (visible comments) and `reaction_counts`; scheduling twice within the lock window enqueues once; `verify_counters` fixes deliberately corrupted counters.
- `broadcast_post`: recipients filtered by `notification_level` per Decision 16, batches of 500 (test with a batch size override), author excluded.
- Concurrency (`transaction=True`, threads): two simultaneous identical reactions → one row and no 500; two simultaneous reports from different users reaching the threshold → hidden once.
- `make lint && make test && make i18n-check` pass. Commit `feat(posts): comments, reactions, bookmarks, reports and deferred counters`.

---

### Task 4: Feeds and post detail — read side (Wave 2, parallel with Tasks 2 and 3)

**Files:** create `src/posts/views.py` (feed, home_feed, detail, revisions), add feed/thread selectors to `src/posts/selectors.py`, fill read routes in `src/posts/urls.py`, templates `src/posts/templates/posts/{feed,home_feed,detail,revisions,_comment,_feed_filters}.html`, `src/core/templates/components/post_card.html`; tests `src/posts/tests/{test_selectors_feed,test_views_read}.py`; French catalogue. Tests create rows through the ORM fixtures only (no services).

**Requirements:** community feed inside the community page layout (`communities/_header.html` + tabs with `current="feed"`): pinned posts first (badge "Pinned"), filters by kind (links with `aria-current`), "Unanswered questions" (questions without accepted answer and with zero visible comments), cursor "Load more" (HTMX `hx-get` with a plain link fallback), empty state per filter, `_feed_create_button.html` included. Home feed: posts of member communities with the community name on each card, empty state pointing to the catalogue. Post card: kind badge, title, author display, community, relative date with `<time datetime>` in the user's time zone, tag chips, comment count, reaction counts, "Also published in" / "Content not accessible" for shares, "Hidden" or "Pending review" badge for the author/moderators. Detail: breadcrumb, `body_html`, accepted answer highlighted at the top for questions, expert-answer badge, comment thread (two levels, hidden comments shown as "Comment hidden by a moderator" except to their author and moderators), includes the Task 5/6 partials; revisions page (moderators+, diff-free list with title and rendered body per revision). 404 when `can_view_post` is false (also for a wrong community slug).

**Acceptance Criteria:** HTTP tests: non-member gets 404 on a private post and on its feed (both request and invite modes); open community feed readable by any employee but not by the technical admin/auditor (403 on the tab URL like the members tab); filters, unanswered, cursor stability (no duplicates or gaps when two posts share `last_activity_at`); pinned only on page 1; drafts never in feeds; query ceiling on feed (≤ 15 queries for 20 posts) and detail (≤ 20 with 30 comments); one `h1` per page; French rendering. `make lint && make test && make i18n-check` pass. Commit `feat(posts): community feed, home feed and post detail`.

---

### Task 5: Editor and post actions (Wave 3, parallel with Tasks 6 and 7)

**Files:** create `src/posts/{views_editor,forms}.py`, templates `posts/{editor,share,hide_confirm,review_reject}.html`, `src/core/static/core/js/mention-autocomplete.js` only if HTMX alone is insufficient (prefer an HTMX `hx-get` suggestion list with a `role="listbox"` and keyboard support); fill `posts/urls_editor.py`, `posts/_post_actions.html`, `posts/_feed_create_button.html`; tests `src/posts/tests/test_views_editor.py`; French catalogue.

**Requirements:** editor with kind select limited to the kinds `can_create_post` allows, title, Markdown body with help and character counter, tag input (existing tags suggested; free entry for contributor+), "Preview" (HTMX POST rendering `render_body` into a live region; no-JS fallback re-renders the page with the preview), "Save draft" / "Publish" (or "Submit for review" when review applies), hidden `version`; 409 conflict page keeping the entered text with a link to reload; POST-only actions with confirmation modals and no-JS fallbacks (hide and reject need a reason); PRG with success toast; `DomainError` mapped through `views_errors.domain_error_response`; mention suggestions endpoint (members only, 8 results, 404 when the community is not visible).

**Acceptance Criteria:** HTTP tests for create per kind and role (incl. `kind_forbidden` → 403 toast, non-member → join prompt), review submission, edit by author/moderator/other (403), stale version → 409 with the text preserved, 11th post → 429 with `Retry-After`, every action button only rendered for allowed users, GET on action URLs → 405, invisible community → 404, XSS payload in body and title rendered inert. Commit `feat(posts): post editor, preview and moderation actions`.

---

### Task 6: Comments, reactions, bookmarks and reports UI (Wave 3, parallel with Tasks 5 and 7)

**Files:** create `src/posts/{views_interactions,forms_interactions}.py`, templates `posts/{bookmarks,report_form,_reaction_bar_inner,_bookmark_button}.html`, `src/core/templates/components/reaction_bar.html`; fill `posts/urls_interactions.py`, `posts/_post_interactions.html`, `posts/_comment_actions.html`, `posts/_comment_form.html`; tests `src/posts/tests/test_views_interactions.py`; French catalogue.

**Requirements:** comment form (Markdown, 10,000 chars) under the post and inline "Reply" on each comment; edit own comment inline; hide/unhide for moderators with reason; reaction bar (three toggle buttons with `aria-pressed`, counts, HTMX swap of the bar, POST form fallback; counts shown optimistic from the user's own toggle since counters are deferred); bookmark toggle (`aria-pressed`) and a bookmarks page with collections (create/rename/delete, filter by collection, numbered pagination, entries the user can no longer see shown as "Content not accessible" with a remove button); report form (reason radio group + details) reachable by any reader, success message "Thank you, moderators have been informed".

**Acceptance Criteria:** HTTP and HTMX tests for every action (HTMX request returns the partial, plain request redirects), own-content reaction refused, 61st comment → 429, duplicate report → error toast, non-member of open community can report and bookmark but not comment or react (join prompt), invisible post → 404, query ceiling on bookmarks page. Commit `feat(posts): comments, reactions, bookmarks and reporting UI`.

---

### Task 7: Moderation queue and tag administration (Wave 3, parallel with Tasks 5 and 6)

**Files:** create `src/posts/{views_moderation,forms_moderation}.py`, templates `posts/moderation_queue.html`, `src/taxonomy/templates/manage/{tag_list,tag_merge_confirm}.html`, `src/taxonomy/{views,urls_admin}.py`; fill `posts/urls_moderation.py` and `posts/_moderation_link.html` (link in the community header area for moderators+, with open-items count); add `merge_tags` to `src/taxonomy/services.py`; add the tag pattern list to `accounts.urls.manage_patterns` (one import + one concatenation, as in L3) and a "Tags" entry to `manage/_tabs.html` for functional admins; tests `src/posts/tests/test_views_moderation.py`, `src/taxonomy/tests/test_merge.py`; French catalogue.

**Requirements:** per-community queue (moderator+ or functional admin with content access; otherwise 404 if not visible, 403 if visible): tabs "Reports" (open first, grouped by target with count and reasons, actions resolve-and-hide / resolve / dismiss with note) and "Awaiting review" (approve / reject with note, using Task 2's services), auto-hidden items flagged; numbered pagination. Tag list for functional admins: name, key, usage counts, search; merge form (source → target, confirmation) moving post and community links without duplicates, audited.

**Acceptance Criteria:** HTTP tests by role (member 403, moderator, animator, functional admin with/without grant), decisions audited and notified, merge keeps M2M rows unique and deletes the source, auditor/employee get 404 on `/manage/tags/`, query ceiling on the queue. Commit `feat(posts): moderation queue and tag administration`.

---

### Task 8: Integration — acceptance matrix, accessibility, docs (Wave 4, after Tasks 5–7)

**Files:** run all `after_integration` tests and fix integration issues; create `src/posts/tests/test_acceptance_matrix.py`; extend `src/core/tests/a11y/test_axe.py` (and `test_keyboard.py` for the reaction bar and mention suggestions) with community feed (member, non-member of an open community), home feed, post detail with comments, editor with preview, bookmarks, report form, moderation queue, tag list (light/dark × desktop/mobile); update `README.md` status and `docs/development.md` (new "Posts and interactions" section: statuses and visibility, roles per action, review, hiding and reports threshold, rate limits and settings, deferred counters and the nightly job in "Scheduled jobs", notification categories and levels, mentions handle format; one line under "Communities → Tabs" for the Feed tab).

**Acceptance Criteria:** matrix test over framing § 4.4 rows touched by L4 — view content (open/private), publish a post / ask a question, moderate (hide, archive), pin / publish an announcement — for every column (non-member, member, contributor, expert, moderator, facilitator, lead, manager, functional admin with and without grant, technical admin, auditor), at the HTTP level; "hiding by a moderator is audited" and "a non-member gets 404 on a private post" asserted as named tests; `make test-a11y` zero serious/critical; all gates pass. Commit `test(posts): acceptance matrix, accessibility checks and docs`.
