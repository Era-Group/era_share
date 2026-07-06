# Campaign Agent — Manager Manual

Module: `era_crm_ai_agents_campaign` · Suite: Era Group CRM AI Agents

## What the agent does

Once enabled, the agent runs **once a day** (inside your configured send
window, never on a blackout day) and:

1. **Selects customers** that pass every gate: active with an email address,
   not on the suppression list, outside the per-partner cooldown, under the
   monthly frequency cap, and — while the PDPL guard is ON — holding valid
   marketing consent.
2. **Matches each customer to exactly one service** from your Service Catalog
   via the LLM. The model can only choose from the ACTIVE catalog entries you
   maintain; anything else it returns is rejected and that customer is skipped.
3. **Drafts a per-customer subject and body** in the customer's language
   (their own language if set, else your configured default), applying any
   Playbooks whose trigger tags match the customer.
4. **Routes for review**: with approval required (the default) every campaign
   waits in *Pending Approval* for one of your configured approvers. Any line
   whose match confidence is below your threshold forces review even when
   approval is switched off.
5. **Hands off to Email Marketing**: an approved campaign becomes one official
   `mailing.mailing` record plus one personalized email per customer, sent
   through Odoo's standard outgoing-mail queue. The suppression list is
   re-checked at this final moment.

Everything is **fail-closed**: the agent ships disabled; approval is required
by default; an empty approver list fails the campaign rather than sending; an
unavailable LLM transport aborts the run; hitting the daily LLM call cap halts
generation.

## The PDPL Guard toggle — read this before touching it

`Campaign Agent Settings → PDPL → PDPL Guard` (default: **ON**).

**While ON:**

- No customer is targeted without a positive **marketing-consent** check
  through the Compliance layer. If the Compliance module is not installed,
  customers are **skipped** (reason `no_consent_guard`) — absence of the
  consent guard never means "assume consent".
- **Data minimization to the LLM**: the model receives only business
  attributes (industry, segment tags, country, prior-service identifiers)
  under an opaque reference. The customer's **name, email and phone are never
  sent to the LLM**; the real name is merged into the draft locally, inside
  Odoo, after the model returns.
- PDPL-relevant decisions are written to the critical audit log.

**While OFF — an explicit operator decision:**

> **The agent sends marketing email WITHOUT verifying consent.** The consent
> check is skipped entirely — customers who never granted marketing consent,
> or who withdrew it, WILL be emailed if they pass the other gates. LLM
> data-minimization is also no longer enforced (the customer's display name
> and city are included in the prompt for personalization; email and phone are
> still never sent, as they carry no drafting value).
>
> Under the Saudi Personal Data Protection Law, sending direct marketing
> without valid consent can be a legal violation. Disabling this guard shifts
> **full legal and compliance responsibility to you, the operator**. The
> module will do exactly what you told it to. Document your lawful basis
> before switching this off, and prefer keeping it ON.

The base module's own AI Compliance Guard (PII redaction at the egress seam)
still applies according to ITS settings — this toggle governs only the
Campaign Agent's selection-time consent check, payload minimization and PDPL
audit rows.

## Settings reference (all manager-only, all fail-closed defaults)

| Setting | Default | Meaning |
|---|---|---|
| Enable Campaign Agent | OFF | Master switch; OFF = the daily run is a no-op. |
| LLM Transport | era_ai_accounts | Default: editor subscription (era_ai_accounts); **fails closed** if unavailable. `token` (native API, env-only key) remains fully selectable. |
| Require Human Approval | ON | Campaigns wait for an approver. |
| Campaign Approvers | empty | Approval required + empty list = campaigns **fail**, nothing sent. |
| PDPL Guard | ON | See above. |
| Daily partner limit | 50 | Max partners/day across all campaigns. |
| Max campaigns per day | 5 | Upper bound on campaigns built per day. |
| Per-partner cooldown | 30 days | No re-targeting inside the window. |
| Monthly frequency cap | 2 | Absolute max emails per partner per calendar month. |
| Send window | 09:00–17:00 | Asia/Riyadh by default (timezone configurable). Outside → defer. |
| Blackout days | fri,sat | Weekdays with no runs and no sends. |
| Campaign sender address | empty | From address for campaign emails; empty = company email, then the running user's. None resolvable = hand-off **fails closed**. |
| Confidence threshold | 0.7 | Below → line forced into human review. |
| LLM daily call cap | 200 | Reached → generation halts for the day. |
| Default language | Arabic | Used when the partner has no language set. |

## Operational notes

- **Suppression list** (Configuration → Suppression List): absolute never-email
  list, matched by partner and/or email/domain pattern, enforced at selection
  AND re-checked at hand-off.
- **Service Catalog / Categories / Playbooks** ground and steer the LLM — keep
  descriptions accurate; the model reads them verbatim.
- Approving a campaign outside the send window defers the actual hand-off to
  the next scheduled run inside the window.
- Costs/token consumption are governed by the base suite's Rule-14 limits and
  visible on the CRM AI Agents dashboard.
- After installing or upgrading this module, **restart the Odoo server** and
  hard-refresh the browser (the daily cron registers only after a restart).
