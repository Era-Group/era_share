"""Cite the statute where it actually lives.

Odoo's RAG answers end each claim with a link to its source. Core builds that
link as ``source.url or /web/content/<attachment>``, so with the URL empty
every citation in a legal answer pointed at the copy indexed inside Odoo — a
lawyer following a citation landed on our attachment rather than on the article
at laws.moj.gov.sa. For legal work the authority is the ministry's page, not
our copy of it.

The corpus sync already records the official URL on ``moj.law``; this carries
it onto the sources so core's own preference does the rest.
"""
from odoo import _, api, fields, models


class AIAgentSource(models.Model):
    _inherit = "ai.agent.source"

    def action_retry_failed_source(self):
        """Choose the retry path by source type, not by whether a URL exists.

        Core infers "this is a scraped page" from ``self.url`` being set. Once a
        file-backed statute also carries its official URL that inference is
        wrong: the retry would trigger the scraping cron, which skips anything
        whose type is not ``url``, and the source would sit in ``processing``
        with nothing to explain it.
        """
        self.ensure_one()
        if not (self.status == "failed" and self.type == "binary" and self.url):
            return super().action_retry_failed_source()

        chunks = self.env["ai.embedding"].search([
            ("checksum", "=", self.attachment_id.checksum),
            ("embedding_model", "=", self.agent_id._get_embedding_model()),
        ])
        chunks.unlink()
        self.write({"status": "processing", "is_active": False, "error_details": False})
        self.env.ref("ai.ir_cron_generate_embedding")._trigger()


class MojLaw(models.Model):
    _inherit = "moj.law"

    def _apply_official_url_to_sources(self):
        """Point every attached source at the ministry's page for that statute."""
        touched = self.env["ai.agent.source"]
        for law in self:
            if not law.source_url:
                continue
            stale = law.source_ids.filtered(lambda s: s.url != law.source_url)
            if stale:
                stale.write({"url": law.source_url})
                touched |= stale
        return touched
