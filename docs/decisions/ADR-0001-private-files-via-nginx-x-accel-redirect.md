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

## Implementation (L5, 2026-10-10)

- **Nginx → storage authentication: a presigned URL generated server side.** Provider-agnostic:
  any storage whose `url()` signs (S3, SeaweedFS, MinIO, Azure SAS) works without giving
  Nginx credentials. `documents.downloads.protected_location` asks the default storage for a
  URL valid 60 seconds and puts its path and query after the prefix:
  `X-Accel-Redirect: /_protected/<bucket>/<key>?X-Amz-...`. A storage without signed URLs
  (the in-memory test storage) falls back to `/_protected/<storage_key>`. Object keys are
  random hex (`documents/<hex>`), so no escaping issue can alter the signed path.
- **Nginx location** (`docker/nginx/default.conf`): `location /_protected/ { internal; ... }`
  with `proxy_pass ${STORAGE_ORIGIN}/;`. The file is an Nginx template: the official image
  substitutes `STORAGE_ORIGIN` at container start (envsubst on `/etc/nginx/templates/`). It
  must be the scheme and host the URL was signed for, sent as `Host` (`$proxy_host`). The
  hostname is resolved when Nginx starts (reload after a DNS change).
  - `proxy_pass_request_headers off` and `proxy_pass_request_body off`: the browser's
    cookies, `Authorization` and body never reach the storage; only `Host`, `Range` and
    `If-Range` are sent.
  - `proxy_ignore_headers X-Accel-* Expires Cache-Control Set-Cookie Vary`: the storage can
    neither redirect internally nor set caching rules or cookies.
  - **Header semantics (verified with Nginx 1.24 against stub servers).** On an
    X-Accel-Redirect, Nginx copies only `Content-Type`, `Content-Disposition`,
    `Cache-Control`, `Expires`, `Set-Cookie` and `Accept-Ranges` from Django's response; any
    other header Django set (`X-Content-Type-Options`, `Content-Security-Policy`...) is
    dropped. The same headers coming back from the storage would be added or would replace
    Django's, so they are removed with `proxy_hide_header` (`Content-Type`,
    `Content-Disposition`, `Cache-Control`, `Expires`, `Set-Cookie`, CSP and `x-amz-*`
    identifiers), and the location adds `X-Content-Type-Options: nosniff`,
    `Content-Security-Policy: sandbox; default-src 'none'` (harmless on attachments),
    `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer` itself. Django still sets the
    full header set, which is what the development fallback serves.
  - `proxy_intercept_errors` with `error_page`: a storage error (expired signature, missing
    object) becomes a bare Nginx 404 (or 502 for 5xx), so its XML body, which describes the
    signed request, is never relayed. Django's `Content-Disposition` stays on such an error
    (Nginx cannot drop it without the headers-more module).
- **Status codes**: hidden document, unknown or invisible version, infected version → 404;
  preview of a type other than PDF/PNG/JPEG/GIF/WebP → 404; visible but refused → 403. A
  manager asking for a version that is still `pending` or in `error` gets 403 (not 409) with
  a translated toast: no file is ever served before a clean scan, and 403 reuses the
  existing error page.
- **Development**: with `DEBUG` (and `DOCUMENT_DEV_STREAMING`, true by default) Django
  streams the file with a `FileResponse` and the same headers. The optional `nginx` service
  of `compose.yaml` (profile `proxy`) runs the production path locally.
- **Tests** (`src/documents/tests/test_download*.py`): permission matrix, loss of access after
  removal from a private community, `DownloadLog` and counter, headers, preview types,
  filename encoding, signed URL present only in `X-Accel-Redirect`, `/_protected/` → 404 from
  Django, and a static check that the Nginx location is `internal`. Direct access to the
  bucket is refused by SeaweedFS (no anonymous identity, see `docs/development.md`).
