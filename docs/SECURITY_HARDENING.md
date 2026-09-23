# Endpoint and onboarding hardening

The current hardening pass closes three trust-boundary gaps.

## Redirect and DNS-rebinding defenses

`HttpA2AAdapter` no longer enables automatic HTTP redirects. Each request hop is validated immediately before network I/O, only 307/308 redirects are followed, and every redirect target is validated before use. The transport then connects to one of the validated IP addresses directly while preserving the original HTTP `Host` header and TLS SNI hostname, so a second DNS lookup cannot silently rebind the connection to an unvalidated private address.

`SecurityValidator.validate_endpoint()` resolves the destination and rejects private, loopback, link-local, reserved, unspecified, or multicast addresses (except explicitly supported localhost development endpoints). Within one transport operation, non-overlapping resolution changes for the same hostname are treated as a DNS-rebinding signal.

This is application-layer SSRF hardening. Deployments should still use network egress policy/firewalls for defense in depth.

## Signed Agent Card binding

`IdentityVerifier.verify_agent_card_jws()` now requires the signed claims to contain `card_sha256` (or the legacy `sha256` field) matching the canonical unsigned Agent Card. A valid JWS without payload binding is rejected.

## Explicit registration paths

- `register_trusted(agent, reason=...)` is for agents the caller already trusts and intentionally skips untrusted onboarding validation.
- `onboard_untrusted(...)` runs security, optional signed-card identity verification, revocation, and governance policy checks before admission.
- `register(...)` remains only as a backward-compatible alias to the trusted path and records `legacy-register-api` in audit provenance.

Marketplace ingestion now routes through `onboard_untrusted()`.
