"""Keep a source's retry path tied to what the source actually is.

Citations were briefly pointed at laws.moj.gov.sa instead of the indexed copy;
that was reverted because the ministry's deep links do not reliably resolve.
What remains is the defect that change exposed: core decides how to retry a
failed source by looking at whether ``url`` is set, which is not the same
question as what the source *is*. Anyone pasting a URL onto a file-backed
source still hits it.
"""
from odoo import models


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
