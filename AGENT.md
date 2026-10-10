# Agent guide

This file exists for tools that look for `AGENT.md`. The complete, maintained guide is
**[AGENTS.md](AGENTS.md)**: product and delivery status, stack, repository and URL maps,
architecture rules (policies / selectors / services), navigation registry, design system,
commands and CI gates, testing, i18n, security and the feature checklist.

Quick start:

```bash
uv sync && npm ci --no-audit --no-fund
make lint && make i18n-check && make assets-check && make test
```

Task tracking uses beads (`bd ready`, `bd show <id>`, `bd update <id> --claim`,
`bd close <id>`); see the beads sections at the end of AGENTS.md.
