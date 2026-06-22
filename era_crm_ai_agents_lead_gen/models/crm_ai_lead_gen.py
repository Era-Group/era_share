# -*- coding: utf-8 -*-
"""Persistence layer — turn 16.4's structured rows into res.partner records.

The engine (16.4) only RETURNS structured company/contact dicts; this model
writes them, with three guarantees:

1. **De-duplicate BEFORE create** — match an incoming row against existing
   partners on email / website-domain / commercial-registration (vat) /
   name+region. On a match the configured ``dedup_mode`` decides: ``skip`` (leave
   the existing partner untouched, create nothing) or ``update`` (fill blank
   fields on the existing partner). Either way NO duplicate is created.
2. **Tag + source-stamp every CREATED record** — the configurable tag
   (``by_lead_generator_agent``) plus ``x_lead_gen_source`` recording the exact
   provider, so externally-sourced records can be isolated or purged in one move.
   Pre-existing partners we merely matched are NOT tagged (provenance honesty).
3. **PDPL gate** — decision-makers (individuals) are created ONLY when the
   ``fetch_decision_makers`` toggle is ON, linked to their company via
   ``parent_id``.

Everything runs under the calling user (Rule 09 / 19) — partners follow standard
CRM ACLs; only the audit log uses its own approved elevation.
"""
import logging

from odoo import Command, _, fields, models

_logger = logging.getLogger(__name__)

PARAM_PREFIX = "era_crm_ai_agents_lead_gen."


class ResPartner(models.Model):
    _inherit = "res.partner"

    x_lead_gen_source = fields.Char(
        string="Lead-Gen Source",
        index=True,
        copy=False,
        help="The exact lead-generation provider this record was sourced from "
             "(e.g. 'SerpAPI (Web Search)', 'OpenCorporates'). Set only on "
             "records the Lead-Generation agent CREATED; empty for everything "
             "else, so externally-sourced records can be isolated or purged.",
    )


