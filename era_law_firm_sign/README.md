# Era Law Firm Electronic Signature

Provider-neutral electronic signature workflow for engagement letters and legal documents, as an optional extension of `era_law_firm`.

Signature requests freeze a SHA-256 hash of the document before dispatch, so a document altered after preparation cannot be signed silently. Callbacks are verified by HMAC over a timestamped payload within a configurable tolerance window, matched against a per-request nonce and the frozen hash, and de-duplicated by event key so a replayed callback changes nothing. Every dispatch, failure and callback is recorded as an immutable signature event.

Providers are pluggable: a mock provider for testing and an HTTP provider for real integrations.

License: LGPL-3. Maintained by Era Group (`info@era.net.sa`).
