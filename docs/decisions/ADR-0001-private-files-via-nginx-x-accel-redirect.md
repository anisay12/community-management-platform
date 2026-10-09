# ADR-0001 — Serve private files through Nginx (X-Accel-Redirect)

- Status: accepted
- Date: 2026-10-09
- Decision context: critical review of the MVP specification (`.internal/specs/2026-10-09-mvp-knowledge-design.md`, § 8)

## Context

Community documents are stored in a private object store (MinIO in development, S3 or Azure Blob in production). The platform must guarantee that no URL can bypass its permissions (§ 5 of the master prompt). The first version of the spec redirected the browser to a presigned URL valid for 60 seconds.

A presigned URL is a bearer token: during its validity period, anyone who holds it (copied into a chat tool, kept in a proxy log or in the browser history) downloads the file without any check. PDF preview is then served from the storage domain, where the application has little control over security headers.

## Decision

The application checks the access right on every request, logs the download, then returns an empty response carrying the header `X-Accel-Redirect: /_protected/<storage_key>`. Nginx serves the file from an `internal` location that relays the read to the private storage using service credentials. The application sets `Content-Disposition`, `Content-Type`, `X-Content-Type-Options: nosniff`, `Cache-Control: private, no-store` and, for preview, `Content-Security-Policy: sandbox`.

## Rationale

- No storage URL is ever sent to the browser: the exposure window of a leaked link disappears.
- Every download is re-authorized: a member removed from a community loses access immediately.
- Security headers are controlled by the application, on its own domain.
- Cost: file traffic goes through Nginx. At the estimated volume (about 250 GB over 3 years, files ≤ 100 MB), this is negligible.

## Consequences

- Nginx becomes mandatory in front of the application in every environment other than local development; in development, a streaming `FileResponse` fallback is active only if `DEBUG`.
- The Nginx configuration must be able to authenticate to the storage (service credentials, or a signed URL generated server-side and never returned to the client); the implementation depends on the chosen provider (decision D1).
- A public CDN cannot be used for private documents.
- Mandatory tests: direct access to the bucket denied, `/_protected/` unreachable from outside, loss of access after removal from the community.