class CrmAiLeadGen(models.AbstractModel):
    # Pure service (no rows stored) — methods are called on env['crm.ai.lead_gen'].
    # AbstractModel => no table, no ACL needed; partner CRUD it performs runs as
    # the calling user under standard res.partner ACLs (Rule 09 / 19).
    _name = "crm.ai.lead_gen"
    _description = "CRM AI Lead-Generation Persistence"

    # ------------------------------------------------------------------
    # Per-batch creation (called by the engine waterfall, 16.6)
    # ------------------------------------------------------------------
    def create_from_rows(self, rows, kind):
        """De-dup + create the rows of ONE successful batch.

        Returns (created_partners recordset, matched_count, capped). The daily
        cap is enforced HERE, per record, as the last line of defence: even if
        the engine's pre-fetch check passed, two parallel runs could both clear
        it and then breach at creation — so we re-check before every create and
        stop (``capped=True``) the moment the day's count reaches the cap.
        """
        created = self.env["res.partner"]
        matched = 0
        for row in rows:
            if self.daily_cap_reached():
                self._audit_cap_hit(kind, len(created))
                return created, matched, True

            if kind == "company":
                partner, was_created = self._upsert_company(row)
                if was_created:
                    created |= partner
                elif partner:
                    matched += 1
                continue

            # Decision-maker: the company it belongs to may be side-created here,
            # which is a CREATED record too — it counts toward the cap and toward
            # cost attribution, so we add it to `created` and re-check the cap
            # before creating the contact itself (no 2-records-per-cap-check leak).
            company, company_created = self._resolve_company_for_contact(row)
            if company_created:
                created |= company
                if self.daily_cap_reached():
                    self._audit_cap_hit(kind, len(created))
                    return created, matched, True
            contact, was_created = self._upsert_contact(row, company)
            if was_created:
                created |= contact
            elif contact:
                matched += 1
        return created, matched, False

    # ------------------------------------------------------------------
    # Daily fetch cap (Rule 14 pattern) — counts records CREATED today
    # ------------------------------------------------------------------
    def _records_today(self):
        """How many lead-gen records have been created so far today.

        Counts res.partner stamped with x_lead_gen_source created since the
        start of the contextual day. Just-created rows in the running
        transaction are included (the count grows as the run creates), so the
        cap self-enforces within a run.
        """
        start = fields.Datetime.to_datetime(fields.Date.context_today(self))
        return self.env["res.partner"].search_count([
            ("x_lead_gen_source", "!=", False),
            ("create_date", ">=", start),
        ])

    def _daily_cap(self):
        """The configured daily cap, or None if undeterminable (fail-safe)."""
        raw = self._cfg("daily_cap", None)
        if raw in (None, False, ""):
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def daily_cap_reached(self):
        """Fail-safe daily-cap test (mirrors the Base cost cap shape).

        None (missing/unparseable) -> True (BLOCK — never fetch blind); cap <= 0
        -> False (explicit opt-out, unlimited); else over when today's created
        count is at or above the cap.
        """
        cap = self._daily_cap()
        if cap is None:
            return True
        if cap <= 0:
            return False
        return self._records_today() >= cap

    # ------------------------------------------------------------------
    # Cost (16.6, simplified) — batch-level; usage_type distinguishes the halves
    # ------------------------------------------------------------------
    def book_source_cost(self, provider):
        """Book the source-API cost of one successful batch as a single row.

        The LLM-extraction cost is booked separately by the guard inside
        ``_call_llm`` (usage_type='llm'). Together with this row (usage_type=
        'source_api') both halves of a run's spend are recorded. We deliberately
        do NOT split/relink per created record: per-record cost granularity has
        no operational use here and the splitting churned the usage table and
        needed an extra sudo elevation — batch-level is the right altitude.
        """
        self.env["crm.ai.usage"].record(
            self._agent(), False, 0, 0, provider.cost or 0.0,
            usage_type="source_api")

    def _agent(self):
        return self.env["crm.ai.lead_gen.agent"]._get_agent_record()

    def _audit_cap_hit(self, kind, created_so_far):
        agent = self._agent()
        self.env["crm.ai.audit.log"].log(
            "cost_cap_exceeded", agent, None,
            before={"daily_cap": self._daily_cap(), "kind": kind},
            after={"reason": "daily fetch cap reached",
                   "created_this_run": created_so_far})

    # ------------------------------------------------------------------
    # Company / decision-maker upserts
    # ------------------------------------------------------------------
    def _upsert_company(self, row):
        """Find-or-create a company partner from a row. Returns (partner, created)."""
        existing = self._find_duplicate(row, is_company=True)
        if existing:
            self._maybe_update(existing, self._company_vals(row))
            return existing, False
        vals = self._company_vals(row)
        vals.update(self._stamp_vals(row))
        partner = self.env["res.partner"].create(vals)
        self._audit_created(partner, row)
        return partner, True

    def _upsert_contact(self, row, company):
        """Find-or-create a decision-maker linked to ``company`` via parent_id.

        Returns (partner, created). The company is resolved by the caller
        (create_from_rows) so its creation is cap-counted, not hidden here.
        """
        existing = self._find_duplicate(row, is_company=False, parent=company)
        if existing:
            self._maybe_update(existing, self._contact_vals(row, company))
            return existing, False
        vals = self._contact_vals(row, company)
        vals.update(self._stamp_vals(row))
        contact = self.env["res.partner"].create(vals)
        self._audit_created(contact, row)
        return contact, True

    def _resolve_company_for_contact(self, row):
        """Find-or-create the company a decision-maker belongs to.

        Returns (company recordset, created_bool). Empty recordset + False when
        the row names no company.
        """
        name = (row.get("company") or "").strip()
        if not name:
            return self.env["res.partner"], False
        company_row = {"name": name, "_source_provider": row.get("_source_provider"),
                       "_source_type": row.get("_source_type")}
        return self._upsert_company(company_row)

    # ------------------------------------------------------------------
    # De-duplication (BEFORE create) — runs as the calling user
    # ------------------------------------------------------------------
    def _find_duplicate(self, row, is_company, parent=None):
        """Return an existing partner matching the row, or empty recordset.

        Match order (most to least specific): email, website-domain,
        commercial-registration (vat), then name + region (city/state). The first
        hit wins; an empty recordset means "no duplicate — safe to create".
        """
        Partner = self.env["res.partner"]
        type_domain = [("is_company", "=", is_company)]

        email = (row.get("email") or "").strip()
        if email:
            hit = Partner.search(type_domain + [("email", "=ilike", email)], limit=1)
            if hit:
                return hit

        domain = self._domain_of(row)
        if domain:
            hit = Partner.search(
                type_domain + [("website", "ilike", domain)], limit=1)
            if hit:
                return hit
            hit = Partner.search(
                type_domain + [("email", "=ilike", "%@" + domain)], limit=1)
            if hit:
                return hit

        vat = (row.get("vat") or row.get("commercial_registration") or "").strip()
        if vat:
            hit = Partner.search(type_domain + [("vat", "=", vat)], limit=1)
            if hit:
                return hit

        # Name match is the WEAKEST signal: a bare name collision (e.g. a common
        # trading name) must NOT be treated as a duplicate, or a genuinely-new
        # company would be silently dropped in skip mode. So we require a second
        # discriminator — a region (city) for companies, or the parent company
        # for a contact. Name alone never matches.
        name = (row.get("name") or "").strip()
        region = (row.get("city") or "").strip()
        if name and (region or parent):
            extra = [("city", "=ilike", region)] if region else []
            scope = [("parent_id", "=", parent.id)] if parent else []
            hit = Partner.search(
                type_domain + [("name", "=ilike", name)] + extra + scope, limit=1)
            if hit:
                return hit
        return Partner

    @staticmethod
    def _domain_of(row):
        """Best-effort registrable domain from website or email."""
        website = (row.get("website") or row.get("domain") or "").strip().lower()
        if website:
            for sep in ("://", "www."):
                if sep in website:
                    website = website.split(sep)[-1]
            return website.split("/")[0].strip()
        email = (row.get("email") or "").strip().lower()
        if "@" in email:
            return email.split("@")[-1]
        return ""

    # ------------------------------------------------------------------
    # Value builders
    # ------------------------------------------------------------------
    def _company_vals(self, row):
        vals = {
            "name": (row.get("name") or "").strip() or _("Unknown Company"),
            "is_company": True,
            "website": (row.get("website") or "").strip() or False,
            "email": (row.get("email") or "").strip() or False,
            "phone": (row.get("phone") or "").strip() or False,
            "city": (row.get("city") or "").strip() or False,
            "vat": (row.get("vat") or row.get("commercial_registration") or "").strip()
                   or False,
        }
        country = self._country_of(row.get("country"))
        if country:
            vals["country_id"] = country.id
        return {k: v for k, v in vals.items() if v is not False}

    def _contact_vals(self, row, company):
        vals = {
            "name": (row.get("name") or "").strip() or _("Unknown Contact"),
            "is_company": False,
            "function": (row.get("job_title") or "").strip() or False,
            "email": (row.get("email") or "").strip() or False,
            "phone": (row.get("phone") or "").strip() or False,
        }
        if company:
            vals["parent_id"] = company.id
        return {k: v for k, v in vals.items() if v is not False}

    def _stamp_vals(self, row):
        """Tag + source stamp applied ONLY to records we create."""
        vals = {"x_lead_gen_source": (row.get("_source_provider") or "").strip()
                or _("Lead Generation")}
        tag = self._get_tag()
        if tag:
            vals["category_id"] = [Command.link(tag.id)]
        return vals

    def _maybe_update(self, partner, vals):
        """In 'update' mode, fill only BLANK fields on the matched partner.

        Never overwrites existing data (the existing record is authoritative) and
        never adds the lead-gen tag/source to a partner we did not create.
        """
        if self._dedup_mode() != "update":
            return
        to_write = {f: v for f, v in vals.items()
                    if f in partner._fields and not partner[f]}
        if to_write:
            partner.write(to_write)

    # ------------------------------------------------------------------
    # Config helpers (read the manager settings; tag lookup)
    # ------------------------------------------------------------------
    def _cfg(self, key, default=None):
        return self.env["ir.config_parameter"].sudo().get_param(
            PARAM_PREFIX + key, default)

    def _cfg_bool(self, key):
        return str(self._cfg(key, "False")).strip().lower() in ("true", "1")

    def _dedup_mode(self):
        mode = (self._cfg("dedup_mode", "skip") or "skip").strip().lower()
        return "update" if mode == "update" else "skip"

    def _get_tag(self):
        """Resolve the lead-gen partner tag by the configured name.

        Falls back to the seeded tag record so a run never fails on tag lookup.
        Resolves by name (manager-editable) without creating tags at runtime
        (avoids needing category create rights for a salesperson).
        """
        name = (self._cfg("tag_name", "") or "").strip()
        Tag = self.env["res.partner.category"]
        if name:
            tag = Tag.search([("name", "=", name)], limit=1)
            if tag:
                return tag
        return self.env.ref(
            "era_crm_ai_agents_lead_gen.tag_by_lead_generator_agent",
            raise_if_not_found=False)

    def _country_of(self, name):
        name = (name or "").strip()
        if not name:
            return self.env["res.country"]
        Country = self.env["res.country"]
        if len(name) == 2:
            hit = Country.search([("code", "=ilike", name)], limit=1)
            if hit:
                return hit
        return Country.search([("name", "=ilike", name)], limit=1)

    # ------------------------------------------------------------------
    # Audit (Rule 20) — log every created external record
    # ------------------------------------------------------------------
    def _audit_created(self, partner, row):
        # The audit row already links to the partner via its Reference (an id,
        # not a name). For an INDIVIDUAL (decision-maker) we do NOT also write
        # their name into the immutable audit payload (PDPL — don't persist a
        # person's name where it cannot be erased); a company name is fine.
        label = partner.display_name if partner.is_company else "individual #%d" % partner.id
        self.env["crm.ai.audit.log"].log(
            "external_contact", self._agent(), partner,
            before=None,
            after={"created": label,
                   "source": row.get("_source_provider"),
                   "is_company": partner.is_company})
