# Era Law Firm AI Governance

Governed AI drafting, review and case summaries for Saudi law firms. This module is an optional extension of `era_law_firm` and is disabled by default at company level.

No legal content leaves the system until a provider has been explicitly approved, its processing location and retention policy recorded, and a named user has given consent on the request itself. Every request is limited to a whitelist of fields, screened against the document's information classification, redacted for personal identifiers, hashed, and written to the legal audit log. The whole layer can be switched off per company.

This module provides the governance envelope. It does not itself claim any particular model or provider.

License: LGPL-3. Maintained by Era Group (`info@era.net.sa`).

From any case, **Ask the AI** offers eighteen ready tasks — a report to the client of the proceedings to date, a brief with follow-up recommendations, hearing preparation, the missing evidence, a deadline audit, draft pleadings, a research question, document summaries, a handover note, a closing report — each assembling the request with the right agent, the right catalogue entries and the instructions already written. A fifth agent, the Case Assistant, reads the file's own logs (hearings and what happened in them, deadlines, documents, the stage history) for that work. Sensitive extras are offered by name and never ticked by a task on its own; consent, redaction and audit are unchanged. Managers add the firm's own tasks under Configuration > AI > AI Playbooks.
