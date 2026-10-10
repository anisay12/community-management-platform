# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:1105d646 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/core-concepts/sync-concepts.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->


## Full agent guide

**Read [`AGENTS.md`](AGENTS.md) first.** It is the complete guide to this repository: product,
delivery status (L0–L4 done, L5 Documents next), stack, app and URL map, architecture rules,
design system, commands, testing, security and the feature checklist.

## Build & Test

```bash
uv sync && npm ci --no-audit --no-fund
make lint          # ruff + format + missing migrations
make i18n-check    # French catalogue complete
make assets-check  # committed SCSS build is current (run `make assets` after SCSS changes)
make test          # pytest, coverage >= 85 %
make test-a11y     # Playwright + axe-core
make security      # pip-audit, bandit, gitleaks
```

Tests need `DATABASE_URL` (PostgreSQL) and `REDIS_URL`; see AGENTS.md § 6.

## Architecture Overview

Django 5.2 modular monolith in `src/` (config, core, accounts, organizations, taxonomy,
communities, posts, audit, notifications), server-rendered templates with HTMX and a
Bootstrap 5.3 / Sass design system, PostgreSQL, Redis, Celery, S3-compatible storage behind
nginx. Specification: `.internal/specs/2026-10-09-mvp-knowledge-design.md`.

## Conventions & Patterns

- Thin views → `policies.py` (`can_<action>` bools) → `selectors.py` (`visible_to(user)`)
  → `services.py` (transactional, `DomainError`, `audit.services.record`, `notify`).
- Hidden object → 404, forbidden action on a visible object → 403.
- `public_id` UUIDs in URLs, `archived_at` soft archive, `on_delete=PROTECT`.
- Menu entries through `core.navigation.register(NavItem(...))`; no placeholder pages.
- Every string translated (EN default, FR complete); reuse `components/` and `tl-` classes.
