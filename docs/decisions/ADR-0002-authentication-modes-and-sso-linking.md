# ADR-0002 — Authentication modes and SSO linking by immutable identifier

- Status: accepted
- Date: 2026-10-09
- Decision context: critical review of the MVP specification (`.internal/specs/2026-10-09-mvp-knowledge-design.md`, § 4)

## Context

Talan's identity provider (probably Microsoft Entra ID) is not available at project start. The MVP therefore uses local accounts with a password. SSO will be enabled later. Two risks appear at the switchover: local passwords that remain active and bypass company rules (MFA, deactivation on departure), and matching accounts by e-mail, whereas an address can be reassigned to another person.

## Decision

- An `AUTH_MODE` setting takes three values: `local` (MVP), `mixed` (transition: SSO offered, password still accepted) and `sso_only`.
- An `ExternalIdentity` model (`provider`, `subject`, unique) links an account to the IdP's immutable identifier (`oid` or `sub` claim). The e-mail is used only for the first linking of an existing account; after that only `subject` is authoritative.
- In `sso_only`, local passwords are made unusable and the local form is rejected server-side, except for a single emergency account ("break glass") protected by MFA, every login of which is audited and alerted.
- Deactivation follows the IdP: checked at every login and through a nightly synchronization.

## Rationale

- No password entry point survives the move to SSO, without losing access if the IdP goes down.
- A reassigned e-mail never gives access to the former holder's account.
- MVP development is not blocked by the absence of an IdP.

## Consequences

- The `ExternalIdentity` model and the `AUTH_MODE` setting are created in work package L1, even though only the `local` mode is used in the MVP.
- A migration command makes passwords unusable when switching to `sso_only`; it is recorded in the audit log.
- Tests cover: local form rejected in `sso_only`, linking by `subject`, creation of a `pending` account for an unknown user, only the emergency account allowed.
